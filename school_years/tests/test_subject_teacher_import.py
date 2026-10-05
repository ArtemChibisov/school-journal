from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from google_integration.fakes import FakeSheetsClient
from google_integration.ids import spreadsheet_url
from jobs.models import JobRun
from school_years.imports.subject_teacher_sync import JOB_TYPE, import_subject_teachers
from school_years.models import ClassGroup, SchoolYear, SubjectTeacher, Teacher

SPREADSHEET_ID = "1SubjectTeachersBookIdentifier"
SHEET_URL = spreadsheet_url(SPREADSHEET_ID)

TEACHER_HEADERS = ["teacher_name_ru", "teacher_name_en", "is_active", "notes"]
ASSIGNMENT_HEADERS = [
    "class_code",
    "subject_name",
    "teacher_name",
    "teacher_role",
    "group_name",
    "source_sheet_name",
    "is_active",
]


class ImportTestCase(TestCase):
    def setUp(self):
        self.school_year = SchoolYear.objects.create(
            name="26/27", subject_teacher_sheet_url=SHEET_URL, is_active=True
        )
        self.class_1a = self._class("1А", 1)
        self.class_4k1 = self._class("4К1", 4)

    def _class(self, class_code, grade_number):
        return ClassGroup.objects.create(
            school_year=self.school_year,
            class_code=class_code,
            grade_number=grade_number,
            tutor_name="Тьютор",
        )

    def _book(self, teachers, assignments):
        client = FakeSheetsClient()
        client.add_spreadsheet(
            SPREADSHEET_ID,
            {
                "teachers": [TEACHER_HEADERS] + teachers,
                "subject_teachers": [ASSIGNMENT_HEADERS] + assignments,
            },
        )
        return client

    def _run(self, teachers=None, assignments=None, *, dry_run=False, client=None):
        if client is None:
            client = self._book(
                teachers if teachers is not None else [["Юлия Галкина", "Yuliya Galkina", "yes", ""]],
                assignments
                if assignments is not None
                else [["1А", "Искусство", "Юлия Галкина", "", "", "", "yes"]],
            )
        return import_subject_teachers(self.school_year, sheets=client, dry_run=dry_run)


class ImportJobTests(ImportTestCase):
    def test_a_clean_table_imports(self):
        job_run = self._run()

        self.assertEqual(job_run.job_type, JOB_TYPE)
        self.assertEqual(job_run.status, JobRun.Status.SUCCESS)
        self.assertEqual(Teacher.objects.count(), 1)
        self.assertEqual(SubjectTeacher.objects.count(), 1)

    def test_the_assignment_points_at_the_class_and_the_teacher(self):
        self._run()

        row = SubjectTeacher.objects.get()
        self.assertEqual(row.class_group, self.class_1a)
        self.assertEqual(row.teacher.name_ru, "Юлия Галкина")
        self.assertEqual(row.subject_name, "Искусство")
        self.assertTrue(row.is_active)

    def test_a_missing_link_fails_with_a_human_message(self):
        self.school_year.subject_teacher_sheet_url = ""
        self.school_year.save()

        job_run = self._run()

        self.assertEqual(job_run.status, JobRun.Status.FAILED)
        self.assertIn("не указана", job_run.result_json["error"]["user_message"])

    def test_a_missing_sheet_fails_and_changes_nothing(self):
        client = FakeSheetsClient()
        client.add_spreadsheet(SPREADSHEET_ID, {"teachers": [TEACHER_HEADERS]})

        job_run = self._run(client=client)

        self.assertEqual(job_run.status, JobRun.Status.FAILED)
        self.assertEqual(Teacher.objects.count(), 0)

    def test_a_dry_run_reports_without_writing(self):
        job_run = self._run(dry_run=True)

        self.assertEqual(job_run.status, JobRun.Status.SUCCESS)
        self.assertTrue(job_run.result_json["dry_run"])
        self.assertEqual(Teacher.objects.count(), 0)
        self.assertEqual(SubjectTeacher.objects.count(), 0)

    def test_a_table_with_problems_is_partial_not_failed(self):
        job_run = self._run(
            teachers=[["Юлия Галкина", "Yuliya Galkina", "yes", ""]],
            assignments=[
                ["1А", "Искусство", "Юлия Галкина", "", "", "", "yes"],
                ["1А", "Музыка", "Кто-то Новый", "", "", "", "yes"],
            ],
        )

        self.assertEqual(job_run.status, JobRun.Status.PARTIAL)

    def test_the_run_is_tied_to_the_school_year(self):
        job_run = self._run()

        self.assertEqual(job_run.params_json["school_year_id"], self.school_year.pk)


