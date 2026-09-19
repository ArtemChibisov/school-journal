"""Google connection screen: status and access preflight.

DEV-0208 and DEV-0209. The administrator sees which Google account is configured and can
verify that it reaches every link stored on a school year, before any mass operation starts.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from admin_panel.authz import permission_required_403
from school_years.models import SchoolYear

from .errors import GoogleIntegrationError
from .preflight import run_google_access_preflight
from .status import build_clients, get_connection_status

VIEW_PERMISSION = "jobs.view_jobrun"
RUN_PERMISSION = "journal_links.change_classsheetlink"


def _selected_school_year(request) -> SchoolYear | None:
    raw = (request.POST.get("school_year") or request.GET.get("school_year") or "").strip()
    if raw:
        return SchoolYear.objects.filter(pk=raw).first()
    return SchoolYear.objects.filter(is_active=True).first() or SchoolYear.objects.first()


@login_required
@permission_required_403(VIEW_PERMISSION)
def google_status(request):
    school_year = _selected_school_year(request)
    status = get_connection_status(school_year.pk if school_year else None)

    return render(
        request,
        "google_integration/status.html",
        {
            "status": status,
            "school_year": school_year,
            "school_years": SchoolYear.objects.all(),
            "items": status["last_preflight_result"].get("items", []),
        },
    )


@login_required
@permission_required_403(RUN_PERMISSION)
@require_POST
def run_access_preflight(request):
    school_year = _selected_school_year(request)
    redirect_url = reverse("google_integration:google_status")

    if school_year is None:
        messages.error(request, "Сначала создайте учебный год.")
        return redirect(redirect_url)

    try:
        drive, sheets = build_clients()
    except GoogleIntegrationError as exc:
        messages.error(request, exc.user_message)
        return redirect(f"{redirect_url}?school_year={school_year.pk}")

    job_run = run_google_access_preflight(
        school_year,
        drive=drive,
        sheets=sheets,
        initiated_by=request.user,
    )

    result = job_run.result_json or {}
    if result.get("failed"):
        messages.warning(
            request,
            f"Проверка завершена: доступно {result.get('ok', 0)}, с ошибкой {result['failed']}.",
        )
    else:
        messages.success(
            request,
            f"Проверка завершена: доступно {result.get('ok', 0)} из {result.get('total', 0)}.",
        )

    return redirect(f"{redirect_url}?school_year={school_year.pk}")
