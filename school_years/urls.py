from django.urls import path

from .views import (
    module_registers,
    roster_import,
    run_register_scan_view,
    run_roster_import,
    school_year_settings,
)

app_name = "school_years"

urlpatterns = [
    path("school-year/", school_year_settings, name="school_year_settings"),
    path("roster/", roster_import, name="roster_import"),
    path("roster/run/", run_roster_import, name="run_roster_import"),
    path("modules/", module_registers, name="module_registers"),
    path("modules/scan/", run_register_scan_view, name="run_register_scan"),
]