class TeacherSyncTests(ImportTestCase):
    def test_a_second_run_changes_nothing(self):
        self._run()
        job_run = self._run()

        summary = job_run.result_json["sync"]["teachers"]
        self.assertEqual(summary["created"], 0)
        self.assertEqual(summary["unchanged"], 1)
        self.assertEqual(Teacher.objects.count(), 1)

    def test_a_teacher_is_matched_by_either_spelling(self):
        self._run()
        self._run(
            teachers=[["Юлия Галкина-Петрова", "Yuliya Galkina", "yes", ""]],
            assignments=[["1А", "Искусство", "Yuliya Galkina", "", "", "", "yes"]],
        )

        self.assertEqual(Teacher.objects.count(), 1)
        self.assertEqual(Teacher.objects.get().name_ru, "Юлия Галкина-Петрова")

    def test_a_teacher_gone_from_the_sheet_is_deactivated_not_deleted(self):
        self._run(
            teachers=[
                ["Юлия Галкина", "Yuliya Galkina", "yes", ""],
                ["Иван Петров", "", "yes", ""],
            ],
            assignments=[
                ["1А", "Искусство", "Юлия Галкина", "", "", "", "yes"],
                ["1А", "Музыка", "Иван Петров", "", "", "", "yes"],
            ],
        )

        self._run()

        self.assertEqual(Teacher.objects.count(), 2)
        self.assertFalse(Teacher.objects.get(name_ru="Иван Петров").is_active)

    def test_an_inactive_teacher_is_stored_as_inactive(self):
        self._run(
            teachers=[
                ["Юлия Галкина", "Yuliya Galkina", "yes", ""],
                ["Иван Петров", "", "no", "в отпуске"],
            ],
            assignments=[["1А", "Искусство", "Юлия Галкина", "", "", "", "yes"]],
        )

        petrov = Teacher.objects.get(name_ru="Иван Петров")
        self.assertFalse(petrov.is_active)
        self.assertEqual(petrov.notes, "в отпуске")


