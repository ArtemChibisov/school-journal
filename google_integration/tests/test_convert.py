from django.test import SimpleTestCase

from google_integration.convert import (
    ACTION_CONVERT,
    ACTION_SKIP_NAME_TAKEN,
    ARCHIVE_FOLDER_NAME,
    convert_folder,
    plan_conversion,
)
from google_integration.drive import MIME_FOLDER, MIME_SPREADSHEET, MIME_XLSX
from google_integration.fakes import FakeDriveClient


class ConversionTestCase(SimpleTestCase):
    def setUp(self):
        self.drive = FakeDriveClient()
        self.folder = self.drive.add_folder("1 модуль")

    def _add_xlsx(self, name):
        return self.drive.add_file(name, parent_id=self.folder, mime_type=MIME_XLSX)

    def _add_sheet(self, name):
        return self.drive.add_file(name, parent_id=self.folder, mime_type=MIME_SPREADSHEET)

    def _names(self, mime_type=None):
        return sorted(item["name"] for item in self.drive.list_children(self.folder, mime_type=mime_type))


class PlanConversionTests(ConversionTestCase):
    def test_plans_every_uploaded_file(self):
        self._add_xlsx("M1. 4А Class register.xlsx")
        self._add_xlsx("M1. 5Б Class register.xlsx")

        plan = plan_conversion(self.drive, self.folder)

        self.assertEqual(len(plan), 2)
        self.assertTrue(all(item["action"] == ACTION_CONVERT for item in plan))

    def test_drops_the_extension_from_the_new_name(self):
        self._add_xlsx("M1. 4А Class register.xlsx")

        self.assertEqual(plan_conversion(self.drive, self.folder)[0]["new_name"], "M1. 4А Class register")

    def test_keeps_a_name_that_has_no_extension(self):
        self._add_xlsx("M1. 7А Class register")

        self.assertEqual(plan_conversion(self.drive, self.folder)[0]["new_name"], "M1. 7А Class register")

    def test_ignores_files_that_are_already_google_sheets(self):
        self._add_sheet("M1. 10А Class register")
        self._add_xlsx("M1. 4А Class register.xlsx")

        plan = plan_conversion(self.drive, self.folder)

        self.assertEqual([item["name"] for item in plan], ["M1. 4А Class register.xlsx"])

    def test_ignores_subfolders(self):
        self.drive.add_folder(ARCHIVE_FOLDER_NAME, parent_id=self.folder)

        self.assertEqual(plan_conversion(self.drive, self.folder), [])

    def test_skips_when_a_sheet_with_the_target_name_exists(self):
        self._add_sheet("M1. 4А Class register")
        self._add_xlsx("M1. 4А Class register.xlsx")

        plan = plan_conversion(self.drive, self.folder)

        self.assertEqual(plan[0]["action"], ACTION_SKIP_NAME_TAKEN)

    def test_two_uploads_with_the_same_target_name_convert_only_once(self):
        self._add_xlsx("M1. 4А Class register.xlsx")
        self._add_xlsx("M1. 4А Class register")

        actions = sorted(item["action"] for item in plan_conversion(self.drive, self.folder))

        self.assertEqual(actions, [ACTION_CONVERT, ACTION_SKIP_NAME_TAKEN])


class ConvertFolderTests(ConversionTestCase):
    def test_dry_run_changes_nothing(self):
        self._add_xlsx("M1. 4А Class register.xlsx")
        before = self._names()

        result = convert_folder(self.drive, self.folder, dry_run=True)

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["converted"], 0)
        self.assertEqual(self._names(), before)

    def test_creates_a_native_sheet(self):
        self._add_xlsx("M1. 4А Class register.xlsx")

        convert_folder(self.drive, self.folder)

        self.assertEqual(self._names(mime_type=MIME_SPREADSHEET), ["M1. 4А Class register"])

    def test_original_is_moved_not_deleted(self):
        source = self._add_xlsx("M1. 4А Class register.xlsx")

        result = convert_folder(self.drive, self.folder)

        archive_id = result["archive_folder_id"]
        archived = self.drive.list_children(archive_id)
        self.assertEqual([item["id"] for item in archived], [source])
        self.assertEqual(self.drive.get_file(source)["trashed"], False)

    def test_folder_ends_up_with_only_google_sheets(self):
        self._add_xlsx("M1. 4А Class register.xlsx")
        self._add_xlsx("M1. 5Б Class register.xlsx")
        self._add_sheet("M1. 10А Class register")

        convert_folder(self.drive, self.folder)

        left = self.drive.list_children(self.folder)
        spreadsheets = [item for item in left if item["mimeType"] == MIME_SPREADSHEET]
        folders = [item for item in left if item["mimeType"] == MIME_FOLDER]
        self.assertEqual(len(spreadsheets), 3)
        self.assertEqual(len(left), len(spreadsheets) + len(folders))

    def test_counts_are_reported(self):
        self._add_xlsx("M1. 4А Class register.xlsx")
        self._add_sheet("M1. 5Б Class register")
        self._add_xlsx("M1. 5Б Class register.xlsx")

        result = convert_folder(self.drive, self.folder)

        self.assertEqual(result["total_xlsx"], 2)
        self.assertEqual(result["converted"], 1)
        self.assertEqual(result["skipped"], 1)

    def test_running_twice_converts_nothing_the_second_time(self):
        self._add_xlsx("M1. 4А Class register.xlsx")
        convert_folder(self.drive, self.folder)

        second = convert_folder(self.drive, self.folder)

        self.assertEqual(second["total_xlsx"], 0)
        self.assertEqual(second["converted"], 0)

    def test_archive_folder_is_reused(self):
        self._add_xlsx("M1. 4А Class register.xlsx")
        first = convert_folder(self.drive, self.folder)
        self._add_xlsx("M1. 5Б Class register.xlsx")

        second = convert_folder(self.drive, self.folder)

        self.assertEqual(first["archive_folder_id"], second["archive_folder_id"])

    def test_nothing_to_do_needs_no_archive_folder(self):
        self._add_sheet("M1. 10А Class register")

        result = convert_folder(self.drive, self.folder)

        self.assertEqual(result["total_xlsx"], 0)
        self.assertNotIn("archive_folder_id", result)
        self.assertEqual(self.drive.list_children(self.folder, mime_type=MIME_FOLDER), [])
