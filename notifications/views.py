import json
import re
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from admin_panel.authz import permission_required_403
from journal_links.descriptor_fill_report import (
    REPORT_JOB_TYPE,
    REPORT_STATUSES,
    build_teacher_groups,
    rows_from_job_run,
)
from school_years.models import Teacher

from jobs.models import JobLog, JobRun
from jobs.services import log_step

from pipeline.audit import log_criterion_event
from pipeline.models import CriterionEntry
from .models import TeacherConfirmation, TeacherContact
from .recheck import run_teacher_recheck_job
from .reminder_mutes import current_module, muted_teacher_ids, resolve_teacher, set_muted
from .services import TelegramSendError, send_telegram


CONFIRMATION_REGEX = re.compile(
    r"^\s*(?P<keyword>исправил(?:а)?|готово|done|fixed)"
    r"(?:[\s#:]+(?P<job_id>[0-9a-fA-F-]{36}))?\s*[!.]*\s*$",
    re.IGNORECASE,
)


DEFAULT_RECHECK_MAX_CYCLES = 3


def _get_recheck_max_cycles() -> int:
    configured = getattr(settings, "TEACHER_RECHECK_MAX_CYCLES", DEFAULT_RECHECK_MAX_CYCLES)
    try:
        value = int(configured)
    except (TypeError, ValueError):
        return DEFAULT_RECHECK_MAX_CYCLES
    return max(1, value)


def _detect_next_recheck_cycle(job_run: JobRun, *, teacher_name: str, chat_id: str) -> int:
    prior_cycles = JobLog.objects.filter(
        job_run=job_run,
        message="Teacher recheck triggered",
        context_json__teacher=teacher_name,
        context_json__chat_id=chat_id,
    ).count()
    return prior_cycles + 1


def _send_recheck_result_feedback(contact: TeacherContact, *, source_job_run: JobRun, recheck_job: JobRun, cycle: int) -> None:
    summary = ((recheck_job.result_json or {}).get("summary") or {})
    still_invalid = int(summary.get("still_invalid") or 0)
    failed = int(summary.get("failed") or 0)
    became_valid = int(summary.get("became_valid") or 0)
    checked = int(summary.get("checked") or 0)

    if still_invalid > 0 or failed > 0:
        text = (
            "Проверка обновлений завершена. Остались невалидные критерии.\n"
            f"Цикл: {cycle}.\n"
            f"Проверено: {checked}, исправлено: {became_valid}, осталось: {still_invalid}, ошибок AI: {failed}.\n"
            "Исправьте критерии и отправьте подтверждение снова."
        )
    else:
        text = (
            "Отлично, всё ок — невалидных критериев больше нет.\n"
            f"Цикл: {cycle}.\n"
            f"Проверено: {checked}, исправлено: {became_valid}."
        )

    send_telegram(contact.chat_id, text)
    log_step(
        job_run=source_job_run,
        level=JobLog.Level.INFO,
        message="Teacher recheck feedback sent",
        context={
            "teacher": contact.name,
            "chat_id": str(contact.chat_id),
            "recheck_job_id": str(recheck_job.id),
            "cycle": cycle,
            "still_invalid": still_invalid,
            "failed": failed,
        },
    )

def _find_last_reminder_job(contact: TeacherContact) -> JobRun | None:
    latest_log = (
        JobLog.objects.filter(
            message__startswith="Reminder sent to ",
            context_json__teacher=contact.name,
        )
        .select_related("job_run")
        .order_by("-ts")
        .first()
    )
    if latest_log:
        return latest_log.job_run

    latest_chat_log = (
        JobLog.objects.filter(
            message__startswith="Reminder sent to ",
            context_json__chat_id=str(contact.chat_id),
        )
        .select_related("job_run")
        .order_by("-ts")
        .first()
    )
    return latest_chat_log.job_run if latest_chat_log else None


def _extract_confirmation_job(explicit_job_id: str | None, contact: TeacherContact) -> tuple[JobRun | None, str | None]:
    if explicit_job_id:
        job_run = JobRun.objects.filter(id=explicit_job_id).first()
        return job_run, explicit_job_id

    return _find_last_reminder_job(contact), None


