from django.test import TestCase

from google_integration.errors import CATEGORY_NOT_FOUND, GoogleIntegrationError
from jobs.models import JobLog, JobRun
from validation.descriptor_criteria_fill import fetch_sheet_titles, restore_full_sheet_names

SHEET_URL = "https://docs.google.com/spreadsheets/d/1ZF69YxwebacQEs6sadKwXXyUNvGBy_QpwxhGWEH1laQ/edit"

# Exactly what Google's .xlsx export does to a long worksheet name: 31 characters, mid-word.
CUT = "Проектная деятельность | Projec"
FULL = "Проектная деятельность | Project"


class RestoreFullSheetNamesTests(TestCase):
    def test_a_cut_name_is_restored(self):
        restored = restore_full_sheet_names([CUT], [FULL])

        self.assertEqual(restored, {CUT: FULL})

    def test_a_name_that_was_never_cut_is_left_alone(self):
        self.assertEqual(restore_full_sheet_names(["Drama"], ["Drama"]), {})

    def test_only_the_cut_sheets_appear_in_the_mapping(self):
        restored = restore_full_sheet_names(["Drama", CUT], ["Drama", FULL])

        self.assertEqual(restored, {CUT: FULL})

    def test_position_decides_which_title_belongs_to_which_sheet(self):
        restored = restore_full_sheet_names(
            ["Английский язык Нино | Cambridg", "Английский язык Мариам | Cambri"],
            ["Английский язык Нино | Cambridge", "Английский язык Мариам | Cambridge"],
        )

        self.assertEqual(
            restored,
            {
                "Английский язык Нино | Cambridg": "Английский язык Нино | Cambridge",
                "Английский язык Мариам | Cambri": "Английский язык Мариам | Cambridge",
            },
        )

    def test_a_different_number_of_sheets_is_not_trusted(self):
        self.assertEqual(restore_full_sheet_names([CUT], [FULL, "Drama"]), {})

    def test_a_title_that_is_not_an_extension_of_the_name_is_not_trusted(self):
        self.assertEqual(restore_full_sheet_names(["Drama"], ["Theatre"]), {})

    def test_no_titles_at_all_changes_nothing(self):
        self.assertEqual(restore_full_sheet_names([CUT], None), {})
        self.assertEqual(restore_full_sheet_names([CUT], []), {})

    def test_one_unreadable_sheet_does_not_spoil_the_others(self):
        restored = restore_full_sheet_names(
            [CUT, "Science_Grade 7"], [FULL, "Science/Grade 7"]
        )

        self.assertEqual(restored, {CUT: FULL})


class FetchSheetTitlesTests(TestCase):
    def setUp(self):
        self.job_run = JobRun.objects.create(job_type="descriptor_criteria_fill_check")

    def test_titles_come_back_from_the_client(self):
        class Client:
            def get_sheet_titles(self, spreadsheet_id):
                return [FULL, "Drama"]

        with self.settings(GOOGLE_ACCESS_MODE="oauth_owner"):
            titles = self._fetch(Client())

        self.assertEqual(titles, [FULL, "Drama"])

    def test_a_google_failure_returns_nothing_and_is_logged(self):
        class Client:
            def get_sheet_titles(self, spreadsheet_id):
                raise GoogleIntegrationError(
                    CATEGORY_NOT_FOUND,
                    "spreadsheet is gone",
                    user_message="Таблица не найдена.",
                )

        titles = self._fetch(Client())

        self.assertIsNone(titles)
        log = JobLog.objects.get(job_run=self.job_run)
        self.assertEqual(log.level, JobLog.Level.WARNING)
        self.assertIn("Full sheet names unavailable", log.message)

    def test_an_expired_token_does_not_stop_the_check(self):
        """The real failure this hit first: google.auth raises its own error, not ours."""

        class RefreshError(Exception):
            pass

        class Client:
            def get_sheet_titles(self, spreadsheet_id):
                raise RefreshError("invalid_grant: Token has been expired or revoked.")

        titles = self._fetch(Client())

        self.assertIsNone(titles)
        log = JobLog.objects.get(job_run=self.job_run)
        self.assertEqual(log.context_json["error_type"], "RefreshError")

    def test_nothing_is_logged_without_a_job_run(self):
        class Client:
            def get_sheet_titles(self, spreadsheet_id):
                raise RuntimeError("no credentials")

        from unittest.mock import patch

        with patch("google_integration.status.build_clients", return_value=(None, Client())):
            self.assertIsNone(fetch_sheet_titles(SHEET_URL, job_run=None))

        self.assertEqual(JobLog.objects.count(), 0)

    def test_a_broken_link_returns_nothing_without_blowing_up(self):
        titles = fetch_sheet_titles("https://example.com/not-a-sheet", job_run=self.job_run)

        self.assertIsNone(titles)

    def _fetch(self, client):
        from unittest.mock import patch

        with patch("google_integration.status.build_clients", return_value=(None, client)):
            return fetch_sheet_titles(SHEET_URL, job_run=self.job_run)
