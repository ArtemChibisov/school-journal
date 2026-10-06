import csv
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from admin_panel.authz import permission_required_403
from jobs.models import JobRun
from journal_links.models import ClassSheetLink
from pipeline.ai_criteria_review import JOB_TYPE as AI_CRITERIA_REVIEW_JOB_TYPE, enqueue_ai_criteria_class_review_job
from pipeline.audit import log_criterion_event
from pipeline.forms import (
    AICriteriaReportTargetForm,
    ParentContactForm,
    ParentContactsImportForm,
    ModulePdfReportsForm,
    ParentLetterForm,
    StudentReviewReportsForm,
    ValidCriterionTemplateForm,
)
from pipeline.models import AICriteriaReportTarget, CriterionEntry, CriterionReviewEvent, ParentContact, ValidCriterionTemplate
from pipeline.mail_status import mail_status
from pipeline.parent_letter import (
    PLACEHOLDERS,
    letter_for,
    sample_letter,
    sample_module,
    stored_template,
)
from pipeline.parent_contacts import import_parent_contacts_csv
from school_years.imports.parent_contact_sync import import_parent_contacts
from pipeline.student_review_reports import JOB_TYPE as STUDENT_REVIEW_REPORTS_JOB_TYPE, enqueue_prepare_student_review_reports_job
from pipeline.module_review_reports import (
    JOB_TYPE as MODULE_REVIEW_REPORTS_JOB_TYPE,
    enqueue_module_review_reports_job,
)
from pipeline.module_pdf_reports import (
    JOB_TYPE as MODULE_PDF_REPORTS_JOB_TYPE,
    enqueue_module_pdf_reports_job,
)
from pipeline.module_pdf_scan import (
    JOB_TYPE as MODULE_PDF_SCAN_JOB_TYPE,
    enqueue_module_pdf_scan_job,
)


def _parse_non_empty(value: str | None) -> str | None:
    if value is None:
        return None

    trimmed = value.strip()
    return trimmed or None


def _is_admin_role(user) -> bool:
    return bool(user.is_superuser or user.groups.filter(name="admin").exists())


def _latest_ai_criteria_review_run() -> JobRun | None:
    return (
        JobRun.objects.filter(job_type=AI_CRITERIA_REVIEW_JOB_TYPE)
        .order_by("-started_at", "-id")
        .first()
    )


def _latest_student_review_reports_run() -> JobRun | None:
    """The last run this screen started, of any of the three kinds it can start.

    Looking only at the per-class type left the screen showing a run from before modules
    existed, while the module run the завуч had just started was nowhere on the page.
    """

    return (
        JobRun.objects.filter(
            job_type__in=[
                MODULE_REVIEW_REPORTS_JOB_TYPE,
                MODULE_PDF_REPORTS_JOB_TYPE,
                STUDENT_REVIEW_REPORTS_JOB_TYPE,
            ]
        )
        .order_by("-started_at", "-id")
        .first()
    )


def _ai_criteria_rows(job_run: JobRun | None) -> list[dict]:
    if job_run is None or not isinstance(job_run.result_json, dict):
        return []
    rows = job_run.result_json.get("rows", [])
    return [row for row in rows if isinstance(row, dict)]


def _ai_criteria_filter_options(rows: list[dict]) -> dict[str, list[str]]:
    return {
        "classes": sorted({str(row.get("class_code") or "") for row in rows if row.get("class_code")}),
        "teachers": sorted({str(row.get("teacher_name") or "") for row in rows if row.get("teacher_name")}),
        "statuses": sorted({str(row.get("ai_verdict") or "") for row in rows if row.get("ai_verdict")}),
    }


def _filter_ai_criteria_rows(rows: list[dict], request) -> list[dict]:
    class_code = _parse_non_empty(request.GET.get("class_code"))
    teacher_name = _parse_non_empty(request.GET.get("teacher_name"))
    ai_verdict = _parse_non_empty(request.GET.get("ai_verdict"))

    filtered = rows
    if class_code:
        filtered = [row for row in filtered if str(row.get("class_code", "")) == class_code]
    if teacher_name:
        filtered = [row for row in filtered if str(row.get("teacher_name", "")) == teacher_name]
    if ai_verdict:
        filtered = [row for row in filtered if str(row.get("ai_verdict", "")) == ai_verdict]
    return filtered


