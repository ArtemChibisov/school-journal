import tempfile
from pathlib import Path
from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from jobs.models import JobLog, JobRun
from journal_links.models import ClassSheetLink
from pipeline.module_review_reports import JOB_TYPE as MODULE_JOB_TYPE
from school_years.models import ClassGroup, SchoolModule, SchoolYear
from pipeline.services_upload import _upload_or_update_file_with_action, upload_docx_files_to_drive_folder
from pipeline.student_review_reports import JOB_TYPE, run_prepare_student_review_reports_job


def _sheet_url(sheet_id="sheet"):
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"


def _drive_folder_url(folder_id="folder123456789"):
    return f"https://drive.google.com/drive/folders/{folder_id}"


class StudentReviewReportsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="admin", password="p")
        perms = Permission.objects.filter(codename__in=["view_jobrun", "run_full_pipeline"])
        self.user.user_permissions.add(*perms)
        self.client.force_login(self.user)
        patcher = patch("pipeline.views.enqueue_module_review_reports_job")
        self.mock_enqueue = patcher.start()
        self.addCleanup(patcher.stop)

    def test_page_shows_only_active_classes(self):
        ClassSheetLink.objects.create(
            class_code="4A",
            subject_name="Math",
            google_sheet_url=_sheet_url("active"),
            is_active=True,
        )
        ClassSheetLink.objects.create(
            class_code="5B",
            subject_name="History",
            google_sheet_url=_sheet_url("inactive"),
            is_active=False,
        )

        response = self.client.get(reverse("pipeline:student_review_reports"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("Отчеты на проверку", response.content.decode("utf-8"))

        # Assert on the offered choices, not on the whole page: a class code as short as "5B"
        # collides with the random CSRF token often enough to make the test flaky.
        offered = [str(link) for link in response.context["form"].fields["class_links"].queryset]
        self.assertEqual(offered, ["4A — Math"])

    def _module(self, **overrides):
        year = SchoolYear.objects.filter(is_active=True).first() or SchoolYear.objects.create(
            name="26/27", is_active=True
        )
        fields = {
            "school_year": year,
            "number": SchoolModule.Number.MODULE_2,
            "starts_on": date(2026, 9, 1),
            "ends_on": date(2026, 10, 2),
            "docx_folder_4_11_url": _drive_folder_url("folder123456789"),
        }
        fields.update(overrides)
        return SchoolModule.objects.create(**fields)

    def _class_group(self, class_code, grade_number, module):
        return ClassGroup.objects.create(
            school_year=module.school_year,
            class_code=class_code,
            grade_number=grade_number,
            tutor_name="Тьютор",
        )

    def test_the_run_covers_the_chosen_module(self):
        module = self._module()
        self._class_group("4A", 4, module)
        link = ClassSheetLink.objects.create(
            class_code="4A", subject_name="Math", google_sheet_url=_sheet_url("active"), is_active=True
        )
        self.mock_enqueue.return_value = JobRun.objects.create(
            job_type=MODULE_JOB_TYPE, status=JobRun.Status.PENDING
        )

        response = self.client.post(
            reverse("pipeline:student_review_reports"),
            {"class_links": [link.id], "module": module.id},
        )

        self.assertEqual(response.status_code, 302)
        kwargs = self.mock_enqueue.call_args.kwargs
        self.assertEqual(kwargs["module_id"], module.pk)
        self.assertEqual(kwargs["class_codes"], ["4A"])
        self.assertEqual(kwargs["initiated_by"], self.user)

    def test_several_classes_can_be_chosen(self):
        module = self._module()
        self._class_group("4A", 4, module)
        self._class_group("5Б", 5, module)
        first = ClassSheetLink.objects.create(
            class_code="4A", subject_name="Math", google_sheet_url=_sheet_url("a"), is_active=True
        )
        second = ClassSheetLink.objects.create(
            class_code="5Б", subject_name="Math", google_sheet_url=_sheet_url("b"), is_active=True
        )
        self.mock_enqueue.return_value = JobRun.objects.create(
            job_type=MODULE_JOB_TYPE, status=JobRun.Status.PENDING
        )

        self.client.post(
            reverse("pipeline:student_review_reports"),
            {"class_links": [first.id, second.id], "module": module.id},
        )

        self.assertEqual(self.mock_enqueue.call_args.kwargs["class_codes"], ["4A", "5Б"])

    def test_choosing_nothing_means_every_class(self):
        module = self._module()
        self.mock_enqueue.return_value = JobRun.objects.create(
            job_type=MODULE_JOB_TYPE, status=JobRun.Status.PENDING
        )

        self.client.post(
            reverse("pipeline:student_review_reports"), {"module": module.id}
        )

        self.assertIsNone(self.mock_enqueue.call_args.kwargs["class_codes"])

    def test_a_skipped_class_shows_its_reason_on_the_screen(self):
        """The run records why a class was skipped; the screen has to say it out loud."""

        job_run = JobRun.objects.create(
            job_type=MODULE_JOB_TYPE,
            status=JobRun.Status.PARTIAL,
            result_json={
                "summary": {"classes_total": 1, "classes_skipped": 1, "classes_done": 0},
                "classes": [
                    {
                        "class_code": "2А",
                        "status": "skipped",
                        "reason": "У модуля не указана папка DOCX 0-3",
                    }
                ],
            },
        )

        response = self.client.get(
            reverse("pipeline:student_review_reports"), {"run_id": job_run.id}
        )

        self.assertContains(response, "2А")
        self.assertContains(response, "не указана папка DOCX 0-3")

    def test_the_module_is_required(self):
        self.mock_enqueue.return_value = JobRun.objects.create(
            job_type=MODULE_JOB_TYPE, status=JobRun.Status.PENDING
        )

        response = self.client.post(reverse("pipeline:student_review_reports"), {})

        self.assertEqual(response.status_code, 200)
        self.mock_enqueue.assert_not_called()


class FakeDocxGenerator:
    def __init__(self, capture):
        self.capture = capture

    def generate_for_workbook(
        self,
        *,
        workbook_path,
        output_dir,
        temp_dir,
        module_number,
        module_dates,
        school_year,
        school_level,
        include_tutor,
    ):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        self.capture.update(
            {
                "workbook_path": str(workbook_path),
                "output_dir": str(output_dir),
                "temp_dir": str(temp_dir),
                "module_number": module_number,
                "module_dates": module_dates,
                "school_year": school_year,
                "school_level": school_level,
                "include_tutor": include_tutor,
            }
        )
        docx_path = output_dir / "John Doe.docx"
        docx_path.write_bytes(b"docx")
        return [str(docx_path)]


class StudentReviewReportsJobTests(TestCase):
    def _link(self, class_code="4A"):
        return ClassSheetLink.objects.create(
            class_code=class_code,
            subject_name="Math",
            google_sheet_url=_sheet_url(class_code),
            is_active=True,
        )

    def _run_job(self, *, class_code="4A"):
        link = self._link(class_code)
        capture = {}
        with tempfile.TemporaryDirectory() as tmpdir:
            workbook = Path(tmpdir) / "journal.xlsx"
            workbook.write_bytes(b"xlsx")
            with (
                patch("pipeline.student_review_reports.fetch_workbook_for_link", return_value=workbook),
                patch("pipeline.student_review_reports.upload_docx_files_to_drive_folder") as mock_upload,
            ):
                mock_upload.return_value = {
                    "uploaded_total": 1,
                    "uploaded_success": 1,
                    "uploaded_failed": 0,
                    "uploaded_created": 0,
                    "uploaded_updated": 1,
                    "uploaded_skipped": 0,
                    "uploaded_files": [
                        {
                            "name": f"{class_code} John Doe.docx",
                            "class_code": class_code,
                            "drive_file_id": "drive-id",
                            "link": "https://drive.google.com/file/d/drive-id/view",
                            "action": "updated",
                        }
                    ],
                    "errors": [],
                }
                job = run_prepare_student_review_reports_job(
                    class_sheet_link_id=link.id,
                    drive_folder_url=_drive_folder_url("folder123456789"),
                    module_number=2,
                    module_dates="01.09 – 25.10.2026",
                    school_year="2026-2027",
                    generator=FakeDocxGenerator(capture),
                )
                upload_kwargs = mock_upload.call_args.kwargs
                output_dir = Path(capture["output_dir"])

        return job, capture, upload_kwargs, output_dir

    def test_run_creates_job_params_result_logs_and_removes_temp_files(self):
        job, capture, upload_kwargs, output_dir = self._run_job(class_code="4A")

        self.assertEqual(job.job_type, JOB_TYPE)
        self.assertEqual(job.status, JobRun.Status.SUCCESS)
        self.assertEqual(job.params_json["class_code"], "4A")
        self.assertEqual(job.params_json["drive_folder_id"], "folder123456789")
        self.assertEqual(job.params_json["module_number"], 2)
        self.assertEqual(job.params_json["module_dates"], "01.09 – 25.10.2026")
        self.assertEqual(job.params_json["school_year"], "2026-2027")
        self.assertEqual(job.params_json["output_format"], "docx")
        self.assertEqual(job.params_json["trigger"], "manual")
        self.assertEqual(job.result_json["summary"]["students_found"], 1)
        self.assertEqual(job.result_json["summary"]["docx_created"], 1)
        self.assertEqual(job.result_json["summary"]["uploaded_updated"], 1)
        self.assertTrue(job.result_json["local_temp_removed"])
        self.assertFalse(output_dir.exists())
        self.assertEqual(capture["module_number"], 2)
        self.assertEqual(capture["module_dates"], "01.09 – 25.10.2026")
        self.assertEqual(capture["school_year"], "2026-2027")
        self.assertEqual(upload_kwargs["folder_id"], "folder123456789")
        self.assertEqual(upload_kwargs["duplicate_strategy"], "update")
        self.assertEqual(upload_kwargs["docx_files"][0]["name"], "4A John Doe.docx")
        self.assertTrue(JobLog.objects.filter(job_run=job, message="Student review DOCX reports started").exists())
        self.assertTrue(JobLog.objects.filter(job_run=job, message="DOCX created for student").exists())
        self.assertTrue(JobLog.objects.filter(job_run=job, message="Local temporary DOCX files removed").exists())

    def test_primary_classes_use_primary_scale_and_skip_tutor(self):
        job, capture, _upload_kwargs, _output_dir = self._run_job(class_code="3A")

        self.assertEqual(job.result_json["school_level"], "primary")
        self.assertFalse(job.result_json["include_tutor"])
        self.assertEqual(capture["school_level"], "primary")
        self.assertFalse(capture["include_tutor"])

    def test_secondary_classes_use_secondary_scale_and_include_tutor(self):
        job, capture, _upload_kwargs, _output_dir = self._run_job(class_code="4A")

        self.assertEqual(job.result_json["school_level"], "secondary")
        self.assertTrue(job.result_json["include_tutor"])
        self.assertEqual(capture["school_level"], "secondary")
        self.assertTrue(capture["include_tutor"])


class StudentReviewReportsUploadTests(SimpleTestCase):
    @patch("pipeline.services_upload._build_drive_service", return_value=object())
    @patch("pipeline.services_upload._upload_or_update_file_with_action", return_value=("drive-id", "https://drive/file", "updated"))
    def test_upload_summary_counts_updated_existing_files(self, mock_upload, _mock_service):
        with tempfile.TemporaryDirectory() as tmpdir:
            docx_path = Path(tmpdir) / "4A John Doe.docx"
            docx_path.write_bytes(b"docx")

            result = upload_docx_files_to_drive_folder(
                docx_files=[{"path": str(docx_path), "name": docx_path.name, "class_code": "4A"}],
                folder_id="folder123456789",
                duplicate_strategy="update",
            )

        self.assertEqual(result["uploaded_success"], 1)
        self.assertEqual(result["uploaded_updated"], 1)
        self.assertEqual(result["uploaded_created"], 0)
        self.assertEqual(result["uploaded_files"][0]["action"], "updated")
        mock_upload.assert_called_once()

    @patch("googleapiclient.http.MediaFileUpload", return_value=object())
    @patch("pipeline.services_upload._find_existing_file", return_value={"id": "existing-id", "webViewLink": "https://drive/old"})
    def test_drive_helper_updates_existing_file_in_folder(self, _mock_find, _mock_media):
        class _Execute:
            def __init__(self, payload):
                self.payload = payload

            def execute(self):
                return self.payload

        class _Files:
            def __init__(self):
                self.updated = False

            def update(self, **_kwargs):
                self.updated = True
                return _Execute({"id": "existing-id", "webViewLink": "https://drive/new"})

            def create(self, **_kwargs):
                raise AssertionError("create must not be called for an existing DOCX")

        class _Service:
            def __init__(self):
                self.files_resource = _Files()

            def files(self):
                return self.files_resource

        with tempfile.TemporaryDirectory() as tmpdir:
            docx_path = Path(tmpdir) / "4A John Doe.docx"
            docx_path.write_bytes(b"docx")
            service = _Service()

            file_id, link, action = _upload_or_update_file_with_action(
                service,
                local_path=docx_path,
                folder_id="folder123456789",
                duplicate_strategy="update",
            )

        self.assertEqual(file_id, "existing-id")
        self.assertEqual(link, "https://drive/new")
        self.assertEqual(action, "updated")
        self.assertTrue(service.files_resource.updated)
