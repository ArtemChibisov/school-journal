from datetime import date

from django.test import TestCase

from google_integration.drive import MIME_SPREADSHEET, MIME_XLSX
from google_integration.fakes import FakeDriveClient
from google_integration.ids import folder_url
from jobs.models import JobRun
from journal_links.models import ClassSheetLink
from school_years.imports.registers import (
    JOB_TYPE,
    attach_registers,
    run_register_scan,
    scan_module_folders,
)
from school_years.models import ClassGroup, ClassRegister, SchoolModule, SchoolYear


class ScanTestCase(TestCase):
    def setUp(self):
        self.drive = FakeDriveClient()
        self.primary_folder = self.drive.add_folder("1 модуль 0-3")
        self.secondary_folder = self.drive.add_folder("1 модуль 4-11")

        self.school_year = SchoolYear.objects.create(name="25/26")
        self.module = SchoolModule.objects.create(
            school_year=self.school_year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 10, 25),
            register_folder_0_3_url=folder_url(self.primary_folder),
            register_folder_4_11_url=folder_url(self.secondary_folder),
        )

        self.primary_class = self._class("0M", 0)
        self.secondary_class = self._class("11К", 11)

    def _class(self, class_code, grade_number):
        return ClassGroup.objects.create(
            school_year=self.school_year,
            class_code=class_code,
            grade_number=grade_number,
            tutor_name="Тьютор",
        )

    def _add_journal(self, folder, name, mime_type=MIME_SPREADSHEET):
        return self.drive.add_file(name, parent_id=folder, mime_type=mime_type)


class ScanModuleFoldersTests(ScanTestCase):
    def test_matches_a_journal_in_each_band(self):
        self._add_journal(self.primary_folder, "0М-Модуль 1-Class register template")
        self._add_journal(self.secondary_folder, "M1. 11К/11C Class register")

        scan = scan_module_folders(self.module, drive=self.drive)

        self.assertEqual(scan["matched_total"], 2)
        self.assertEqual(scan["missing_total"], 0)
        self.assertEqual(scan["unmatched_files_total"], 0)

    def test_a_class_looks_only_in_the_folder_of_its_band(self):
        self._add_journal(self.primary_folder, "M1. 11К/11C Class register")

        scan = scan_module_folders(self.module, drive=self.drive)

        self.assertEqual(scan["matched_total"], 0)
        self.assertEqual(scan["unmatched_files_total"], 1)

    def test_reports_a_class_without_a_journal(self):
        self._add_journal(self.primary_folder, "0М-Модуль 1-Class register template")

        scan = scan_module_folders(self.module, drive=self.drive)

        self.assertEqual([group.class_code for group in scan["missing"]], ["11К"])

    def test_reports_a_file_whose_class_is_unreadable(self):
        self._add_journal(self.primary_folder, "Черновик.xlsx")

        scan = scan_module_folders(self.module, drive=self.drive)

        self.assertEqual(scan["unmatched_files_total"], 1)
        self.assertIn("не удалось определить класс", scan["unmatched_files"][0]["reason"])

    def test_second_file_for_the_same_class_is_reported_not_overwritten(self):
        self._add_journal(self.primary_folder, "0М-Модуль 1-Class register template")
        self._add_journal(self.primary_folder, "0M копия Class register")

        scan = scan_module_folders(self.module, drive=self.drive)

        self.assertEqual(scan["matched_total"], 1)
        self.assertEqual(scan["unmatched_files_total"], 1)
        self.assertIn("уже найден другой файл", scan["unmatched_files"][0]["reason"])

    def test_uploaded_excel_files_are_ignored(self):
        self._add_journal(self.primary_folder, "0М-Модуль 1.xlsx", mime_type=MIME_XLSX)

        scan = scan_module_folders(self.module, drive=self.drive)

        self.assertEqual(scan["matched_total"], 0)

    def test_missing_folder_link_is_reported_without_stopping_the_other_band(self):
        self.module.register_folder_0_3_url = ""
        self.module.save()
        self._add_journal(self.secondary_folder, "M1. 11К/11C Class register")

        scan = scan_module_folders(self.module, drive=self.drive)

        self.assertEqual(scan["matched_total"], 1)
        primary = next(band for band in scan["bands"] if band["band"] == "0-3")
        self.assertEqual(primary["status"], "not_configured")

    def test_inactive_classes_are_not_expected_to_have_journals(self):
        self.secondary_class.is_active = False
        self.secondary_class.save()
        self._add_journal(self.primary_folder, "0М-Модуль 1-Class register template")

        scan = scan_module_folders(self.module, drive=self.drive)

        self.assertEqual(scan["classes_total"], 1)
        self.assertEqual(scan["missing_total"], 0)


