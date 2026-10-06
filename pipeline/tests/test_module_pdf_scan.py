from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from google_integration.drive import MIME_DOCX, MIME_PDF
from google_integration.fakes import FakeDriveClient
from jobs.models import JobRun
from journal_links.models import ClassSheetLink
from pipeline.module_pdf_scan import JOB_TYPE, run_module_pdf_scan_job
from school_years.models import ClassGroup, SchoolModule, SchoolYear


class ModulePdfScanTests(TestCase):
    def setUp(self):
        self.year = SchoolYear.objects.create(name="26/27", is_active=True)
        self.drive = FakeDriveClient()
        self.docx_root = self.drive.add_folder("DOCX 0-3")
        self.pdf_root = self.drive.add_folder("PDF 0-3")
        self.module = SchoolModule.objects.create(
            school_year=self.year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 10, 2),
            docx_folder_0_3_url=f"https://drive.google.com/drive/folders/{self.docx_root}",
            pdf_folder_0_3_url=f"https://drive.google.com/drive/folders/{self.pdf_root}",
            docx_folder_4_11_url="https://drive.google.com/drive/folders/seniordocx111",
            pdf_folder_4_11_url="https://drive.google.com/drive/folders/seniorpdf1111",
        )

    def _class(self, class_code, grade_number=2):
        ClassGroup.objects.create(
            school_year=self.year,
            class_code=class_code,
            grade_number=grade_number,
            tutor_name="Тьютор",
        )
        return ClassSheetLink.objects.create(
            class_code=class_code,
            subject_name="",
            google_sheet_url=f"https://docs.google.com/spreadsheets/d/{class_code.lower()}aaaaaaaaaa/edit",
            is_active=True,
        )

    def _put(self, root, class_code, names, mime_type):
        module_folder = self.drive.find_child(root, "1 модуль/module")
        module_id = module_folder["id"] if module_folder else self.drive.add_folder(
            "1 модуль/module", parent_id=root
        )
        class_folder = self.drive.find_child(module_id, class_code)
        class_id = class_folder["id"] if class_folder else self.drive.add_folder(
            class_code, parent_id=module_id
        )
        for name in names:
            self.drive.add_file(name, parent_id=class_id, mime_type=mime_type)
        return class_id

    def _run(self, **kwargs):
        return run_module_pdf_scan_job(module=self.module, drive=self.drive, **kwargs)

    def _row(self, job_run, class_code):
        return next(r for r in job_run.result_json["classes"] if r["class_code"] == class_code)

    def test_a_class_with_reports_is_ready_and_counted(self):
        self._class("2А")
        self._put(self.docx_root, "2А", ["2А Иванов.docx", "2А Петров.docx"], MIME_DOCX)

        job_run = self._run()

        row = self._row(job_run, "2А")
        self.assertEqual(job_run.job_type, JOB_TYPE)
        self.assertTrue(row["ready"])
        self.assertEqual(row["docx"], 2)
        self.assertEqual(row["pdf"], 0)
        self.assertEqual(row["reason"], "Готов к сохранению в PDF")

    def test_a_class_nobody_has_built_yet_says_which_step_is_missing(self):
        self._class("2А")

        row = self._row(self._run(), "2А")

        self.assertFalse(row["ready"])
        self.assertIn("отчёты не собирали", row["reason"])

    def test_an_empty_class_folder_is_told_apart_from_a_missing_one(self):
        self._class("2А")
        self._put(self.docx_root, "2А", [], MIME_DOCX)

        row = self._row(self._run(), "2А")

        self.assertFalse(row["ready"])
        self.assertIn("DOCX-отчётов в ней нет", row["reason"])

    def test_pdf_already_made_is_said_plainly(self):
        self._class("2А")
        self._put(self.docx_root, "2А", ["2А Иванов.docx"], MIME_DOCX)
        self._put(self.pdf_root, "2А", ["2А Иванов.pdf"], MIME_PDF)

        row = self._row(self._run(), "2А")

        self.assertTrue(row["ready"])
        self.assertEqual(row["pdf"], 1)
        self.assertIn("повторный запуск их заменит", row["reason"])

    def test_a_half_converted_class_says_how_many(self):
        self._class("2А")
        self._put(self.docx_root, "2А", ["а.docx", "б.docx", "в.docx"], MIME_DOCX)
        self._put(self.pdf_root, "2А", ["а.pdf"], MIME_PDF)

        row = self._row(self._run(), "2А")

        self.assertEqual(row["reason"], "PDF есть не для всех: 1 из 3")

    def test_a_missing_pdf_folder_is_reported_before_anyone_presses_save(self):
        self.module.pdf_folder_0_3_url = ""
        self.module.save()
        self._class("2А")
        self._put(self.docx_root, "2А", ["2А Иванов.docx"], MIME_DOCX)

        row = self._row(self._run(), "2А")

        self.assertFalse(row["ready"])
        self.assertIn("Не указана папка PDF 0-3", row["reason"])

    def test_the_check_creates_nothing(self):
        self._class("2А")
        self._class("3А", grade_number=3)
        self._put(self.docx_root, "2А", ["2А Иванов.docx"], MIME_DOCX)
        before = set(self.drive.files)

        self._run()

        self.assertEqual(set(self.drive.files), before)

    def test_the_summary_adds_up(self):
        self._class("2А")
        self._class("3А", grade_number=3)
        self._put(self.docx_root, "2А", ["а.docx", "б.docx"], MIME_DOCX)

        summary = self._run().result_json["summary"]

        self.assertEqual(summary["classes_total"], 2)
        self.assertEqual(summary["classes_ready"], 1)
        self.assertEqual(summary["classes_not_ready"], 1)
        self.assertEqual(summary["docx_found"], 2)


class ModulePdfPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("zavuch", password="x")
        self.user.user_permissions.add(
            *Permission.objects.filter(codename__in=["view_jobrun", "run_full_pipeline"])
        )
        self.client.force_login(self.user)
        self.year = SchoolYear.objects.create(name="26/27", is_active=True)
        self.module = SchoolModule.objects.create(
            school_year=self.year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 10, 2),
        )
        self.url = reverse("pipeline:module_pdf_reports")
        # The real enqueue starts a thread that talks to Drive; these tests are about which
        # of the two buttons was pressed.
        self.started: list[tuple[str, dict]] = []
        for name in ("enqueue_module_pdf_scan_job", "enqueue_module_pdf_reports_job"):
            patcher = patch(
                f"pipeline.views.{name}",
                side_effect=self._record(name),
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def _record(self, name):
        def enqueue(**kwargs):
            self.started.append((name, kwargs))
            return JobRun.objects.create(
                job_type=JOB_TYPE if "scan" in name else "module_pdf_reports",
                status=JobRun.Status.PENDING,
            )

        return enqueue

    def test_the_page_opens_on_its_own(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Проверить, что готово")
        self.assertContains(response, "Сохранить в PDF")

    def test_the_default_button_only_checks(self):
        """Pressing Enter in the form must not start converting anything."""

        response = self.client.post(self.url, {"module": self.module.pk})

        self.assertEqual(response.status_code, 302)
        self.assertEqual([name for name, _ in self.started], ["enqueue_module_pdf_scan_job"])

    def test_saving_is_a_separate_button(self):
        response = self.client.post(self.url, {"module": self.module.pk, "save_pdf": "1"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual([name for name, _ in self.started], ["enqueue_module_pdf_reports_job"])

    def test_the_ticked_classes_are_passed_on(self):
        link = ClassSheetLink.objects.create(
            class_code="2А",
            subject_name="",
            google_sheet_url="https://docs.google.com/spreadsheets/d/2aaaaaaaaaaaaaaaa/edit",
            is_active=True,
        )

        self.client.post(self.url, {"module": self.module.pk, "class_links": [link.pk]})

        self.assertEqual(self.started[0][1]["class_codes"], ["2А"])

    def test_without_the_run_permission_nothing_starts(self):
        self.user.user_permissions.remove(Permission.objects.get(codename="run_full_pipeline"))
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)

        response = self.client.post(self.url, {"module": self.module.pk})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.started, [])
