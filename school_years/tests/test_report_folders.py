from datetime import date

from django.test import SimpleTestCase, TestCase

from google_integration.fakes import FakeDriveClient
from school_years.models import SchoolModule, SchoolYear
from school_years.report_folders import module_folder_name, resolve_report_folder


class ModuleFolderNameTests(SimpleTestCase):
    def test_a_numbered_module_reads_like_the_folders_the_school_keeps(self):
        module = SchoolModule(number=SchoolModule.Number.MODULE_1)

        self.assertEqual(module_folder_name(module), "1 модуль/module")

    def test_the_final_module_is_named_rather_than_numbered(self):
        module = SchoolModule(number=SchoolModule.Number.FINAL)

        self.assertEqual(module_folder_name(module), "Итоговый модуль/final")

    def test_a_title_the_school_set_wins(self):
        module = SchoolModule(number=SchoolModule.Number.MODULE_2, title="2 модуль — зима")

        self.assertEqual(module_folder_name(module), "2 модуль — зима")


class ResolveReportFolderTests(TestCase):
    def setUp(self):
        self.year = SchoolYear.objects.create(name="26/27", is_active=True)
        self.module = SchoolModule.objects.create(
            school_year=self.year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 10, 2),
        )
        self.drive = FakeDriveClient()
        self.root = self.drive.add_folder("DOCX 0-3")
        self.root_url = f"https://drive.google.com/drive/folders/{self.root}"

    def _resolve(self, class_code="1А"):
        return resolve_report_folder(
            self.drive, root_folder_url=self.root_url, module=self.module, class_code=class_code
        )

    def test_both_levels_are_created(self):
        folders = self._resolve()

        module_folder = self.drive.find_child(self.root, "1 модуль/module")
        self.assertIsNotNone(module_folder)
        self.assertEqual(folders.module_folder_id, module_folder["id"])
        self.assertEqual(
            self.drive.find_child(module_folder["id"], "1А")["id"], folders.class_folder_id
        )

    def test_running_again_returns_the_same_folder(self):
        first = self._resolve()
        second = self._resolve()

        self.assertEqual(first, second)
        self.assertEqual(len(self.drive.list_children(self.root)), 1)

    def test_an_existing_module_folder_is_reused(self):
        existing = self.drive.add_folder("1 модуль/module", parent_id=self.root)

        self._resolve()

        self.assertEqual(len(self.drive.list_children(self.root)), 1)
        self.assertIsNotNone(self.drive.find_child(existing, "1А"))

    def test_each_class_gets_its_own_folder_under_one_module(self):
        first = self._resolve("1А")
        second = self._resolve("2Б")

        self.assertNotEqual(first.class_folder_id, second.class_folder_id)
        self.assertEqual(first.module_folder_id, second.module_folder_id)
        module_folder = self.drive.find_child(self.root, "1 модуль/module")
        self.assertEqual(len(self.drive.list_children(module_folder["id"])), 2)
