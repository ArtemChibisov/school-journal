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

from .forms import (
    FOLDER_FIELDS,
    SPREADSHEET_FIELDS,
    SchoolYearForm,
    filled_link_count,
    missing_for_generation,
)
from .imports.roster_sync import JOB_TYPE as ROSTER_JOB_TYPE, import_class_roster
from .models import ClassGroup, SchoolYear

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


def _latest_import(school_year_id: int | None) -> JobRun | None:
    queryset = JobRun.objects.filter(job_type=ROSTER_JOB_TYPE)
    if school_year_id is not None:
        queryset = queryset.filter(params_json__school_year_id=school_year_id)
    return queryset.order_by("-started_at").first()


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
            "missing": missing_for_generation(school_year) if school_year else [],
            "filled_count": filled_link_count(school_year) if school_year else (0, 0),
        },
    )


@login_required
@permission_required_403(VIEW_PERMISSION)
def roster_import(request):
    school_year = _selected_school_year(request)
    last_run = _latest_import(school_year.pk if school_year else None)
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
