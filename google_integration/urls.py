from django.urls import path

from .views import google_status, run_access_preflight

app_name = "google_integration"

urlpatterns = [
    path("google/status/", google_status, name="google_status"),
    path("google/preflight/", run_access_preflight, name="run_access_preflight"),
]