class AssignmentSyncTests(ImportTestCase):
    def test_a_class_missing_from_the_roster_is_reported_not_invented(self):
        job_run = self._run(
            assignments=[["9Z", "Искусство", "Юлия Галкина", "", "", "", "yes"]],
        )

        summary = job_run.result_json["sync"]["assignments"]
        self.assertEqual(summary["classes_unknown"], ["9Z"])
        self.assertEqual(SubjectTeacher.objects.count(), 0)
        self.assertEqual(ClassGroup.objects.count(), 2)

    def test_a_class_with_an_error_keeps_what_it_had(self):
        self._run(
            assignments=[
                ["1А", "Искусство", "Юлия Галкина", "", "", "", "yes"],
                ["4К1", "Музыка", "Yuliya Galkina", "", "", "", "yes"],
            ],
        )

        self._run(
            assignments=[
                ["1А", "Искусство", "Кто-то Новый", "", "", "", "yes"],
                ["4К1", "Музыка", "Yuliya Galkina", "", "", "", "yes"],
            ],
        )

        kept = SubjectTeacher.objects.get(class_group=self.class_1a)
        self.assertTrue(kept.is_active)
        self.assertEqual(kept.teacher.name_ru, "Юлия Галкина")

    def test_a_subject_dropped_from_a_class_is_deactivated(self):
        self._run(
            assignments=[
                ["1А", "Искусство", "Юлия Галкина", "", "", "", "yes"],
                ["1А", "Музыка", "Юлия Галкина", "", "", "", "yes"],
            ],
        )

        self._run()

        self.assertEqual(SubjectTeacher.objects.count(), 2)
        self.assertFalse(SubjectTeacher.objects.get(subject_name="Музыка").is_active)
        self.assertTrue(SubjectTeacher.objects.get(subject_name="Искусство").is_active)

    def test_a_subject_that_comes_back_is_reactivated(self):
        self._run(
            assignments=[
                ["1А", "Искусство", "Юлия Галкина", "", "", "", "yes"],
                ["1А", "Музыка", "Юлия Галкина", "", "", "", "yes"],
            ],
        )
        self._run()

        self._run(
            assignments=[
                ["1А", "Искусство", "Юлия Галкина", "", "", "", "yes"],
                ["1А", "Музыка", "Юлия Галкина", "", "", "", "yes"],
            ],
        )

        self.assertTrue(SubjectTeacher.objects.get(subject_name="Музыка").is_active)
        self.assertEqual(SubjectTeacher.objects.count(), 2)

    def test_groups_of_one_subject_are_separate_rows(self):
        self._run(
            teachers=[
                ["Юлия Галкина", "Yuliya Galkina", "yes", ""],
                ["Иван Петров", "", "yes", ""],
            ],
            assignments=[
                ["1А", "Английский", "Юлия Галкина", "", "Группа 1", "", "yes"],
                ["1А", "Английский", "Иван Петров", "", "Группа 2", "", "yes"],
            ],
        )

        self.assertEqual(SubjectTeacher.objects.filter(subject_name="Английский").count(), 2)

    def test_an_english_class_resolves_the_english_spelling(self):
        self._run(
            assignments=[["4К1", "Искусство", "Yuliya Galkina", "", "", "", "yes"]],
        )

        row = SubjectTeacher.objects.get()
        self.assertEqual(row.class_group, self.class_4k1)
        self.assertEqual(row.teacher.name_ru, "Юлия Галкина")


class SubjectTeacherViewTests(ImportTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("school_years:subject_teachers")
        self.run_url = reverse("school_years:run_subject_teacher_import")
        self.user = get_user_model().objects.create_user("zavuch", password="subject-tests")

    def _grant(self, *codenames):
        self.user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_denied_without_permission(self):
        self.client.force_login(self.user)

        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_shows_the_sheet_names_it_expects(self):
        self._grant("view_jobrun")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "subject_teachers")
        self.assertContains(response, "teachers")

    def test_says_when_the_link_is_missing(self):
        self.school_year.subject_teacher_sheet_url = ""
        self.school_year.save()
        self._grant("view_jobrun")

        response = self.client.get(self.url)

        self.assertContains(response, "не указана")

    def test_a_viewer_sees_no_run_button(self):
        self._grant("view_jobrun")

        response = self.client.get(self.url)

        self.assertContains(response, "нет прав на запуск")

    def test_a_viewer_cannot_run_the_import(self):
        self._grant("view_jobrun")

        self.assertEqual(self.client.post(self.run_url).status_code, 403)

    def test_the_last_run_is_shown(self):
        self._run()
        self._grant("view_jobrun")

        response = self.client.get(self.url)

        self.assertContains(response, "Искусство")
        self.assertContains(response, "Юлия Галкина")

    def test_problems_are_shown_in_human_words(self):
        self._run(
            assignments=[["1А", "Искусство", "Кто-то Новый", "", "", "", "yes"]],
        )
        self._grant("view_jobrun")

        response = self.client.get(self.url)

        self.assertContains(response, "нет в справочнике")
