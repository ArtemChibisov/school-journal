from django.urls import path

from .views import roster_import, run_roster_import, school_year_settings

app_name = "school_years"

urlpatterns = [
    path("school-year/", school_year_settings, name="school_year_settings"),
    path("roster/", roster_import, name="roster_import"),
    path("roster/run/", run_roster_import, name="run_roster_import"),
]
