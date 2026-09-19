from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from jobs.models import JobRun

from school_years.models import (
    ClassGroup,
    ClassRegister,
    FillTask,
    ParentReportSendRun,
    ReportArtifact,
    SchoolModule,
    SchoolYear,
    TeacherSheetStatus,
)


class SchoolYearModelTests(TestCase):
    def test_url_fields_are_optional(self):
        school_year = SchoolYear.objects.create(name="2026/2027")

        self.assertEqual(str(school_year), "2026/2027")
        self.assertTrue(school_year.is_active)
        self.assertEqual(school_year.class_register_template_url, "")
        self.assertEqual(school_year.parent_contacts_sheet_url, "")


class SchoolModuleModelTests(TestCase):
    def setUp(self):
        self.school_year = SchoolYear.objects.create(name="2026/2027")

    def test_number_choices_support_modules_1_to_6_and_final(self):
        self.assertEqual(
            [choice.value for choice in SchoolModule.Number],
            ["1", "2", "3", "4", "5", "6", "final"],
        )

    def test_module_is_unique_by_school_year_and_number(self):
        constraint_names = {constraint.name for constraint in SchoolModule._meta.constraints}

        self.assertIn("uq_schoolmodule_school_year_number", constraint_names)

    def test_module_folder_urls_are_optional(self):
        module = SchoolModule.objects.create(
            school_year=self.school_year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 10, 1),
        )

        self.assertEqual(str(module), "2026/2027 / Module 1")
        self.assertEqual(module.register_folder_0_3_url, "")
        self.assertEqual(module.pdf_folder_4_11_url, "")


class ClassGroupModelTests(TestCase):
    def setUp(self):
        self.school_year = SchoolYear.objects.create(name="2026/2027")

    def test_class_group_is_unique_by_school_year_and_class_code(self):
        constraint_names = {constraint.name for constraint in ClassGroup._meta.constraints}

        self.assertIn("uq_classgroup_school_year_class_code", constraint_names)

    def test_primary_grade_band_is_calculated_from_grade_number(self):
        class_group = ClassGroup.objects.create(
            school_year=self.school_year,
            class_code="3C",
            grade_number=3,
        )

        self.assertEqual(class_group.grade_band, ClassGroup.GradeBand.PRIMARY_0_3)

    def test_middle_high_grade_band_is_calculated_from_grade_number(self):
        class_group = ClassGroup.objects.create(
            school_year=self.school_year,
            class_code="4A",
            grade_number=4,
        )

        self.assertEqual(class_group.grade_band, ClassGroup.GradeBand.MIDDLE_HIGH_4_11)

    def test_mismatched_grade_band_is_invalid(self):
        class_group = ClassGroup(
            school_year=self.school_year,
            class_code="3C",
            grade_number=3,
            grade_band=ClassGroup.GradeBand.MIDDLE_HIGH_4_11,
        )

        with self.assertRaises(ValidationError) as error:
            class_group.full_clean()

        self.assertIn("grade_band", error.exception.message_dict)


class SchoolYearDomainModelSetupMixin:
    def setUp(self):
        self.school_year = SchoolYear.objects.create(name="2026/2027")
        self.other_school_year = SchoolYear.objects.create(name="2027/2028")
        self.module = SchoolModule.objects.create(
            school_year=self.school_year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 10, 1),
        )
        self.other_module = SchoolModule.objects.create(
            school_year=self.other_school_year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=date(2027, 9, 1),
            ends_on=date(2027, 10, 1),
        )
        self.class_group = ClassGroup.objects.create(
            school_year=self.school_year,
            class_code="3C",
            grade_number=3,
        )
        self.other_class_group = ClassGroup.objects.create(
            school_year=self.other_school_year,
            class_code="4A",
            grade_number=4,
        )