def _handle_teacher_confirmation(contact: TeacherContact, text: str) -> str:
    matched = CONFIRMATION_REGEX.match(text)
    if not matched:
        maybe_job = _find_last_reminder_job(contact)
        if maybe_job:
            log_step(
                job_run=maybe_job,
                level=JobLog.Level.INFO,
                message="Teacher message ignored",
                context={
                    "teacher": contact.name,
                    "chat_id": str(contact.chat_id),
                    "message_text": text,
                    "reason": "not_confirmation_keyword",
                },
            )
        return "ignored"

    job_run, explicit_job_id = _extract_confirmation_job(matched.group("job_id"), contact)
    if not job_run:
        return "ignored"

    now = timezone.now()
    confirmation, created = TeacherConfirmation.objects.get_or_create(
        job_run=job_run,
        chat_id=str(contact.chat_id),
        defaults={
            "teacher_name": contact.name,
            "status": TeacherConfirmation.Status.CONFIRMED,
            "message_text": text,
            "confirmed_at": now,
        },
    )

    if not created:
        confirmation.teacher_name = contact.name
        confirmation.message_text = text
        confirmation.confirmed_at = now
        confirmation.status = TeacherConfirmation.Status.CONFIRMED
        confirmation.save(update_fields=["teacher_name", "message_text", "confirmed_at", "status"])

    log_step(
        job_run=job_run,
        level=JobLog.Level.INFO,
        message="Teacher confirmation received",
        context={
            "teacher": contact.name,
            "chat_id": str(contact.chat_id),
            "status": TeacherConfirmation.Status.CONFIRMED,
            "is_repeat": not created,
            "message_text": text,
            "explicit_job_id": explicit_job_id or "",
        },
    )
    criteria = CriterionEntry.objects.filter(teacher_name=contact.name, needs_recheck=True)
    for criterion in criteria:
        log_criterion_event(
            criterion,
            event_type="teacher_confirmed",
            actor_name=contact.name,
            actor_role="teacher",
            reason=text,
            payload={"job_run_id": str(job_run.id), "chat_id": str(contact.chat_id)},
        )
    cycle = _detect_next_recheck_cycle(job_run, teacher_name=contact.name, chat_id=str(contact.chat_id))
    max_cycles = _get_recheck_max_cycles()
    if cycle > max_cycles:
        log_step(
            job_run=job_run,
            level=JobLog.Level.WARNING,
            message="Teacher recheck skipped: max cycles reached",
            context={
                "teacher": contact.name,
                "chat_id": str(contact.chat_id),
                "cycle": cycle,
                "max_cycles": max_cycles,
            },
        )
        try:
            send_telegram(
                str(contact.chat_id),
                (
                    "Достигнут лимит повторных проверок.\n"
                    f"Текущий цикл: {cycle}, лимит: {max_cycles}.\n"
                    "Свяжитесь с администратором для ручного разбора."
                ),
            )
        except TelegramSendError:
            pass
        return "repeat" if not created else "confirmed"
    recheck_job = run_teacher_recheck_job(source_job_run=job_run, teacher_name=contact.name)
    log_step(
        job_run=job_run,
        level=JobLog.Level.INFO,
        message="Teacher recheck triggered",
        context={
            "teacher": contact.name,
            "chat_id": str(contact.chat_id),
            "recheck_job_id": str(recheck_job.id),
            "recheck_status": recheck_job.status,
            "cycle": cycle,
            "max_cycles": max_cycles,
        },
    )
    try:
        _send_recheck_result_feedback(contact, source_job_run=job_run, recheck_job=recheck_job, cycle=cycle)
    except TelegramSendError:
        log_step(
            job_run=job_run,
            level=JobLog.Level.WARNING,
            message="Teacher recheck feedback send failed",
            context={
                "teacher": contact.name,
                "chat_id": str(contact.chat_id),
                "recheck_job_id": str(recheck_job.id),
                "cycle": cycle,
            },
        )
    return "repeat" if not created else "confirmed"