@login_required
@permission_required_403("pipeline.view_criterionentry", message="Доступ запрещён: нет прав на просмотр таблицы критериев.")
def criteria_table(request):
    queryset = CriterionEntry.objects.all().order_by(
        "class_code", "teacher_name", "subject_name", "module_number", "criterion_text"
    )

    class_code = _parse_non_empty(request.GET.get("class_code"))
    if class_code:
        queryset = queryset.filter(class_code=class_code)

    teacher_name = _parse_non_empty(request.GET.get("teacher_name"))
    if teacher_name:
        queryset = queryset.filter(teacher_name=teacher_name)

    subject_name = _parse_non_empty(request.GET.get("subject_name"))
    if subject_name:
        queryset = queryset.filter(subject_name=subject_name)

    module_number = _parse_non_empty(request.GET.get("module_number"))
    if module_number:
        queryset = queryset.filter(module_number=module_number)
    validation_status = _parse_non_empty(request.GET.get("validation_status"))
    if validation_status:
        queryset = queryset.filter(validation_status=validation_status)
    paginator = Paginator(queryset, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    preserved_query = request.GET.copy()
    preserved_query.pop("page", None)

    return render(
        request,
        "pipeline/criteria_table.html",
        {
            "page_obj": page_obj,
            "class_choices": CriterionEntry.objects.values_list("class_code", flat=True).distinct().order_by("class_code"),
            "teacher_choices": CriterionEntry.objects.values_list("teacher_name", flat=True)
            .distinct()
            .order_by("teacher_name"),
            "subject_choices": CriterionEntry.objects.values_list("subject_name", flat=True)
            .distinct()
            .order_by("subject_name"),
            "module_choices": CriterionEntry.objects.values_list("module_number", flat=True)
            .distinct()
            .order_by("module_number"),
            "status_choices": CriterionEntry.ValidationStatus.choices,
            "filters": {
                "class_code": class_code or "",
                "teacher_name": teacher_name or "",
                "subject_name": subject_name or "",
                "module_number": module_number or "",
                "validation_status": validation_status or "",
            },
            "querystring": preserved_query.urlencode(),
        },
    )


@login_required
@permission_required_403("pipeline.view_criterionentry", message="Доступ запрещён: нет прав на просмотр AI-вычитки критериев.")
def ai_criteria_review(request):
    active_class_codes = list(
        ClassSheetLink.objects.filter(is_active=True)
        .values_list("class_code", flat=True)
        .distinct()
        .order_by("class_code")
    )

    if request.method == "POST":
        if not request.user.has_perm("jobs.run_full_pipeline"):
            return HttpResponse("forbidden", status=403)
        class_code = _parse_non_empty(request.POST.get("class_code"))
        job_run = enqueue_ai_criteria_class_review_job(
            class_code=class_code,
            all_active=class_code is None,
            initiated_by=request.user if request.user.is_authenticated else None,
        )
        messages.success(request, "AI-вычитка критериев запущена.")
        return redirect(f"{reverse('pipeline:ai_criteria_review')}?run_id={job_run.id}")

    requested_run_id = _parse_non_empty(request.GET.get("run_id"))
    if requested_run_id:
        job_run = get_object_or_404(JobRun, id=requested_run_id, job_type=AI_CRITERIA_REVIEW_JOB_TYPE)
    else:
        job_run = _latest_ai_criteria_review_run()

    rows = _ai_criteria_rows(job_run)
    summary = {}
    if job_run and isinstance(job_run.result_json, dict):
        possible_summary = job_run.result_json.get("summary", {})
        if isinstance(possible_summary, dict):
            summary = possible_summary

    return render(
        request,
        "pipeline/ai_criteria_review.html",
        {
            "class_options": active_class_codes,
            "filter_options": _ai_criteria_filter_options(rows),
            "filters": {
                "class_code": request.GET.get("class_code", ""),
                "teacher_name": request.GET.get("teacher_name", ""),
                "ai_verdict": request.GET.get("ai_verdict", ""),
            },
            "job_run": job_run,
            "summary": summary,
            "rows": _filter_ai_criteria_rows(rows, request),
        },
    )


@login_required
@permission_required_403(
    "pipeline.change_criterionentry",
    message="Доступ запрещён: нельзя изменять Google AI-отчет критериев.",
)
def ai_criteria_report_target(request):
    target = AICriteriaReportTarget.objects.order_by("-is_active", "-updated_at", "-id").first()

    if request.method == "POST":
        form = AICriteriaReportTargetForm(request.POST, instance=target)
        if form.is_valid():
            target = form.save()
            messages.success(request, "Google AI-отчет критериев сохранен.")
            return redirect("pipeline:ai_criteria_report_target")
    else:
        form = AICriteriaReportTargetForm(instance=target)

    return render(
        request,
        "pipeline/ai_criteria_report_target.html",
        {"form": form, "target": target},
    )


@login_required
@permission_required_403("jobs.view_jobrun", message="Доступ запрещён: нет прав на просмотр отправки.")
def parent_mail_settings(request):
    """The mailbox, the letter and an example of it - on one screen.

    Reading the env file on the server answers a different question than whether the settings
    reached Django, so the application is asked instead and a test letter settles the rest.

    The example is rendered on every visit, from whatever the template currently is. Editing
    the wording of a letter that four hundred families will read, without seeing the result,
    is how a stray placeholder reaches all of them.
    """

    from pipeline.models import ParentLetterTemplate

    instance = ParentLetterTemplate.objects.order_by("-updated_at").first()
    if request.method == "POST":
        if not request.user.has_perm("jobs.run_full_pipeline"):
            return HttpResponse("forbidden", status=403)
        form = ParentLetterForm(request.POST, instance=instance)
        if form.is_valid():
            letter = form.save(commit=False)
            letter.updated_by = request.user if request.user.is_authenticated else None
            letter.save()
            messages.success(request, "Текст письма сохранён.")
            return redirect(reverse("pipeline:parent_mail_settings"))
    else:
        subject, body = stored_template()
        form = ParentLetterForm(instance=instance, initial={"subject": subject, "body": body})

    # The example follows what is in the form, so a draft that has not been saved is still
    # shown the way it would go out.
    preview_template = None
    if form.is_bound and not form.errors:
        preview_template = (form.cleaned_data["subject"], form.cleaned_data["body"])
    sample_subject, sample_body = sample_letter(preview_template)

    return render(
        request,
        "pipeline/parent_mail_settings.html",
        {
            "mail": mail_status(),
            "test_to": request.GET.get("to", ""),
            "form": form,
            "placeholders": PLACEHOLDERS,
            "sample_subject": sample_subject,
            "sample_body": sample_body,
            "sample_module": sample_module(),
        },
    )


@login_required
@permission_required_403("jobs.run_full_pipeline", message="Доступ запрещён: нет прав на отправку.")
@require_POST
def send_test_letter(request):
    """One letter to one address, with nobody's report attached."""

    from django.core.mail import EmailMessage

    from school_years.models import SchoolModule, SchoolYear

    target = reverse("pipeline:parent_mail_settings")
    address = (request.POST.get("to") or "").strip()
    if not address:
        messages.error(request, "Укажите адрес, на который отправить проверочное письмо.")
        return redirect(target)

    year = SchoolYear.objects.filter(is_active=True).first() or SchoolYear.objects.first()
    module = (
        SchoolModule.objects.filter(school_year=year).order_by("number").first() if year else None
    )
    if module is None:
        messages.error(request, "Сначала заведите учебный год и модуль — из них берётся текст письма.")
        return redirect(target)

    subject, body = letter_for(student_name="Мария Иванова", module=module, class_code="4А")
    try:
        EmailMessage(
            subject=f"[Проверка] {subject}",
            body=(
                "Это проверочное письмо, отправленное из панели.\n"
                "Ниже — текст, который получат родители.\n\n"
                f"{body}"
            ),
            to=[address],
        ).send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f"Отправить не удалось: {exc}")
        return redirect(f"{target}?to={address}")

    messages.success(request, f"Письмо отправлено на {address}. Проверьте ящик, в том числе «Спам».")
    return redirect(f"{target}?to={address}")


