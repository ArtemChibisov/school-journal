from django.test import TestCase

from google_integration.fakes import FakeDriveClient, FakeSheetsClient
from google_integration.ids import folder_url, spreadsheet_url
from google_integration.preflight import (
    JOB_TYPE,
    STATUS_FAILED,
    STATUS_NOT_CONFIGURED,
    STATUS_OK,
    check_school_year_access,
    latest_preflight,
    run_google_access_preflight,
)
from jobs.models import JobLog, JobRun
from school_years.models import SchoolYear


class PreflightTestCase(TestCase):
    def setUp(self):
        self.drive = FakeDriveClient()
        self.sheets = FakeSheetsClient()

        self.register_root = self.drive.add_folder("Отчеты 4-11")
        self.roster_id = self.sheets.add_spreadsheet("roster", {"classes": [], "students": []})

        self.school_year = SchoolYear.objects.create(
            name="25/26",
            register_root_4_11_url=folder_url(self.register_root),
            class_roster_sheet_url=spreadsheet_url(self.roster_id),
        )

    def _items_by_field(self, result):
        return {item["field"]: item for item in result["items"]}


class CheckSchoolYearAccessTests(PreflightTestCase):
    def test_reports_ok_for_a_reachable_folder(self):
        result = check_school_year_access(self.school_year, drive=self.drive, sheets=self.sheets)

        item = self._items_by_field(result)["register_root_4_11_url"]
        self.assertEqual(item["status"], STATUS_OK)
        self.assertEqual(item["resource_id"], self.register_root)
        self.assertEqual(item["name"], "Отчеты 4-11")

    def test_reports_ok_for_a_reachable_spreadsheet(self):
        result = check_school_year_access(self.school_year, drive=self.drive, sheets=self.sheets)

        item = self._items_by_field(result)["class_roster_sheet_url"]
        self.assertEqual(item["status"], STATUS_OK)
        self.assertEqual(item["sheet_count"], 2)
        self.assertEqual(item["sheet_titles"], ["classes", "students"])

    def test_reports_not_configured_for_empty_links(self):
        result = check_school_year_access(self.school_year, drive=self.drive, sheets=self.sheets)

        item = self._items_by_field(result)["monitoring_sheet_url"]
        self.assertEqual(item["status"], STATUS_NOT_CONFIGURED)
        self.assertEqual(result["not_configured"], 12)

    def test_counts_add_up_to_every_check(self):
        result = check_school_year_access(self.school_year, drive=self.drive, sheets=self.sheets)

        self.assertEqual(result["ok"] + result["failed"] + result["not_configured"], result["total"])
        self.assertEqual(result["ok"], 2)

    def test_unreachable_folder_is_reported_without_technical_detail(self):
        self.school_year.docx_root_4_11_url = folder_url("missing-folder-id")
        self.school_year.save()

        result = check_school_year_access(self.school_year, drive=self.drive, sheets=self.sheets)

        item = self._items_by_field(result)["docx_root_4_11_url"]
        self.assertEqual(item["status"], STATUS_FAILED)
        self.assertEqual(item["error_category"], "not_found")
        self.assertIn("не найдены", item["user_message"])
        self.assertNotIn("missing-folder-id", item["user_message"])

    def test_a_broken_link_does_not_stop_the_remaining_checks(self):
        self.school_year.docx_root_4_11_url = folder_url("missing-folder-id")
        self.school_year.save()

        result = check_school_year_access(self.school_year, drive=self.drive, sheets=self.sheets)

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["ok"], 2)

    def test_folder_field_pointing_at_a_spreadsheet_is_a_failure(self):
        self.school_year.pdf_root_4_11_url = folder_url(self.drive.add_file("Таблица"))
        self.school_year.save()

        result = check_school_year_access(self.school_year, drive=self.drive, sheets=self.sheets)

        item = self._items_by_field(result)["pdf_root_4_11_url"]
        self.assertEqual(item["status"], STATUS_FAILED)
        self.assertIn("не на папку", item["user_message"])


class RunGoogleAccessPreflightTests(PreflightTestCase):
    def test_creates_a_successful_job_run(self):
        job_run = run_google_access_preflight(self.school_year, drive=self.drive, sheets=self.sheets)

        self.assertEqual(job_run.job_type, JOB_TYPE)
        self.assertEqual(job_run.status, JobRun.Status.SUCCESS)
        self.assertIsNotNone(job_run.finished_at)
        self.assertEqual(job_run.params_json["school_year_id"], self.school_year.pk)
        self.assertEqual(job_run.result_json["ok"], 2)

    def test_partial_status_when_some_links_fail(self):
        self.school_year.docx_root_4_11_url = folder_url("missing-folder-id")
        self.school_year.save()

        job_run = run_google_access_preflight(self.school_year, drive=self.drive, sheets=self.sheets)

        self.assertEqual(job_run.status, JobRun.Status.PARTIAL)

    def test_failed_status_when_nothing_is_reachable(self):
        year = SchoolYear.objects.create(
            name="26/27",
            register_root_4_11_url=folder_url("missing-folder-id"),
        )

        job_run = run_google_access_preflight(year, drive=self.drive, sheets=self.sheets)

        self.assertEqual(job_run.status, JobRun.Status.FAILED)

    def test_technical_detail_goes_to_job_logs(self):
        self.school_year.docx_root_4_11_url = folder_url("missing-folder-id")
        self.school_year.save()

        job_run = run_google_access_preflight(self.school_year, drive=self.drive, sheets=self.sheets)

        errors = JobLog.objects.filter(job_run=job_run, level=JobLog.Level.ERROR)
        self.assertEqual(errors.count(), 1)
        self.assertIn("missing-folder-id", errors.first().message)
        self.assertEqual(errors.first().context_json["category"], "not_found")

    def test_latest_preflight_finds_the_most_recent_run_for_the_year(self):
        run_google_access_preflight(self.school_year, drive=self.drive, sheets=self.sheets)
        second = run_google_access_preflight(self.school_year, drive=self.drive, sheets=self.sheets)

        self.assertEqual(latest_preflight(self.school_year.pk).pk, second.pk)

    def test_latest_preflight_is_none_for_another_year(self):
        run_google_access_preflight(self.school_year, drive=self.drive, sheets=self.sheets)
        other = SchoolYear.objects.create(name="26/27")

        self.assertIsNone(latest_preflight(other.pk))