@csrf_exempt
def telegram_webhook(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST required"}, status=405)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)

    message = payload.get("message") or {}
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    if not chat_id:
        return JsonResponse({"ok": True, "status": "ignored"})

    if text.startswith("/start"):
        # Supported format from deep-link: /start register_<token>
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].startswith("register_"):
            try:
                send_telegram(
                    str(chat_id),
                    "Введите имя и фамилию как в системе (пример: Иван Иванов), чтобы привязать Telegram.",
                )
            except TelegramSendError:
                pass
            return JsonResponse({"ok": True, "status": "awaiting_name"})
        token = parts[1].replace("register_", "", 1).strip()
        if not token:
            return JsonResponse({"ok": True, "status": "ignored"})

        contact = TeacherContact.objects.filter(registration_token=token).first()
        if not contact:
            try:
                send_telegram(str(chat_id), "Ссылка регистрации недействительна. Обратитесь к администратору.")
            except TelegramSendError:
                pass
            return JsonResponse({"ok": True, "status": "token_not_found"})

        contact.chat_id = str(chat_id)
        contact.is_active = True
        contact.last_seen_at = timezone.now()
        contact.registration_token = None
        contact.save(update_fields=["chat_id", "is_active", "last_seen_at", "registration_token"])

        try:
            send_telegram(contact.chat_id, f"Регистрация успешна. Контакт привязан: {contact.name}.")
        except TelegramSendError:
            # Even if confirmation failed, binding is successful
            pass
        return JsonResponse({"ok": True, "status": "registered", "teacher": contact.name})

    contact = TeacherContact.objects.filter(chat_id=str(chat_id)).first()
    if not contact:
        typed_name = " ".join(text.split())
        if not typed_name:
            return JsonResponse({"ok": True, "status": "ignored"})

        candidate = TeacherContact.objects.filter(name__iexact=typed_name).first()
        if not candidate:
            try:
                send_telegram(
                    str(chat_id),
                    "Учитель с таким именем не найден. Обратитесь к администратору.",
                )
            except TelegramSendError:
                pass
            return JsonResponse({"ok": True, "status": "teacher_not_found"})

        if candidate.chat_id and candidate.chat_id != str(chat_id):
            try:
                send_telegram(
                    str(chat_id),
                    "Этот учитель уже привязан к другому чату. Обратитесь к администратору.",
                )
            except TelegramSendError:
                pass
            return JsonResponse({"ok": True, "status": "already_linked"})

        candidate.chat_id = str(chat_id)
        candidate.is_active = True
        candidate.last_seen_at = timezone.now()
        candidate.registration_token = None
        candidate.save(update_fields=["chat_id", "is_active", "last_seen_at", "registration_token"])
        try:
            send_telegram(candidate.chat_id, f"Регистрация успешна. Контакт привязан: {candidate.name}.")
        except TelegramSendError:
            pass
        return JsonResponse({"ok": True, "status": "registered_by_name", "teacher": candidate.name})


    contact.last_seen_at = timezone.now()
    contact.save(update_fields=["last_seen_at"])
    status = _handle_teacher_confirmation(contact, text)
    return JsonResponse({"ok": True, "status": status})



@login_required
@permission_required_403("jobs.view_jobrun", message="Доступ запрещён: нет прав на просмотр напоминаний.")
def reminder_list(request):
    """Who would be reminded in this module, and the tick that stops it."""

    module = current_module()
    job_run = (
        JobRun.objects.filter(job_type=REPORT_JOB_TYPE, status__in=REPORT_STATUSES)
        .order_by("-started_at")
        .first()
    )
    groups = build_teacher_groups(rows_from_job_run(job_run)) if job_run else []
    muted = muted_teacher_ids(module) if module else set()

    rows = []
    for group in groups:
        teacher = (
            resolve_teacher(group["teacher_name"], school_year=module.school_year)
            if module
            else None
        )
        contact = TeacherContact.objects.filter(name__iexact=group["teacher_name"]).first()
        rows.append(
            {
                **group,
                "teacher_id": teacher.pk if teacher else None,
                "known": teacher is not None,
                "muted": bool(teacher and teacher.pk in muted),
                "has_contact": bool(contact and contact.is_active and contact.chat_id),
            }
        )

    return render(
        request,
        "notifications/reminder_list.html",
        {
            "module": module,
            "job_run": job_run,
            "rows": rows,
            "can_edit": request.user.has_perm("jobs.send_reminders"),
            "muted_total": sum(1 for row in rows if row["muted"]),
            "unknown_total": sum(1 for row in rows if not row["known"]),
            "no_contact_total": sum(1 for row in rows if row["known"] and not row["has_contact"]),
        },
    )


@login_required
@permission_required_403("jobs.send_reminders", message="Доступ запрещён: нельзя менять напоминания.")
@require_POST
def toggle_reminder_mute(request):
    module = current_module()
    if module is None:
        messages.error(request, "Сначала заведите модуль на экране «Журналы модуля».")
        return redirect("notifications:reminder_list")

    teacher = get_object_or_404(Teacher, pk=request.POST.get("teacher_id"))
    muted = request.POST.get("muted") == "1"
    set_muted(module=module, teacher=teacher, muted=muted, user=request.user)

    if muted:
        messages.success(
            request,
            f"{teacher.display_name} не будет получать напоминания в модуле {module.get_number_display()}.",
        )
    else:
        messages.success(request, f"{teacher.display_name} снова получает напоминания.")
    return redirect("notifications:reminder_list")
