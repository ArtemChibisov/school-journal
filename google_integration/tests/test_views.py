from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from google_integration.errors import CATEGORY_INVALID_CONFIG, GoogleIntegrationError
from google_integration.fakes import FakeDriveClient, FakeSheetsClient
from google_integration.ids import folder_url
from google_integration.preflight import JOB_TYPE
from jobs.models import JobRun
from school_years.models import SchoolYear


class GoogleStatusViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("zavuch", password="pwd-for-tests")
        self.status_url = reverse("google_integration:google_status")
        self.preflight_url = reverse("google_integration:run_access_preflight")

        self.drive = FakeDriveClient()
        self.sheets = FakeSheetsClient()
        self.root = self.drive.add_folder("Отчеты 4-11")
        self.school_year = SchoolYear.objects.create(
            name="25/26",
            register_root_4_11_url=folder_url(self.root),
        )

    def _grant(self, *codenames):
        self.user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        self.user = get_user_model().objects.get(pk=self.user.pk)

    def test_status_requires_login(self):
        response = self.client.get(self.status_url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"next={self.status_url}", response["Location"])

    def test_status_denied_without_permission(self):
        self.client.force_login(self.user)

        response = self.client.get(self.status_url)

        self.assertEqual(response.status_code, 403)

    def test_status_renders_for_a_viewer(self):
        self._grant("view_jobrun")
        self.client.force_login(self.user)

        response = self.client.get(self.status_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "google_integration/status.html")
        self.assertContains(response, "Google-подключение")

    def test_status_says_preflight_never_ran(self):
        self._grant("view_jobrun")
        self.client.force_login(self.user)

        response = self.client.get(self.status_url)

        self.assertContains(response, "Проверка доступов ещё не запускалась")

    def test_status_does_not_call_google(self):
        self._grant("view_jobrun")
        self.client.force_login(self.user)

        with patch("google_integration.status.build_clients") as build:
            self.client.get(self.status_url)

        build.assert_not_called()

    def test_preflight_denied_without_permission(self):
        self._grant("view_jobrun")
        self.client.force_login(self.user)

        response = self.client.post(self.preflight_url, {"school_year": self.school_year.pk})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(JobRun.objects.filter(job_type=JOB_TYPE).exists())

    def test_preflight_rejects_get(self):
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)

        response = self.client.get(self.preflight_url)

        self.assertEqual(response.status_code, 405)

    def test_preflight_creates_a_job_run_and_redirects(self):
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)

        with patch(
            "google_integration.views.build_clients",
            return_value=(self.drive, self.sheets),
        ):
            response = self.client.post(self.preflight_url, {"school_year": self.school_year.pk})

        self.assertEqual(response.status_code, 302)
        job_run = JobRun.objects.get(job_type=JOB_TYPE)
        self.assertEqual(job_run.status, JobRun.Status.SUCCESS)
        self.assertEqual(job_run.initiated_by, self.user)
        self.assertEqual(job_run.result_json["ok"], 1)

    def test_preflight_result_is_shown_on_the_status_page(self):
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)

        with patch(
            "google_integration.views.build_clients",
            return_value=(self.drive, self.sheets),
        ):
            self.client.post(self.preflight_url, {"school_year": self.school_year.pk})

        response = self.client.get(self.status_url, {"school_year": self.school_year.pk})

        self.assertContains(response, "Корневая папка журналов 4-11")
        self.assertContains(response, "доступно")

    def test_preflight_shows_user_message_when_credentials_are_missing(self):
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)
        error = GoogleIntegrationError(CATEGORY_INVALID_CONFIG, "token file missing")

        with patch("google_integration.views.build_clients", side_effect=error):
            response = self.client.post(
                self.preflight_url, {"school_year": self.school_year.pk}, follow=True
            )

        self.assertFalse(JobRun.objects.filter(job_type=JOB_TYPE).exists())
        text = response.content.decode()
        self.assertIn(error.user_message, text)
        self.assertNotIn("token file missing", text)

    def test_preflight_without_a_school_year_reports_it(self):
        SchoolYear.objects.all().delete()
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)

        response = self.client.post(self.preflight_url, follow=True)

        self.assertContains(response, "Сначала создайте учебный год")