class AttachRegistersTests(ScanTestCase):
    def setUp(self):
        super().setUp()
        self._add_journal(self.primary_folder, "0М-Модуль 1-Class register template")
        self._add_journal(self.secondary_folder, "M1. 11К/11C Class register")

    def test_creates_a_register_per_match(self):
        scan = scan_module_folders(self.module, drive=self.drive)

        summary = attach_registers(self.module, scan)

        self.assertEqual(summary["created"], 2)
        self.assertEqual(ClassRegister.objects.count(), 2)
        self.assertEqual(
            ClassRegister.objects.get(class_group=self.primary_class).status,
            ClassRegister.Status.CREATED,
        )

    def test_second_run_changes_nothing(self):
        scan = scan_module_folders(self.module, drive=self.drive)
        attach_registers(self.module, scan)

        summary = attach_registers(self.module, scan_module_folders(self.module, drive=self.drive))

        self.assertEqual(summary, {"created": 0, "updated": 0, "unchanged": 2})

    def test_bridges_to_the_legacy_class_sheet_link(self):
        scan = scan_module_folders(self.module, drive=self.drive)

        attach_registers(self.module, scan)

        link = ClassSheetLink.objects.get(class_code="0M")
        self.assertTrue(link.is_active)
        self.assertEqual(link.google_sheet_url, scan["matched"][self.primary_class.pk]["url"])

    def test_legacy_link_follows_a_replaced_journal(self):
        scan = scan_module_folders(self.module, drive=self.drive)
        attach_registers(self.module, scan)
        for item in self.drive.list_children(self.primary_folder):
            self.drive.trash_file(item["id"])
        self._add_journal(self.primary_folder, "0М-Модуль 1-Class register template")

        new_scan = scan_module_folders(self.module, drive=self.drive)
        summary = attach_registers(self.module, new_scan)

        self.assertEqual(summary["updated"], 1)
        self.assertEqual(ClassSheetLink.objects.filter(class_code="0M").count(), 1)
        self.assertEqual(
            ClassSheetLink.objects.get(class_code="0M").google_sheet_url,
            new_scan["matched"][self.primary_class.pk]["url"],
        )


class RunRegisterScanTests(ScanTestCase):
    def test_success_when_every_class_has_a_journal(self):
        self._add_journal(self.primary_folder, "0М-Модуль 1-Class register template")
        self._add_journal(self.secondary_folder, "M1. 11К/11C Class register")

        job_run = run_register_scan(self.module, drive=self.drive)

        self.assertEqual(job_run.job_type, JOB_TYPE)
        self.assertEqual(job_run.status, JobRun.Status.SUCCESS)
        self.assertEqual(job_run.result_json["attached"]["created"], 2)

    def test_partial_when_a_class_is_missing_its_journal(self):
        self._add_journal(self.primary_folder, "0М-Модуль 1-Class register template")

        job_run = run_register_scan(self.module, drive=self.drive)

        self.assertEqual(job_run.status, JobRun.Status.PARTIAL)

    def test_failed_when_nothing_matched(self):
        job_run = run_register_scan(self.module, drive=self.drive)

        self.assertEqual(job_run.status, JobRun.Status.FAILED)

    def test_dry_run_reports_without_attaching(self):
        self._add_journal(self.primary_folder, "0М-Модуль 1-Class register template")
        self._add_journal(self.secondary_folder, "M1. 11К/11C Class register")

        job_run = run_register_scan(self.module, drive=self.drive, dry_run=True)

        self.assertEqual(job_run.result_json["matched_total"], 2)
        self.assertEqual(ClassRegister.objects.count(), 0)
        self.assertEqual(ClassSheetLink.objects.count(), 0)

    def test_result_lists_matches_for_the_screen(self):
        self._add_journal(self.primary_folder, "0М-Модуль 1-Class register template")

        job_run = run_register_scan(self.module, drive=self.drive)

        matched = job_run.result_json["matched"]
        self.assertEqual(matched[0]["class_code"], "0M")
        self.assertIn("Class register", matched[0]["file_name"])