class ClassRegisterModelTests(SchoolYearDomainModelSetupMixin, TestCase):
    def test_status_choices_match_contract(self):
        self.assertEqual(
            [choice.value for choice in ClassRegister.Status],
            ["not_created", "created", "partial", "error", "conflict"],
        )

    def test_class_register_has_job_run_relation_and_defaults(self):
        job_run = JobRun.objects.create(job_type="class_register_generation")
        class_register = ClassRegister.objects.create(
            school_year=self.school_year,
            module=self.module,
            class_group=self.class_group,
            google_sheet_url="",
            google_file_id="",
            last_job_run=job_run,
        )

        self.assertEqual(class_register.status, ClassRegister.Status.NOT_CREATED)
        self.assertEqual(class_register.last_job_run, job_run)
        self.assertEqual(class_register.last_error_user_message, "")

    def test_class_register_is_unique_by_school_year_module_class_group(self):
        constraint_names = {constraint.name for constraint in ClassRegister._meta.constraints}

        self.assertIn("uq_classregister_school_year_module_class_group", constraint_names)

    def test_module_must_match_school_year(self):
        class_register = ClassRegister(
            school_year=self.school_year,
            module=self.other_module,
            class_group=self.class_group,
        )

        with self.assertRaises(ValidationError) as error:
            class_register.full_clean()

        self.assertIn("module", error.exception.message_dict)

    def test_class_group_must_match_school_year(self):
        class_register = ClassRegister(
            school_year=self.school_year,
            module=self.module,
            class_group=self.other_class_group,
        )

        with self.assertRaises(ValidationError) as error:
            class_register.full_clean()

        self.assertIn("class_group", error.exception.message_dict)


class FillTaskModelTests(SchoolYearDomainModelSetupMixin, TestCase):
    def test_task_type_choices_match_contract(self):
        self.assertEqual(
            [choice.value for choice in FillTask.TaskType],
            ["descriptors", "criteria", "grades"],
        )

    def test_status_choices_match_contract(self):
        self.assertEqual(
            [choice.value for choice in FillTask.Status],
            ["draft", "active", "finished", "cancelled"],
        )

    def test_fill_task_defaults_and_dates(self):
        deadline = timezone.now()
        fill_task = FillTask.objects.create(
            school_year=self.school_year,
            module=self.module,
            task_type=FillTask.TaskType.DESCRIPTORS,
            deadline_at=deadline,
        )

        self.assertEqual(fill_task.status, FillTask.Status.DRAFT)
        self.assertEqual(fill_task.deadline_at, deadline)
        self.assertIsNone(fill_task.started_at)
        self.assertIsNone(fill_task.finished_at)

    def test_module_must_match_school_year(self):
        fill_task = FillTask(
            school_year=self.school_year,
            module=self.other_module,
            task_type=FillTask.TaskType.GRADES,
            deadline_at=timezone.now(),
        )

        with self.assertRaises(ValidationError) as error:
            fill_task.full_clean()

        self.assertIn("module", error.exception.message_dict)


