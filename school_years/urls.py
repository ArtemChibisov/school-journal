from django.urls import path

from .views import roster_import, run_roster_import

app_name = "school_years"

urlpatterns = [
    path("roster/", roster_import, name="roster_import"),
    path("roster/run/", run_roster_import, name="run_roster_import"),
]
