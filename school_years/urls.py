from django.urls import path

from .views import (
    assistant_principals,
    module_registers,
    roster_import,
    run_register_generation_view,
    run_register_scan_view,
    run_roster_import,
    run_subject_teacher_import,
    school_year_settings,
    subject_teachers,
)

app_name = "school_years"

urlpatterns = [
    path("school-year/", school_year_settings, name="school_year_settings"),
    path("assistant-principals/", assistant_principals, name="assistant_principals"),
    path("roster/", roster_import, name="roster_import"),
    path("roster/run/", run_roster_import, name="run_roster_import"),
    path("subject-teachers/", subject_teachers, name="subject_teachers"),
    path("subject-teachers/run/", run_subject_teacher_import, name="run_subject_teacher_import"),
    path("modules/", module_registers, name="module_registers"),
    path("modules/scan/", run_register_scan_view, name="run_register_scan"),
    path("modules/generate/", run_register_generation_view, name="run_register_generation"),
]
