"""Screens for the school year domain.

DEV-0401 and DEV-0406: running the roster import and reading its report. A phase is only
finished when the завуч can see the result here, not only in the output of a command.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from admin_panel.authz import permission_required_403
from google_integration.errors import GoogleIntegrationError
from google_integration.status import build_clients
from jobs.models import JobRun
from journal_links.models import DescriptorCriteriaReportTarget

from .forms import (
    ACCESS_FIELDS,
    AssistantPrincipalForm,
    FOLDER_FIELDS,
    SPREADSHEET_FIELDS,
    SchoolModuleForm,
    SchoolYearForm,
    filled_link_count,
    missing_for_generation,
)
from .assistant_principals import coverage
from .imports.registers import JOB_TYPE as SCAN_JOB_TYPE, run_register_scan
from .imports.roster_sync import JOB_TYPE as ROSTER_JOB_TYPE, import_class_roster
from .imports.subject_teacher_sync import (
    JOB_TYPE as SUBJECT_JOB_TYPE,
    import_subject_teachers,
)
from .models import (
    AssistantPrincipal,
    ClassGroup,
    ClassRegister,
    SchoolModule,
    SchoolYear,
    SubjectTeacher,
    Teacher,
)

VIEW_PERMISSION = "jobs.view_jobrun"
RUN_PERMISSION = "journal_links.change_classsheetlink"


def _selected_school_year(request) -> SchoolYear | None:
    raw = (request.POST.get("school_year") or request.GET.get("school_year") or "").strip()
    if raw == "new":
        # An explicit request for a blank form, so the settings screen can create a year.
        return None
    if raw:
        return SchoolYear.objects.filter(pk=raw).first()
    return SchoolYear.objects.filter(is_active=True).first() or SchoolYear.objects.first()


def _latest_run(job_type: str, school_year_id: int | None) -> JobRun | None:
    queryset = JobRun.objects.filter(job_type=job_type)
    if school_year_id is not None:
        queryset = queryset.filter(params_json__school_year_id=school_year_id)
    return queryset.order_by("-started_at").first()



def _sync_monitoring_target(school_year) -> None:
    """Point the legacy report target at the year's monitoring spreadsheet.

    The fill-check export writes to `DescriptorCriteriaReportTarget`, configured on a separate
    legacy screen. Asking the завуч to paste the same link in two places is a way to end up
    with two different links, so the year settings own it and this keeps the old setting in
    step. Same bridge idea as the class sheet links - see docs/execution_plan.md.
    """

    url = (school_year.monitoring_sheet_url or "").strip()
    if not url:
        return

    target = DescriptorCriteriaReportTarget.objects.filter(is_active=True).first()
    if target is None:
        DescriptorCriteriaReportTarget.objects.create(
            name=f"Заполняемость {school_year}",
            google_sheet_url=url,
            is_active=True,
        )
        return

    if target.google_sheet_url != url:
        target.google_sheet_url = url
        target.save()

@login_required
@permission_required_403(VIEW_PERMISSION)
def assistant_principals(request):
    """Who signs the reports, and whether every parallel has someone.

    The list is short - three people - so it is edited in place rather than through a page per
    person. What is worth seeing at a glance is the other table: every parallel the school has
    and who covers it, because a parallel nobody covers prints an empty line in every report
    of every class in it, and nothing else would ever say so.
    """

    school_year = _selected_school_year(request)
    editing = AssistantPrincipal.objects.filter(pk=request.GET.get("edit")).first()

    if request.method == "POST":
        if not request.user.has_perm(RUN_PERMISSION):
            return HttpResponseForbidden("forbidden")
        instance = AssistantPrincipal.objects.filter(pk=request.POST.get("pk")).first()
        form = AssistantPrincipalForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            messages.success(request, "Сохранено.")
            return redirect(reverse("school_years:assistant_principals"))
    else:
        form = AssistantPrincipalForm(instance=editing)

    return render(
        request,
        "school_years/assistant_principals.html",
        {
            "form": form,
            "editing": editing,
            "principals": AssistantPrincipal.objects.all(),
            "coverage": coverage(school_year) if school_year else [],
            "school_year": school_year,
            "can_edit": request.user.has_perm(RUN_PERMISSION),
        },
    )


@login_required
@permission_required_403(VIEW_PERMISSION)
def school_year_settings(request):
    """Create or edit the school year: the links everything else reads from."""

    school_year = _selected_school_year(request)
    can_edit = request.user.has_perm(RUN_PERMISSION)

    if request.method == "POST":
        if not can_edit:
            return HttpResponseForbidden("Доступ запрещён: недостаточно прав для изменения настроек.")

        form = SchoolYearForm(request.POST, instance=school_year)
        if form.is_valid():
            saved = form.save(commit=False)
            if saved.pk is None:
                saved.created_by = request.user
            saved.updated_by = request.user
            saved.save()
            _sync_monitoring_target(saved)
            messages.success(request, "Настройки учебного года сохранены.")
            return redirect(f"{reverse('school_years:school_year_settings')}?school_year={saved.pk}")
        messages.error(request, "Настройки не сохранены: проверьте отмеченные поля.")
    else:
        form = SchoolYearForm(instance=school_year)

    return render(
        request,
        "school_years/school_year_settings.html",
        {
            "form": form,
            "school_year": school_year,
            "school_years": SchoolYear.objects.all(),
            "can_edit": can_edit,
            "sheet_fields": [form[name] for name in SPREADSHEET_FIELDS],
            "folder_fields": [form[name] for name in FOLDER_FIELDS],
            "access_fields": [form[name] for name in ACCESS_FIELDS],
            "missing": missing_for_generation(school_year) if school_year else [],
            "filled_count": filled_link_count(school_year) if school_year else (0, 0),
        },
    )


@login_required
@permission_required_403(VIEW_PERMISSION)
def roster_import(request):
    school_year = _selected_school_year(request)
    last_run = _latest_run(ROSTER_JOB_TYPE, school_year.pk if school_year else None)
    result = (last_run.result_json if last_run else {}) or {}

    issues = result.get("issues", [])

    return render(
        request,
        "school_years/roster_import.html",
        {
            "school_year": school_year,
            "school_years": SchoolYear.objects.all(),
            "last_run": last_run,
            "result": result,
            "sync": result.get("sync", {}),
            "issue_counts": result.get("issue_counts", {}),
            "critical_issues": [item for item in issues if item["severity"] == "critical"],
            "other_issues": [item for item in issues if item["severity"] != "critical"],
            "imported_classes": result.get("classes", []),
            "class_groups": (
                ClassGroup.objects.filter(school_year=school_year).order_by("grade_number", "class_code")
                if school_year
                else ClassGroup.objects.none()
            ),
        },
    )


@login_required
@permission_required_403(RUN_PERMISSION)
@require_POST
def run_roster_import(request):
    school_year = _selected_school_year(request)
    redirect_url = reverse("school_years:roster_import")
    dry_run = request.POST.get("dry_run") == "1"

    if school_year is None:
        messages.error(request, "Сначала создайте учебный год.")
        return redirect(redirect_url)

    target = f"{redirect_url}?school_year={school_year.pk}"

    try:
        _, sheets = build_clients()
    except GoogleIntegrationError as exc:
        messages.error(request, exc.user_message)
        return redirect(target)

    job_run = import_class_roster(
        school_year,
        sheets=sheets,
        initiated_by=request.user,
        dry_run=dry_run,
    )

    result = job_run.result_json or {}
    if "error" in result:
        messages.error(request, result["error"]["user_message"])
        return redirect(target)

    prefix = "Пробный прогон" if dry_run else "Импорт"
    valid = result.get("classes_valid", 0)
    critical = result.get("issue_counts", {}).get("critical", 0)

    if critical:
        messages.warning(request, f"{prefix} завершён: классов без ошибок {valid}, проблем {critical}.")
    else:
        messages.success(request, f"{prefix} завершён: классов без ошибок {valid}, проблем нет.")

    return redirect(target)


@login_required
@permission_required_403(VIEW_PERMISSION)
def subject_teachers(request):
    """The subject-teacher table as it was last read, and the button to read it again."""

    school_year = _selected_school_year(request)
    last_run = _latest_run(SUBJECT_JOB_TYPE, school_year.pk if school_year else None)
    result = (last_run.result_json if last_run else {}) or {}
    issues = result.get("issues", [])

    assignments = (
        SubjectTeacher.objects.filter(school_year=school_year, is_active=True)
        .select_related("class_group", "teacher")
        .order_by("class_group__grade_number", "class_group__class_code", "subject_name")
        if school_year
        else SubjectTeacher.objects.none()
    )

    return render(
        request,
        "school_years/subject_teachers.html",
        {
            "school_year": school_year,
            "school_years": SchoolYear.objects.all(),
            "last_run": last_run,
            "result": result,
            "sync": result.get("sync", {}),
            "issue_counts": result.get("issue_counts", {}),
            "critical_issues": [item for item in issues if item["severity"] == "critical"],
            "other_issues": [item for item in issues if item["severity"] != "critical"],
            "assignments": assignments,
            "teachers": (
                Teacher.objects.filter(school_year=school_year, is_active=True)
                if school_year
                else Teacher.objects.none()
            ),
        },
    )


@login_required
@permission_required_403(RUN_PERMISSION)
@require_POST
def run_subject_teacher_import(request):
    school_year = _selected_school_year(request)
    redirect_url = reverse("school_years:subject_teachers")
    dry_run = request.POST.get("dry_run") == "1"

    if school_year is None:
        messages.error(request, "Сначала создайте учебный год.")
        return redirect(redirect_url)

    target = f"{redirect_url}?school_year={school_year.pk}"

    try:
        _, sheets = build_clients()
    except GoogleIntegrationError as exc:
        messages.error(request, exc.user_message)
        return redirect(target)

    job_run = import_subject_teachers(
        school_year,
        sheets=sheets,
        initiated_by=request.user,
        dry_run=dry_run,
    )

    result = job_run.result_json or {}
    if "error" in result:
        messages.error(request, result["error"]["user_message"])
        return redirect(target)

    prefix = "Пробный прогон" if dry_run else "Импорт"
    critical = result.get("issue_counts", {}).get("critical", 0)
    message = (
        f"{prefix} завершён: преподавателей {result.get('teachers_total', 0)}, "
        f"связок {result.get('assignments_total', 0)}"
    )

    if critical:
        messages.warning(request, f"{message}, проблем {critical}.")
    else:
        messages.success(request, f"{message}, проблем нет.")

    return redirect(target)


def _selected_module(request, school_year):
    if school_year is None:
        return None
    raw = (request.POST.get("module") or request.GET.get("module") or "").strip()
    if raw == "new":
        return None
    if raw:
        return SchoolModule.objects.filter(pk=raw, school_year=school_year).first()
    return SchoolModule.objects.filter(school_year=school_year).order_by("number").first()


def _latest_scan(module_id: int | None) -> JobRun | None:
    queryset = JobRun.objects.filter(job_type=SCAN_JOB_TYPE)
    if module_id is not None:
        queryset = queryset.filter(params_json__module_id=module_id)
    return queryset.order_by("-started_at").first()


@login_required
@permission_required_403(VIEW_PERMISSION)
def module_registers(request):
    """Module dates and folders, plus the journals found inside those folders."""

    school_year = _selected_school_year(request)
    module = _selected_module(request, school_year)
    can_edit = request.user.has_perm(RUN_PERMISSION)

    if request.method == "POST":
        if not can_edit:
            return HttpResponseForbidden("Доступ запрещён: недостаточно прав для изменения настроек.")
        if school_year is None:
            messages.error(request, "Сначала создайте учебный год.")
            return redirect(reverse("school_years:module_registers"))

        form = SchoolModuleForm(request.POST, instance=module, school_year=school_year)
        if form.is_valid():
            saved = form.save(commit=False)
            saved.school_year = school_year
            saved.save()
            messages.success(request, "Модуль сохранён.")
            return redirect(
                f"{reverse('school_years:module_registers')}?school_year={school_year.pk}&module={saved.pk}"
            )
        messages.error(request, "Модуль не сохранён: проверьте отмеченные поля.")
    else:
        form = SchoolModuleForm(instance=module, school_year=school_year)

    last_run = _latest_scan(module.pk if module else None)
    result = (last_run.result_json if last_run else {}) or {}

    return render(
        request,
        "school_years/module_registers.html",
        {
            "form": form,
            "report_folder_fields": [
                form[name]
                for name in (
                    "docx_folder_0_3_url",
                    "docx_folder_4_11_url",
                    "pdf_folder_0_3_url",
                    "pdf_folder_4_11_url",
                )
            ],
            "school_year": school_year,
            "school_years": SchoolYear.objects.all(),
            "module": module,
            "modules": SchoolModule.objects.filter(school_year=school_year) if school_year else [],
            "can_edit": can_edit,
            "last_run": last_run,
            "result": result,
            "registers": (
                ClassRegister.objects.filter(module=module)
                .select_related("class_group")
                .order_by("class_group__grade_number", "class_group__class_code")
                if module
                else ClassRegister.objects.none()
            ),
        },
    )


@login_required
@permission_required_403(RUN_PERMISSION)
@require_POST
def run_register_scan_view(request):
    school_year = _selected_school_year(request)
    module = _selected_module(request, school_year)
    redirect_url = reverse("school_years:module_registers")
    dry_run = request.POST.get("dry_run") == "1"

    if module is None:
        messages.error(request, "Сначала сохраните модуль.")
        return redirect(redirect_url)

    target = f"{redirect_url}?school_year={school_year.pk}&module={module.pk}"

    try:
        drive, _ = build_clients()
    except GoogleIntegrationError as exc:
        messages.error(request, exc.user_message)
        return redirect(target)

    job_run = run_register_scan(module, drive=drive, initiated_by=request.user, dry_run=dry_run)

    result = job_run.result_json or {}
    if "error" in result:
        messages.error(request, result["error"]["user_message"])
        return redirect(target)

    prefix = "Поиск" if dry_run else "Привязка"
    messages.success(
        request,
        f"{prefix} завершён: найдено журналов {result.get('matched_total', 0)} "
        f"из {result.get('classes_total', 0)}.",
    )
    return redirect(target)