class TeacherSheetStatusModelTests(SchoolYearDomainModelSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.class_register = ClassRegister.objects.create(
            school_year=self.school_year,
            module=self.module,
            class_group=self.class_group,
        )

    def test_teacher_sheet_status_fields_and_defaults(self):
        status = TeacherSheetStatus.objects.create(
            school_year=self.school_year,
            module=self.module,
            class_group=self.class_group,
            class_register=self.class_register,
            subject_name="Math",
            teacher_name="Иван Петров",
            sheet_name="Math",
        )

        self.assertFalse(status.has_descriptor)
        self.assertFalse(status.has_criteria)
        self.assertFalse(status.has_grades)
        self.assertEqual(status.labels_json, {})
        self.assertFalse(status.is_complete)
        self.assertFalse(status.is_overdue)
        self.assertIsNone(status.deadline_at)
        self.assertIsNone(status.checked_at)

    def test_teacher_sheet_status_is_unique_by_register_and_sheet_name(self):
        constraint_names = {constraint.name for constraint in TeacherSheetStatus._meta.constraints}

        self.assertIn("uq_teachersheetstatus_register_sheet_name", constraint_names)

    def test_required_content_helper_does_not_set_is_complete(self):
        status = TeacherSheetStatus(
            school_year=self.school_year,
            module=self.module,
            class_group=self.class_group,
            class_register=self.class_register,
            subject_name="Math",
            teacher_name="Иван Петров",
            sheet_name="Math",
            has_descriptor=True,
            has_criteria=True,
            has_grades=True,
        )

        self.assertTrue(status.has_required_content)
        self.assertFalse(status.is_complete)

    def test_related_school_years_must_match(self):
        status = TeacherSheetStatus(
            school_year=self.school_year,
            module=self.other_module,
            class_group=self.class_group,
            class_register=self.class_register,
            subject_name="Math",
            teacher_name="Иван Петров",
            sheet_name="Math",
        )

        with self.assertRaises(ValidationError) as error:
            status.full_clean()

        self.assertIn("module", error.exception.message_dict)

    def test_class_register_must_match_module_and_class_group(self):
        status = TeacherSheetStatus(
            school_year=self.school_year,
            module=self.module,
            class_group=self.other_class_group,
            class_register=self.class_register,
            subject_name="Math",
            teacher_name="Иван Петров",
            sheet_name="Math",
        )

        with self.assertRaises(ValidationError) as error:
            status.full_clean()

        self.assertIn("class_group", error.exception.message_dict)
        self.assertIn("class_register", error.exception.message_dict)


class ReportArtifactModelTests(SchoolYearDomainModelSetupMixin, TestCase):
    def test_artifact_type_choices_match_contract(self):
        self.assertEqual(
            [choice.value for choice in ReportArtifact.ArtifactType],
            ["docx", "pdf"],
        )

    def test_status_choices_match_contract(self):
        self.assertEqual(
            [choice.value for choice in ReportArtifact.Status],
            ["created", "failed", "confirmed", "sent"],
        )

    def test_report_artifact_has_job_run_relation_and_google_fields(self):
        job_run = JobRun.objects.create(job_type="student_report_generation")
        artifact = ReportArtifact.objects.create(
            school_year=self.school_year,
            module=self.module,
            class_group=self.class_group,
            student_name="Ева Багманова",
            artifact_type=ReportArtifact.ArtifactType.PDF,
            google_file_url="https://docs.google.com/document/d/demo",
            google_file_id="demo-file-id",
            last_job_run=job_run,
        )

        self.assertEqual(artifact.status, ReportArtifact.Status.CREATED)
        self.assertEqual(artifact.last_job_run, job_run)
        self.assertEqual(artifact.google_file_id, "demo-file-id")

    def test_failed_report_artifact_allows_empty_google_file_fields(self):
        artifact = ReportArtifact.objects.create(
            school_year=self.school_year,
            module=self.module,
            class_group=self.class_group,
            student_name="Ева Багманова",
            artifact_type=ReportArtifact.ArtifactType.DOCX,
            status=ReportArtifact.Status.FAILED,
        )

        self.assertEqual(artifact.google_file_url, "")
        self.assertEqual(artifact.google_file_id, "")

    def test_non_failed_report_artifact_requires_google_file_fields(self):
        artifact = ReportArtifact(
            school_year=self.school_year,
            module=self.module,
            class_group=self.class_group,
            student_name="Ева Багманова",
            artifact_type=ReportArtifact.ArtifactType.PDF,
            status=ReportArtifact.Status.CREATED,
        )

        with self.assertRaises(ValidationError) as error:
            artifact.full_clean()

        self.assertIn("google_file_url", error.exception.message_dict)
        self.assertIn("google_file_id", error.exception.message_dict)

    def test_report_artifact_is_unique_by_school_year_module_class_student_type(self):
        constraint_names = {constraint.name for constraint in ReportArtifact._meta.constraints}

        self.assertIn("uq_reportartifact_year_module_class_student_type", constraint_names)

    def test_module_must_match_school_year(self):
        artifact = ReportArtifact(
            school_year=self.school_year,
            module=self.other_module,
            class_group=self.class_group,
            student_name="Ева Багманова",
            artifact_type=ReportArtifact.ArtifactType.PDF,
            status=ReportArtifact.Status.FAILED,
        )

        with self.assertRaises(ValidationError) as error:
            artifact.full_clean()

        self.assertIn("module", error.exception.message_dict)

    def test_class_group_must_match_school_year(self):
        artifact = ReportArtifact(
            school_year=self.school_year,
            module=self.module,
            class_group=self.other_class_group,
            student_name="Ева Багманова",
            artifact_type=ReportArtifact.ArtifactType.PDF,
            status=ReportArtifact.Status.FAILED,
        )

        with self.assertRaises(ValidationError) as error:
            artifact.full_clean()

        self.assertIn("class_group", error.exception.message_dict)


class ParentReportSendRunModelTests(SchoolYearDomainModelSetupMixin, TestCase):
    def test_status_choices_match_contract(self):
        self.assertEqual(
            [choice.value for choice in ParentReportSendRun.Status],
            ["success", "partial", "failed"],
        )

    def test_parent_report_send_run_has_job_run_and_counters(self):
        job_run = JobRun.objects.create(job_type="parent_report_send")
        send_run = ParentReportSendRun.objects.create(
            school_year=self.school_year,
            module=self.module,
            job_run=job_run,
            status=ParentReportSendRun.Status.PARTIAL,
            sent_count=12,
            failed_count=2,
            unsent_report_sheet_url="https://docs.google.com/spreadsheets/d/demo",
        )

        self.assertEqual(send_run.job_run, job_run)
        self.assertEqual(send_run.sent_count, 12)
        self.assertEqual(send_run.failed_count, 2)
        self.assertEqual(send_run.status, ParentReportSendRun.Status.PARTIAL)

    def test_multiple_send_runs_are_allowed_for_one_module(self):
        first_job_run = JobRun.objects.create(job_type="parent_report_send")
        second_job_run = JobRun.objects.create(job_type="parent_report_send")

        ParentReportSendRun.objects.create(
            school_year=self.school_year,
            module=self.module,
            job_run=first_job_run,
            status=ParentReportSendRun.Status.SUCCESS,
            sent_count=10,
        )
        ParentReportSendRun.objects.create(
            school_year=self.school_year,
            module=self.module,
            job_run=second_job_run,
            status=ParentReportSendRun.Status.SUCCESS,
            sent_count=10,
        )

        self.assertEqual(ParentReportSendRun.objects.count(), 2)

    def test_sent_count_cannot_be_negative(self):
        send_run = ParentReportSendRun(
            school_year=self.school_year,
            module=self.module,
            job_run=JobRun.objects.create(job_type="parent_report_send"),
            status=ParentReportSendRun.Status.FAILED,
            sent_count=-1,
        )

        with self.assertRaises(ValidationError) as error:
            send_run.full_clean()

        self.assertIn("sent_count", error.exception.message_dict)

    def test_failed_count_cannot_be_negative(self):
        send_run = ParentReportSendRun(
            school_year=self.school_year,
            module=self.module,
            job_run=JobRun.objects.create(job_type="parent_report_send"),
            status=ParentReportSendRun.Status.FAILED,
            failed_count=-1,
        )

        with self.assertRaises(ValidationError) as error:
            send_run.full_clean()

        self.assertIn("failed_count", error.exception.message_dict)

    def test_module_must_match_school_year(self):
        send_run = ParentReportSendRun(
            school_year=self.school_year,
            module=self.other_module,
            job_run=JobRun.objects.create(job_type="parent_report_send"),
            status=ParentReportSendRun.Status.FAILED,
        )

        with self.assertRaises(ValidationError) as error:
            send_run.full_clean()

        self.assertIn("module", error.exception.message_dict)
