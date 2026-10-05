from django.urls import path

from .views import reminder_list, toggle_reminder_mute

app_name = "notifications"

urlpatterns = [
    path("reminders/", reminder_list, name="reminder_list"),
    path("reminders/mute/", toggle_reminder_mute, name="toggle_reminder_mute"),
]
