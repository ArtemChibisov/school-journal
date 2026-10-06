from datetime import date
from unittest.mock import patch

from django.test import TestCase

from google_integration.errors import CATEGORY_PERMISSION_DENIED, GoogleIntegrationError
from google_integration.fakes import FakeDriveClient
from jobs.models import JobLog, JobRun
from journal_links.models import ClassSheetLink
from pipeline.module_review_reports import JOB_TYPE, run_module_review_reports_job
from school_years.models import ClassGroup, SchoolModule, SchoolYear

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
JUNIOR_FOLDER = "https://drive.google.com/drive/folders/junior0303030303"
SENIOR_FOLDER = "https://drive.google.com/drive/folders/senior4114114114"


def _sheet_url(suffix):
    return f"https://docs.google.com/spreadsheets/d/{suffix}aaaaaaaaaaaaaaa/edit"


class ModuleReviewReportsTests(TestCase):
    def setUp(self):
        self.year = SchoolYear.objects.create(name="26/27", is_active=True)
        self.module = SchoolModule.objects.create(
            school_year=self.year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 10, 2),
            docx_folder_0_3_url=JUNIOR_FOLDER,
            docx_folder_4_11_url=SENIOR_FOLDER,
        )
        self.calls = []
        self.drive = FakeDriveClient()
        self.junior_root = self.drive.add_folder("DOCX 0-3")
        self.senior_root = self.drive.add_folder("DOCX 4-11")
        self.module.docx_folder_0_3_url = f"https://drive.google.com/drive/folders/{self.junior_root}"
        self.module.docx_folder_4_11_url = f"https://drive.google.com/drive/folders/{self.senior_root}"
        self.module.save()

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

    def _child(self, status=JobRun.Status.SUCCESS, docx=3, uploaded=3, files=None):
        def runner(**kwargs):
            self.calls.append(kwargs)
            built = [{"name": name} for name in (files or [])]
            return JobRun.objects.create(
                job_type="prepare_student_review_reports",
                status=status,
                result_json={
                    "summary": {"docx_created": docx, "uploaded_success": uploaded},
                    "files": built,
                },
            )

        return runner

    def _uploads(self, folder_name, names):
        """Put files in a class folder the way a previous run would have left them."""

        module_folder = self.drive.find_child(self.junior_root, "1 модуль/module")
        class_folder = self.drive.find_child(module_folder["id"], folder_name)
        for name in names:
            self.drive.add_file(name, parent_id=class_folder["id"], mime_type=DOCX_MIME)
        return class_folder["id"]

    def _run(self, runner=None, **kwargs):
        with patch(
            "pipeline.module_review_reports.run_prepare_student_review_reports_job",
            side_effect=runner or self._child(),
        ):
            return run_module_review_reports_job(module=self.module, drive=self.drive, **kwargs)

    def test_every_class_is_built_in_one_run(self):
        self._class("2А", 2)
        self._class("7А", 7)

        job_run = self._run()

        self.assertEqual(job_run.job_type, JOB_TYPE)
        self.assertEqual(job_run.status, JobRun.Status.SUCCESS)
        self.assertEqual(job_run.result_json["summary"]["classes_done"], 2)
        self.assertEqual(job_run.result_json["summary"]["docx_created"], 6)

    def test_each_class_gets_a_folder_of_its_own_under_the_module(self):
        """DOCX 0-3 / 1 модуль/module / 2А - both levels made on the way."""

        self._class("2А", 2)

        self._run()

        module_folder = self.drive.find_child(self.junior_root, "1 модуль/module")
        self.assertIsNotNone(module_folder)
        class_folder = self.drive.find_child(module_folder["id"], "2А")
        self.assertIsNotNone(class_folder)
        self.assertIn(class_folder["id"], self.calls[0]["drive_folder_url"])

    def test_the_two_halves_of_the_school_keep_their_own_trees(self):
        self._class("2А", 2)
        self._class("7А", 7)

        self._run()

        junior_module = self.drive.find_child(self.junior_root, "1 модуль/module")
        senior_module = self.drive.find_child(self.senior_root, "1 модуль/module")
        self.assertIsNotNone(self.drive.find_child(junior_module["id"], "2А"))
        self.assertIsNotNone(self.drive.find_child(senior_module["id"], "7А"))

    def test_a_second_run_reuses_the_folders_instead_of_duplicating_them(self):
        self._class("2А", 2)

        self._run()
        self._run()

        module_folders = [
            item for item in self.drive.list_children(self.junior_root) if item["name"] == "1 модуль/module"
        ]
        self.assertEqual(len(module_folders), 1)
        class_folders = [
            item for item in self.drive.list_children(module_folders[0]["id"]) if item["name"] == "2А"
        ]
        self.assertEqual(len(class_folders), 1)

    def test_the_module_supplies_the_number_the_dates_and_the_year(self):
        """Everything the first page prints - none of it typed into a form."""

        self._class("7А", 7)

        self._run()

        self.assertEqual(self.calls[0]["module_number"], 1)
        self.assertEqual(self.calls[0]["module_dates"], "01.09 – 02.10.2026")
        self.assertEqual(self.calls[0]["school_year"], "2026-2027")

    def test_the_module_folder_is_opened_to_the_school(self):
        """One share for the whole module - the class folders and the files inherit it."""

        self.year.reports_access_group_email = "reports@projector-school.info"
        self.year.save()
        self._class("2А", 2)
        self._class("3А", 3)

        self._run()

        module_folder = self.drive.find_child(self.junior_root, "1 модуль/module")
        permissions = self.drive.permissions[module_folder["id"]]
        self.assertEqual(len(permissions), 1)
        self.assertEqual(permissions[0]["emailAddress"], "reports@projector-school.info")

    def test_each_half_of_the_school_gets_its_own_share(self):
        self.year.reports_access_group_email = "reports@projector-school.info"
        self.year.save()
        self._class("2А", 2)
        self._class("7А", 7)

        self._run()

        for root in (self.junior_root, self.senior_root):
            module_folder = self.drive.find_child(root, "1 модуль/module")
            self.assertEqual(len(self.drive.permissions[module_folder["id"]]), 1)

    def test_without_an_address_the_run_says_so_and_carries_on(self):
        self._class("2А", 2)

        job_run = self._run()

        self.assertEqual(job_run.result_json["summary"]["classes_done"], 1)
        self.assertTrue(
            JobLog.objects.filter(
                job_run=job_run, message="No address to share the report folder with"
            ).exists()
        )

    def test_a_refused_share_does_not_cost_anyone_their_reports(self):
        self.year.reports_access_group_email = "reports@projector-school.info"
        self.year.save()
        self._class("2А", 2)

        with patch(
            "pipeline.module_review_reports.ensure_shared",
            side_effect=GoogleIntegrationError(CATEGORY_PERMISSION_DENIED, "no rights"),
        ):
            job_run = self._run()

        self.assertEqual(job_run.result_json["summary"]["classes_done"], 1)
        self.assertTrue(
            JobLog.objects.filter(
                job_run=job_run, message="Could not share the report folder"
            ).exists()
        )

    def test_a_report_this_run_did_not_build_is_named_on_the_screen(self):
        """A surname corrected in the журнал leaves the old report beside the right one."""

        self._class("2А", 2)
        self._run(runner=self._child(files=["2А Мирон Грицай.docx"]))
        self._uploads("2А", ["2А Мирон Грiцай.docx"])

        job_run = self._run(runner=self._child(files=["2А Мирон Грицай.docx"]))

        row = job_run.result_json["classes"][0]
        self.assertEqual(row["extra_files"], ["2А Мирон Грiцай.docx"])
        self.assertEqual(job_run.result_json["summary"]["extra_found"], 1)
        self.assertNotIn("extra_removed", row)

    def test_nothing_is_removed_unless_it_was_asked_for(self):
        self._class("2А", 2)
        self._run(runner=self._child(files=["2А Мирон Грицай.docx"]))
        class_folder = self._uploads("2А", ["2А Старый отчёт.docx"])

        self._run(runner=self._child(files=["2А Мирон Грицай.docx"]))

        self.assertEqual(len(self.drive.list_children(class_folder)), 1)

    def test_the_leftovers_go_to_the_bin_when_asked(self):
        self._class("2А", 2)
        self._run(runner=self._child(files=["2А Мирон Грицай.docx"]))
        class_folder = self._uploads("2А", ["2А Старый отчёт.docx"])

        job_run = self._run(
            runner=self._child(files=["2А Мирон Грицай.docx"]), remove_extra_files=True
        )

        self.assertEqual(self.drive.list_children(class_folder), [])
        self.assertEqual(job_run.result_json["summary"]["extra_removed"], 1)
        self.assertEqual(
            job_run.result_json["classes"][0]["extra_removed"], ["2А Старый отчёт.docx"]
        )

    def test_the_reports_this_run_built_are_never_touched(self):
        self._class("2А", 2)
        self._run(runner=self._child(files=["2А Мирон Грицай.docx"]))
        class_folder = self._uploads("2А", ["2А Мирон Грицай.docx"])

        self._run(runner=self._child(files=["2А Мирон Грицай.docx"]), remove_extra_files=True)

        self.assertEqual(len(self.drive.list_children(class_folder)), 1)

    def test_after_a_failed_class_nothing_counts_as_a_leftover(self):
        """Nothing was built, so everything in the folder is somebody's real report."""

        self._class("2А", 2)
        self._run(runner=self._child(files=["2А Мирон Грицай.docx"]))
        class_folder = self._uploads("2А", ["2А Мирон Грицай.docx"])

        job_run = self._run(
            runner=self._child(status=JobRun.Status.FAILED, docx=0, uploaded=0, files=[]),
            remove_extra_files=True,
        )

        self.assertEqual(job_run.result_json["summary"]["extra_found"], 0)
        self.assertEqual(len(self.drive.list_children(class_folder)), 1)

    def test_only_the_chosen_classes_are_built(self):
        self._class("2А", 2)
        self._class("7А", 7)

        self._run(class_codes=["7А"])

        self.assertEqual(len(self.calls), 1)

    def test_several_chosen_classes_are_built(self):
        self._class("2А", 2)
        self._class("7А", 7)
        self._class("9А", 9)

        job_run = self._run(class_codes=["2А", "9А"])

        self.assertEqual(len(self.calls), 2)
        self.assertEqual(job_run.result_json["summary"]["classes_total"], 2)

    def test_a_class_without_a_folder_is_skipped_and_the_rest_still_run(self):
        self.module.docx_folder_0_3_url = ""
        self.module.save()
        self._class("2А", 2)
        self._class("7А", 7)

        job_run = self._run()

        self.assertEqual(job_run.status, JobRun.Status.PARTIAL)
        self.assertEqual(job_run.result_json["summary"]["classes_skipped"], 1)
        self.assertEqual(job_run.result_json["summary"]["classes_done"], 1)
        skipped = [row for row in job_run.result_json["classes"] if row["status"] == "skipped"]
        self.assertIn("0-3", skipped[0]["reason"])

    def test_a_class_missing_from_the_roster_is_skipped_by_name(self):
        ClassSheetLink.objects.create(
            class_code="9Z", subject_name="", google_sheet_url=_sheet_url("zz"), is_active=True
        )

        job_run = self._run()

        self.assertEqual(job_run.result_json["summary"]["classes_skipped"], 1)
        self.assertIn("9Z", job_run.result_json["classes"][0]["reason"])

    def test_a_failed_class_does_not_stop_the_others(self):
        self._class("2А", 2)
        self._class("7А", 7)
        statuses = iter([JobRun.Status.FAILED, JobRun.Status.SUCCESS])

        def runner(**kwargs):
            self.calls.append(kwargs)
            return JobRun.objects.create(
                job_type="prepare_student_review_reports",
                status=next(statuses),
                result_json={"summary": {"docx_created": 1, "uploaded_success": 1}},
            )

        job_run = self._run(runner=runner)

        self.assertEqual(len(self.calls), 2)
        self.assertEqual(job_run.status, JobRun.Status.PARTIAL)
        self.assertEqual(job_run.result_json["summary"]["classes_failed"], 1)

    def test_each_class_keeps_its_own_job_to_look_at(self):
        self._class("7А", 7)

        job_run = self._run()

        row = job_run.result_json["classes"][0]
        self.assertTrue(JobRun.objects.filter(id=row["job_run_id"]).exists())

    def test_no_classes_at_all_is_a_failure_not_a_quiet_success(self):
        job_run = self._run()

        self.assertEqual(job_run.status, JobRun.Status.FAILED)
        self.assertEqual(job_run.result_json["summary"]["classes_total"], 0)

    def test_an_inactive_class_is_left_out(self):
        self._class("7А", 7)
        ClassSheetLink.objects.create(
            class_code="8А", subject_name="", google_sheet_url=_sheet_url("old"), is_active=False
        )

        job_run = self._run()

        self.assertEqual(job_run.result_json["summary"]["classes_total"], 1)
