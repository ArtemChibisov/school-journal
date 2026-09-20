from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from google_integration.errors import CATEGORY_INVALID_CONFIG, GoogleIntegrationError
from google_integration.ids import spreadsheet_url
from jobs.models import JobRun
from school_years.imports.roster_sync import JOB_TYPE
from school_years.models import ClassGroup, SchoolYear
from school_years.tests.test_roster_import import BOOK_ID, workbook


class RosterImportViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("zavuch", password="roster-view-tests")
        self.page_url = reverse("school_years:roster_import")
        self.run_url = reverse("school_years:run_roster_import")

        self.sheets = workbook(
            classes=[["4А", "4", "Алена Прокушева", "yes"]],
            students=[["4А", "1", "Оливия Арутюнян", "yes", ""]],
        )
        self.school_year = SchoolYear.objects.create(
            name="25/26",
            class_roster_sheet_url=spreadsheet_url(BOOK_ID),
        )

    def _grant(self, *codenames):
        self.user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        self.user = get_user_model().objects.get(pk=self.user.pk)

    def _run(self, data=None):
        with patch("school_years.views.build_clients", return_value=(None, self.sheets)):
            return self.client.post(self.run_url, data or {"school_year": self.school_year.pk})

    def test_page_requires_login(self):
        response = self.client.get(self.page_url)

        self.assertEqual(response.status_code, 302)

    def test_page_denied_without_permission(self):
        self.client.force_login(self.user)

        self.assertEqual(self.client.get(self.page_url).status_code, 403)

    def test_page_renders_and_offers_the_source_link(self):
        self._grant("view_jobrun")
        self.client.force_login(self.user)

        response = self.client.get(self.page_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "school_years/roster_import.html")
        self.assertContains(response, "таблица состава классов")

    def test_page_warns_when_the_source_link_is_missing(self):
        self.school_year.class_roster_sheet_url = ""
        self.school_year.save()
        self._grant("view_jobrun")
        self.client.force_login(self.user)

        response = self.client.get(self.page_url)

        self.assertContains(response, "не указана таблица состава классов")

    def test_run_denied_without_permission(self):
        self._grant("view_jobrun")
        self.client.force_login(self.user)

        response = self._run()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(JobRun.objects.filter(job_type=JOB_TYPE).exists())

    def test_run_rejects_get(self):
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)

        self.assertEqual(self.client.get(self.run_url).status_code, 405)

    def test_import_creates_classes(self):
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)

        response = self._run()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ClassGroup.objects.count(), 1)
        self.assertEqual(JobRun.objects.get(job_type=JOB_TYPE).initiated_by, self.user)

    def test_dry_run_changes_nothing(self):
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)

        self._run({"school_year": self.school_year.pk, "dry_run": "1"})

        self.assertEqual(ClassGroup.objects.count(), 0)
        self.assertTrue(JobRun.objects.get(job_type=JOB_TYPE).result_json["dry_run"])

    def test_result_is_shown_on_the_page(self):
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)
        self._run()

        response = self.client.get(self.page_url, {"school_year": self.school_year.pk})

        self.assertContains(response, "Последний запуск")
        self.assertContains(response, "Алена Прокушева")
        self.assertContains(response, "4А")

    def test_problems_are_listed_in_plain_language(self):
        self.sheets = workbook(
            classes=[["4А", "4", "", "yes"]],
            students=[["4А", "1", "Оливия", "yes", ""]],
        )
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)
        self._run()

        response = self.client.get(self.page_url, {"school_year": self.school_year.pk})

        self.assertContains(response, "Что нужно исправить в таблице")
        self.assertContains(response, "не указан тьютор")
        self.assertNotContains(response, "MISSING_TUTOR")

    def test_credential_failure_shows_a_user_message(self):
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)
        error = GoogleIntegrationError(CATEGORY_INVALID_CONFIG, "token file missing")

        with patch("school_years.views.build_clients", side_effect=error):
            response = self.client.post(
                self.run_url, {"school_year": self.school_year.pk}, follow=True
            )

        text = response.content.decode()
        self.assertIn(error.user_message, text)
        self.assertNotIn("token file missing", text)