@login_required
@permission_required_403("jobs.view_jobrun", message="Доступ запрещён: нет прав на просмотр сохранения в PDF.")
def module_pdf_reports(request):
    """Check what is ready, then save it. Both read the same folders in Drive.

    The check exists because the PDF step converts whatever a class folder holds, so «какие
    классы можно запустить» is a question about Drive and not about the database. Finding the
    answer by pressing the button and reading the skips is the wrong way round.
    """

    if request.method == "POST":
        if not request.user.has_perm("jobs.run_full_pipeline"):
            return HttpResponse("forbidden", status=403)
        form = ModulePdfReportsForm(request.POST)
        if form.is_valid():
            module = form.cleaned_data["module"]
            links = list(form.cleaned_data.get("class_links") or [])
            class_codes = [link.class_code for link in links]
            scope = ", ".join(class_codes) if class_codes else "все классы"
            initiated_by = request.user if request.user.is_authenticated else None

            if "save_pdf" in request.POST:
                job_run = enqueue_module_pdf_reports_job(
                    module_id=module.pk,
                    class_codes=class_codes or None,
                    initiated_by=initiated_by,
                )
                messages.success(
                    request,
                    f"Сохранение в PDF запущено: {module.get_number_display()}, {scope}.",
                )
            else:
                job_run = enqueue_module_pdf_scan_job(
                    module_id=module.pk,
                    class_codes=class_codes or None,
                    initiated_by=initiated_by,
                )
                messages.success(
                    request, f"Проверка готовности запущена: {module.get_number_display()}, {scope}."
                )
            return redirect(f"{reverse('pipeline:module_pdf_reports')}?run_id={job_run.id}")
    else:
        form = ModulePdfReportsForm()

    requested_run_id = _parse_non_empty(request.GET.get("run_id"))
    if requested_run_id:
        job_run = get_object_or_404(
            JobRun,
            id=requested_run_id,
            job_type__in=[MODULE_PDF_SCAN_JOB_TYPE, MODULE_PDF_REPORTS_JOB_TYPE],
        )
    else:
        job_run = (
            JobRun.objects.filter(
                job_type__in=[MODULE_PDF_SCAN_JOB_TYPE, MODULE_PDF_REPORTS_JOB_TYPE]
            )
            .order_by("-started_at", "-id")
            .first()
        )

    result = job_run.result_json if job_run and isinstance(job_run.result_json, dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    rows = result.get("classes") if isinstance(result.get("classes"), list) else []
    is_scan = bool(job_run and job_run.job_type == MODULE_PDF_SCAN_JOB_TYPE)

    return render(
        request,
        "pipeline/module_pdf_reports.html",
        {
            "form": form,
            "job_run": job_run,
            "summary": summary,
            "rows": rows,
            "is_scan": is_scan,
            "result": result,
        },
    )


@login_required
@permission_required_403("jobs.view_jobrun", message="Доступ запрещён: нет прав на просмотр отчетов на проверку.")
def student_review_reports(request):
    if request.method == "POST":
        if not request.user.has_perm("jobs.run_full_pipeline"):
            return HttpResponse("forbidden", status=403)
        form = StudentReviewReportsForm(request.POST)
        if form.is_valid():
            module = form.cleaned_data["module"]
            links = list(form.cleaned_data.get("class_links") or [])
            class_codes = [link.class_code for link in links]
            scope = ", ".join(class_codes) if class_codes else "все классы"
            job_run = enqueue_module_review_reports_job(
                module_id=module.pk,
                class_codes=class_codes or None,
                remove_extra_files=bool(form.cleaned_data.get("remove_extra_files")),
                initiated_by=request.user if request.user.is_authenticated else None,
            )
            messages.success(
                request,
                f"Подготовка DOCX-отчётов запущена: {module.get_number_display()}, {scope}.",
            )
            return redirect(f"{reverse('pipeline:student_review_reports')}?run_id={job_run.id}")
    else:
        form = StudentReviewReportsForm()

    requested_run_id = _parse_non_empty(request.GET.get("run_id"))
    if requested_run_id:
        job_run = get_object_or_404(
            JobRun,
            id=requested_run_id,
            job_type__in=[
                MODULE_REVIEW_REPORTS_JOB_TYPE,
                MODULE_PDF_REPORTS_JOB_TYPE,
                STUDENT_REVIEW_REPORTS_JOB_TYPE,
            ],
        )
    else:
        job_run = _latest_student_review_reports_run()

    result = job_run.result_json if job_run and isinstance(job_run.result_json, dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    uploaded_files = result.get("uploaded_files") if isinstance(result.get("uploaded_files"), list) else []
    class_rows = result.get("classes") if isinstance(result.get("classes"), list) else []

    # This screen shows both shapes of run: the module-wide one, which counts classes, and the
    # older per-class one, which counted students. Normalising here keeps the template simple.
    counters = {
        "classes_total": summary.get("classes_total", 1 if summary else 0),
        "classes_done": summary.get("classes_done", 0),
        "classes_skipped": summary.get("classes_skipped", 0),
        "classes_failed": summary.get("classes_failed", 0),
        "docx_created": summary.get("docx_created", 0),
        "uploaded_success": summary.get("uploaded_success", 0),
    }

    return render(
        request,
        "pipeline/student_review_reports.html",
        {
            "form": form,
            "job_run": job_run,
            "summary": summary,
            "errors": errors,
            "uploaded_files": uploaded_files,
            "class_rows": class_rows,
            "counters": counters,
            "result": result,
        },
    )


@login_required
@permission_required_403(
    "pipeline.view_criterionentry",
    message="Доступ запрещён: нет прав на просмотр проблемных критериев.",
)
def criteria_failures(request):
    queryset = CriterionEntry.objects.filter(
        validation_status=CriterionEntry.ValidationStatus.INVALID,
        needs_recheck=True,
    ).order_by("class_code", "teacher_name", "subject_name", "module_number", "criterion_text")

    class_code = _parse_non_empty(request.GET.get("class_code"))
    if class_code:
        queryset = queryset.filter(class_code=class_code)

    teacher_name = _parse_non_empty(request.GET.get("teacher_name"))
    if teacher_name:
        queryset = queryset.filter(teacher_name=teacher_name)

    subject_name = _parse_non_empty(request.GET.get("subject_name"))
    if subject_name:
        queryset = queryset.filter(subject_name=subject_name)

    module_number = _parse_non_empty(request.GET.get("module_number"))
    if module_number:
        queryset = queryset.filter(module_number=module_number)

    status = _parse_non_empty(request.GET.get("status"))
    if status:
        queryset = queryset.filter(ai_verdict=status)

    export_format = _parse_non_empty(request.GET.get("export"))
    if export_format == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="criteria_failures.csv"'
        writer = csv.writer(response)
        writer.writerow(
            [
                "class_code",
                "teacher_name",
                "subject_name",
                "module_number",
                "criterion_text",
                "ai_verdict",
                "ai_comment_or_fix",
                "id",
            ]
        )
        for row in queryset:
            writer.writerow(
                [
                    row.class_code,
                    row.teacher_name,
                    row.subject_name,
                    row.module_number,
                    row.criterion_text,
                    row.ai_verdict,
                    f"{row.ai_why} / {row.ai_fix_suggestion}",
                    row.id,
                ]
            )
        return response

    if export_format == "json":
        payload = [
            {
                "class_code": row.class_code,
                "teacher_name": row.teacher_name,
                "subject_name": row.subject_name,
                "module_number": row.module_number,
                "criterion_text": row.criterion_text,
                "ai_verdict": row.ai_verdict,
                "ai_comment_or_fix": f"{row.ai_why} / {row.ai_fix_suggestion}",
                "id": row.id,
            }
            for row in queryset
        ]
        return JsonResponse(payload, safe=False)

    class_counters = (
        queryset.values("class_code")
        .annotate(total_invalid=Count("id"))
        .order_by("-total_invalid", "class_code")
    )
    teacher_counters = (
        queryset.values("teacher_name")
        .annotate(total_invalid=Count("id"))
        .order_by("-total_invalid", "teacher_name")
    )

    paginator = Paginator(queryset, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    preserved_query = request.GET.copy()
    preserved_query.pop("page", None)
    preserved_query.pop("export", None)
    query_without_export = preserved_query.urlencode()

    return render(
        request,
        "pipeline/criteria_failures.html",
        {
            "page_obj": page_obj,
            "class_choices": CriterionEntry.objects.values_list("class_code", flat=True).distinct().order_by("class_code"),
            "teacher_choices": CriterionEntry.objects.values_list("teacher_name", flat=True)
            .distinct()
            .order_by("teacher_name"),
            "subject_choices": CriterionEntry.objects.values_list("subject_name", flat=True)
            .distinct()
            .order_by("subject_name"),
            "module_choices": CriterionEntry.objects.values_list("module_number", flat=True)
            .distinct()
            .order_by("module_number"),
            "status_choices": queryset.values_list("ai_verdict", flat=True).exclude(ai_verdict="").distinct().order_by("ai_verdict"),
            "filters": {
                "class_code": class_code or "",
                "teacher_name": teacher_name or "",
                "subject_name": subject_name or "",
                "module_number": module_number or "",
                "status": status or "",
            },
            "class_counters": class_counters,
            "teacher_counters": teacher_counters,
            "querystring": query_without_export,
            "can_override": _is_admin_role(request.user),
        },
    )

@login_required
@permission_required_403("pipeline.view_criterionentry", message="Доступ запрещён: нет прав на просмотр критерия.")
def criterion_detail(request, pk):
    criterion = get_object_or_404(CriterionEntry, pk=pk)
    events = criterion.review_events.all().order_by("created_at", "id")
    export_format = _parse_non_empty(request.GET.get("export"))

    if export_format == "json":
        payload = [
            {
                "when": event.created_at.isoformat(),
                "event_type": event.event_type,
                "actor_name": event.actor_name,
                "actor_role": event.actor_role,
                "reason": event.reason,
                "payload": event.payload_json,
            }
            for event in events
        ]
        return JsonResponse(payload, safe=False)

    if export_format == "csv":
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="criterion_{criterion.id}_history.csv"'
        writer = csv.writer(response)
        writer.writerow(["when", "event_type", "actor_name", "actor_role", "reason", "payload_json"])
        for event in events:
            writer.writerow(
                [
                    event.created_at.isoformat(),
                    event.event_type,
                    event.actor_name,
                    event.actor_role,
                    event.reason,
                    event.payload_json,
                ]
            )
        return response

    return render(
        request,
        "pipeline/criterion_detail.html",
        {"criterion": criterion, "events": events, "can_override": _is_admin_role(request.user)},
    )


@require_POST
@login_required
@permission_required_403("pipeline.change_criterionentry", message="Доступ запрещён: нельзя менять критерий.")
def override_criterion_valid(request, pk):
    if not _is_admin_role(request.user):
        messages.error(request, "Доступ запрещён: override доступен только роли admin.")
        return HttpResponse("forbidden", status=403)

    criterion = get_object_or_404(CriterionEntry, pk=pk)
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, "Причина обязательна для override.")
        return redirect(request.POST.get("next") or "pipeline:criteria_failures")

    criterion.validation_status = CriterionEntry.ValidationStatus.OVERRIDDEN_VALID
    criterion.ai_verdict = "overridden_valid"
    criterion.ai_why = reason
    criterion.needs_recheck = False
    criterion.save(update_fields=["validation_status", "ai_verdict", "ai_why", "needs_recheck", "updated_at"])
    log_criterion_event(
        criterion,
        event_type=CriterionReviewEvent.EventType.OVERRIDDEN_VALID,
        actor_name=request.user.username,
        actor_role="admin",
        reason=reason,
        payload={"new_status": "overridden_valid"},
    )
    messages.success(request, "Критерий помечен как валидный по ручному override.")
    return redirect(request.POST.get("next") or "pipeline:criteria_failures")

@login_required
@permission_required_403("jobs.run_full_pipeline", message="Доступ запрещён: нет прав на импорт контактов.")
@require_POST
def parent_contacts_sheet_import(request):
    """Pull the contacts from the Google Sheet named in the year settings.

    The file upload stays for one-off fixes; this is the one the school repeats every time
    the table changes, so it reads the link rather than asking for a file again.
    """

    from google_integration.errors import GoogleIntegrationError
    from google_integration.status import build_clients
    from school_years.models import SchoolYear

    target = reverse("pipeline:parent_contacts_list")
    school_year = SchoolYear.objects.filter(is_active=True).first() or SchoolYear.objects.first()
    if school_year is None:
        messages.error(request, "Сначала создайте учебный год.")
        return redirect(target)

    try:
        _, sheets = build_clients()
    except GoogleIntegrationError as exc:
        messages.error(request, exc.user_message)
        return redirect(target)

    job_run = import_parent_contacts(
        school_year,
        sheets=sheets,
        initiated_by=request.user,
        dry_run=request.POST.get("dry_run") == "1",
    )

    result = job_run.result_json or {}
    if isinstance(result.get("error"), str):
        messages.error(request, result["error"])
        return redirect(target)

    summary = result.get("summary", {})
    sync = result.get("sync", {})
    counts = result.get("issue_counts", {})
    prefix = "Пробный прогон" if request.POST.get("dry_run") == "1" else "Импорт"
    message = (
        f"{prefix} завершён: учеников {summary.get('active', 0)}, "
        f"добавлено {sync.get('created', 0)}, обновлено {sync.get('updated', 0)}, "
        f"без контактов {summary.get('without_contacts', 0)}"
    )
    if counts.get("critical"):
        messages.warning(request, f"{message}. Ошибок в строках: {counts['critical']}.")
    else:
        messages.success(request, message)
    return redirect(f"{reverse('job_run_detail', args=[job_run.id])}")


@login_required
@permission_required_403("pipeline.view_parentcontact", message="Доступ запрещён: нет прав на просмотр контактов родителей.")
def parent_contacts_list(request):
    queryset = ParentContact.objects.all().order_by("parallel", "student_name")

    parallel = _parse_non_empty(request.GET.get("parallel"))
    if parallel and parallel.isdigit():
        queryset = queryset.filter(parallel=int(parallel))

    student = _parse_non_empty(request.GET.get("student"))
    if student:
        queryset = queryset.filter(student_name__icontains=student)

    email = _parse_non_empty(request.GET.get("email"))
    if email:
        queryset = queryset.filter(Q(parent_email_1__icontains=email) | Q(parent_email_2__icontains=email))

    paginator = Paginator(queryset, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    import_form = ParentContactsImportForm()

    return render(
        request,
        "pipeline/parent_contacts_list.html",
        {
            "page_obj": page_obj,
            "filters": {"parallel": parallel or "", "student": student or "", "email": email or ""},
            "import_form": import_form,
        },
    )


@login_required
@permission_required_403("pipeline.add_parentcontact", message="Доступ запрещён: нельзя создавать контакты родителей.")
def parent_contact_create(request):
    if request.method == "POST":
        form = ParentContactForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("pipeline:parent_contacts_list")
    else:
        form = ParentContactForm()

    return render(
        request,
        "pipeline/parent_contacts_form.html",
        {"form": form, "title": "Контакты родителей: создать", "submit_label": "Сохранить"},
    )


@login_required
@permission_required_403("pipeline.change_parentcontact", message="Доступ запрещён: нельзя редактировать контакты родителей.")
def parent_contact_edit(request, pk):
    contact = get_object_or_404(ParentContact, pk=pk)
    if request.method == "POST":
        form = ParentContactForm(request.POST, instance=contact)
        if form.is_valid():
            form.save()
            return redirect("pipeline:parent_contacts_list")
    else:
        form = ParentContactForm(instance=contact)

    return render(
        request,
        "pipeline/parent_contacts_form.html",
        {"form": form, "title": "Контакты родителей: редактировать", "submit_label": "Сохранить"},
    )


@require_POST
@login_required
@permission_required_403("pipeline.change_parentcontact", message="Доступ запрещён: нельзя отключать контакты родителей.")
def parent_contact_disable(request, pk):
    contact = get_object_or_404(ParentContact, pk=pk)
    contact.is_active = False
    contact.save(update_fields=["is_active", "updated_at"])
    return redirect("pipeline:parent_contacts_list")


@require_POST
@login_required
@permission_required_403("pipeline.add_parentcontact", message="Доступ запрещён: нельзя импортировать контакты родителей.")
def parent_contacts_import(request):
    form = ParentContactsImportForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Не удалось загрузить файл импорта.")
        return redirect("pipeline:parent_contacts_list")

    uploaded_file = form.cleaned_data["file"]
    if not uploaded_file.name.lower().endswith(".csv"):
        messages.error(request, "Пока поддерживается только CSV импорт.")
        return redirect("pipeline:parent_contacts_list")

    result = import_parent_contacts_csv(uploaded_file.read())
    for error in result.errors:
        messages.error(request, error)

    messages.success(
        request,
        f"Импорт завершён: created={result.created}, updated={result.updated}, skipped={result.skipped}, errors={len(result.errors)}",
    )
    return redirect("pipeline:parent_contacts_list")


@login_required
@permission_required_403(
    "pipeline.view_validcriteriontemplate",
    message="Доступ запрещён: нет прав на просмотр whitelist критериев.",
)
def valid_criteria_list(request):
    templates = ValidCriterionTemplate.objects.select_related("created_by").order_by("name")
    return render(
        request,
        "pipeline/valid_criteria_list.html",
        {
            "templates": templates,
        },
    )


@login_required
@permission_required_403(
    "pipeline.add_validcriteriontemplate",
    message="Доступ запрещён: нельзя создавать whitelist критерии.",
)
def valid_criterion_create(request):
    if request.method == "POST":
        form = ValidCriterionTemplateForm(request.POST)
        if form.is_valid():
            template = form.save(commit=False)
            template.created_by = request.user
            template.save()
            return redirect("pipeline:valid_criteria_list")
    else:
        form = ValidCriterionTemplateForm()

    return render(
        request,
        "pipeline/valid_criteria_form.html",
        {"form": form, "title": "Whitelist критериев: создать", "submit_label": "Сохранить"},
    )


@login_required
@permission_required_403(
    "pipeline.change_validcriteriontemplate",
    message="Доступ запрещён: нельзя редактировать whitelist критерии.",
)
def valid_criterion_edit(request, pk):
    template = get_object_or_404(ValidCriterionTemplate, pk=pk)
    if request.method == "POST":
        form = ValidCriterionTemplateForm(request.POST, instance=template)
        if form.is_valid():
            form.save()
            return redirect("pipeline:valid_criteria_list")
    else:
        form = ValidCriterionTemplateForm(instance=template)

    return render(
        request,
        "pipeline/valid_criteria_form.html",
        {"form": form, "title": "Whitelist критериев: редактировать", "submit_label": "Сохранить"},
    )


@require_POST
@login_required
@permission_required_403(
    "pipeline.change_validcriteriontemplate",
    message="Доступ запрещён: нельзя отключать whitelist критерии.",
)
def valid_criterion_disable(request, pk):
    template = get_object_or_404(ValidCriterionTemplate, pk=pk)
    template.is_active = False
    template.save(update_fields=["is_active", "updated_at"])
    return redirect("pipeline:valid_criteria_list")
