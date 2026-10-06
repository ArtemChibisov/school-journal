from datetime import date

from django.test import TestCase

from google_integration.drive import MIME_DOCX, MIME_PDF
from google_integration.fakes import FakeDriveClient
from jobs.models import JobLog, JobRun
from journal_links.models import ClassSheetLink
from pipeline.module_pdf_reports import JOB_TYPE, run_module_pdf_reports_job
from school_years.models import ClassGroup, SchoolModule, SchoolYear


def _sheet_url(suffix):
    return f"https://docs.google.com/spreadsheets/d/{suffix}aaaaaaaaaaaaaaa/edit"


class ModulePdfReportsTests(TestCase):
    def setUp(self):
        self.year = SchoolYear.objects.create(name="26/27", is_active=True)
        self.drive = FakeDriveClient()
        self.docx_root = self.drive.add_folder("DOCX 0-3")
        self.pdf_root = self.drive.add_folder("PDF 0-3")
        self.senior_docx_root = self.drive.add_folder("DOCX 4-11")
        self.senior_pdf_root = self.drive.add_folder("PDF 4-11")
        self.module = SchoolModule.objects.create(
            school_year=self.year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 10, 2),
            docx_folder_0_3_url=self._url(self.docx_root),
            pdf_folder_0_3_url=self._url(self.pdf_root),
            docx_folder_4_11_url=self._url(self.senior_docx_root),
            pdf_folder_4_11_url=self._url(self.senior_pdf_root),
        )

    @staticmethod
    def _url(folder_id):
        return f"https://drive.google.com/drive/folders/{folder_id}"

    def _class(self, class_code, grade_number):
        ClassGroup.objects.create(
            school_year=self.year,
            class_code=class_code,
            grade_number=grade_number,
            tutor_name="Тьютор",
        )
        return ClassSheetLink.objects.create(
            class_code=class_code,
            subject_name="",
            google_sheet_url=_sheet_url(class_code.lower()),
            is_active=True,
        )

    def _reports(self, class_code, names, root=None):
        """Put built DOCX where a reports run would have left them."""

        module_folder = self.drive.find_child(root or self.docx_root, "1 модуль/module")
        if module_folder is None:
            module_folder = {"id": self.drive.add_folder("1 модуль/module", parent_id=root or self.docx_root)}
        class_folder = self.drive.find_child(module_folder["id"], class_code)
        if class_folder is None:
            class_folder = {"id": self.drive.add_folder(class_code, parent_id=module_folder["id"])}
        for name in names:
            self.drive.add_file(name, parent_id=class_folder["id"], mime_type=MIME_DOCX)
        return class_folder["id"]

    def _run(self, **kwargs):
        return run_module_pdf_reports_job(module=self.module, drive=self.drive, **kwargs)

    def _pdf_class_folder(self, class_code, root=None):
        module_folder = self.drive.find_child(root or self.pdf_root, "1 модуль/module")
        if module_folder is None:
            return None
        return self.drive.find_child(module_folder["id"], class_code)

    def test_every_report_in_the_class_becomes_a_pdf(self):
        self._class("2А", 2)
        self._reports("2А", ["2А Иванов.docx", "2А Петров.docx"])

        job_run = self._run()

        self.assertEqual(job_run.job_type, JOB_TYPE)
        self.assertEqual(job_run.status, JobRun.Status.SUCCESS)
        self.assertEqual(job_run.result_json["summary"]["docx_created"], 2)
        folder = self._pdf_class_folder("2А")
        names = sorted(item["name"] for item in self.drive.list_children(folder["id"]))
        self.assertEqual(names, ["2А Иванов.pdf", "2А Петров.pdf"])
        self.assertTrue(
            all(item["mimeType"] == MIME_PDF for item in self.drive.list_children(folder["id"]))
        )

    def test_the_pdf_tree_mirrors_the_docx_one(self):
        """PDF 0-3 / 1 модуль/module / 2А - the same shape, one root over."""

        self._class("2А", 2)
        self._reports("2А", ["2А Иванов.docx"])

        self._run()

        module_folder = self.drive.find_child(self.pdf_root, "1 модуль/module")
        self.assertIsNotNone(module_folder)
        self.assertIsNotNone(self.drive.find_child(module_folder["id"], "2А"))

    def test_the_docx_are_left_where_they_were(self):
        self._class("2А", 2)
        source = self._reports("2А", ["2А Иванов.docx"])

        self._run()

        self.assertEqual(
            [item["name"] for item in self.drive.list_children(source)], ["2А Иванов.docx"]
        )

    def test_the_two_halves_of_the_school_keep_their_own_trees(self):
        self._class("2А", 2)
        self._class("7А", 7)
        self._reports("2А", ["2А Иванов.docx"])
        self._reports("7А", ["7А Сидоров.docx"], root=self.senior_docx_root)

        self._run()

        self.assertIsNotNone(self._pdf_class_folder("2А"))
        self.assertIsNotNone(self._pdf_class_folder("7А", root=self.senior_pdf_root))
        self.assertIsNone(self._pdf_class_folder("7А"))

    def test_the_pdf_folder_is_opened_to_the_school(self):
        self.year.reports_access_group_email = "reports@projector-school.info"
        self.year.save()
        self._class("2А", 2)
        self._reports("2А", ["2А Иванов.docx"])

        self._run()

        module_folder = self.drive.find_child(self.pdf_root, "1 модуль/module")
        permissions = self.drive.permissions[module_folder["id"]]
        self.assertEqual(permissions[0]["emailAddress"], "reports@projector-school.info")

    def test_a_class_with_no_reports_yet_is_skipped_with_a_reason(self):
        self._class("2А", 2)

        job_run = self._run()

        row = job_run.result_json["classes"][0]
        self.assertEqual(row["status"], "skipped")
        self.assertIn("сначала соберите", row["reason"])
        self.assertEqual(job_run.result_json["summary"]["docx_created"], 0)

    def test_pressing_it_before_the_reports_exist_creates_nothing(self):
        """Wrong button, wrong order - and Drive is left exactly as it was."""

        self._class("2А", 2)
        self._class("7А", 7)
        before = set(self.drive.files)

        self._run()

        self.assertEqual(set(self.drive.files), before)
        self.assertEqual(self.drive.list_children(self.pdf_root), [])
        self.assertEqual(self.drive.list_children(self.docx_root), [])

    def test_asking_where_the_reports_are_does_not_build_the_folder(self):
        """A class whose DOCX folder was never made is read, not created."""

        self._class("2А", 2)
        self._reports("2А", ["2А Иванов.docx"])
        self._class("3А", 3)

        self._run()

        module_folder = self.drive.find_child(self.docx_root, "1 модуль/module")
        self.assertIsNone(self.drive.find_child(module_folder["id"], "3А"))
        self.assertIsNone(self._pdf_class_folder("3А"))

    def test_without_a_pdf_folder_the_class_says_so_rather_than_guessing(self):
        self.module.pdf_folder_0_3_url = ""
        self.module.save()
        self._class("2А", 2)
        self._reports("2А", ["2А Иванов.docx"])

        job_run = self._run()

        row = job_run.result_json["classes"][0]
        self.assertEqual(row["status"], "skipped")
        self.assertIn("Не указана папка PDF 0-3", row["reason"])

    def test_only_the_chosen_classes_are_saved(self):
        self._class("2А", 2)
        self._class("3А", 3)
        self._reports("2А", ["2А Иванов.docx"])
        self._reports("3А", ["3А Орлов.docx"])

        self._run(class_codes=["3А"])

        self.assertIsNone(self._pdf_class_folder("2А"))
        self.assertIsNotNone(self._pdf_class_folder("3А"))

    def test_running_again_replaces_the_pdfs_rather_than_doubling_them(self):
        self._class("2А", 2)
        self._reports("2А", ["2А Иванов.docx"])

        self._run()
        self._run()

        folder = self._pdf_class_folder("2А")
        self.assertEqual(len(self.drive.list_children(folder["id"])), 1)

    def test_one_report_google_refuses_does_not_cost_the_class_the_rest(self):
        self._class("2А", 2)
        self._reports("2А", ["2А Иванов.docx", "2А Петров.docx"])
        real_export = self.drive.export_file
        seen: list[str] = []

        def flaky(file_id, mime_type):
            seen.append(file_id)
            if len(seen) == 1:
                from google_integration.errors import CATEGORY_NOT_FOUND, GoogleIntegrationError

                raise GoogleIntegrationError(CATEGORY_NOT_FOUND, "export refused")
            return real_export(file_id, mime_type)

        self.drive.export_file = flaky
        job_run = self._run()

        self.assertEqual(job_run.result_json["summary"]["docx_created"], 1)
        self.assertEqual(job_run.status, JobRun.Status.PARTIAL)
        self.assertIn("2А Иванов.docx", job_run.result_json["classes"][0]["reason"])
        self.assertTrue(
            JobLog.objects.filter(
                job_run=job_run, message="Could not save a report as PDF"
            ).exists()
        )
