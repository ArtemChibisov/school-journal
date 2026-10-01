from datetime import timedelta

from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from jobs.models import JobRun
from journal_links.descriptor_fill_report import (
    build_subject_teacher_rows,
    normalize_report_row,
    split_teacher_cell,
)
from validation.descriptor_criteria_fill import JOB_TYPE

SHEET_URL = "https://docs.google.com/spreadsheets/d/report-source/edit#gid=10"


def row(class_code, subject_name, teacher_name, *, descriptor_status="filled"):
    return normalize_report_row(
        {
            "teacher_name": teacher_name,
            "class_code": class_code,
            "subject_name": subject_name,
            "module_number": 1,
            "descriptor_status": descriptor_status,
            "criteria_status": "filled",
            "criteria_missing": 0,
            "criteria_total": 2,
            "grades_status": "ok",
            "grades_missing": 0,
            "grades_ratio": "4/4",
            "sheet_url": SHEET_URL,
        }
    )


class SplitTeacherCellTests(TestCase):
    def test_one_name_stays_one(self):
        self.assertEqual(split_teacher_cell("Антон Михеев"), ["Антон Михеев"])

    def test_a_comma_separates_two_people(self):
        self.assertEqual(
            split_teacher_cell("Мария Зуева, Дарья Рэпп"), ["Мария Зуева", "Дарья Рэпп"]
        )

    def test_spacing_around_the_comma_does_not_matter(self):
        self.assertEqual(split_teacher_cell("Мария Зуева ,Дарья Рэпп"), ["Мария Зуева", "Дарья Рэпп"])

    def test_a_trailing_comma_does_not_invent_a_teacher(self):
        self.assertEqual(split_teacher_cell("Мария Зуева,"), ["Мария Зуева"])

    def test_a_slash_is_left_alone(self):
        self.assertEqual(
            split_teacher_cell("Дмитрий Белов/Dmitrii Belov"), ["Дмитрий Белов/Dmitrii Belov"]
        )

    def test_an_empty_cell_yields_nothing(self):
        self.assertEqual(split_teacher_cell(""), [])


class BuildSubjectTeacherRowsTests(TestCase):
    def test_turns_a_journal_sheet_into_a_contract_row(self):
        rows = build_subject_teacher_rows([row("7A", "Math", "Teacher A")])

        self.assertEqual(
            rows,
            [
                {
                    "class_code": "7A",
                    "subject_name": "Math",
                    "teacher_name": "Teacher A",
                    "teacher_role": "subject_teacher",
                    "group_name": "",
                    "source_sheet_name": "Math",
                    "is_active": "yes",
                }
            ],
        )

    def test_the_same_assignment_appears_once(self):
        rows = build_subject_teacher_rows(
            [row("7A", "Math", "Teacher A"), row("7A", "Math", "Teacher A")]
        )

        self.assertEqual(len(rows), 1)

    def test_two_teachers_on_one_subject_both_survive(self):
        rows = build_subject_teacher_rows(
            [row("7A", "Math", "Teacher A"), row("7A", "Math", "Teacher B")]
        )

        self.assertEqual([item["teacher_name"] for item in rows], ["Teacher A", "Teacher B"])

    def test_two_teachers_in_one_cell_become_two_rows(self):
        rows = build_subject_teacher_rows([row("0M", "Soft Skills", "Мария Зуева, Дарья Рэпп")])

        self.assertEqual(
            [item["teacher_name"] for item in rows], ["Дарья Рэпп", "Мария Зуева"]
        )
        self.assertEqual({item["subject_name"] for item in rows}, {"Soft Skills"})

    def test_a_slash_is_one_person_in_two_alphabets(self):
        rows = build_subject_teacher_rows(
            [row("7К2", "Математика", "Дмитрий Белов/Dmitrii Belov")]
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["teacher_name"], "Дмитрий Белов/Dmitrii Belov")

    def test_a_sheet_without_a_readable_teacher_still_earns_a_row(self):
        rows = build_subject_teacher_rows([row("7A", "Math", "")])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["teacher_name"], "")

    def test_a_row_without_a_class_or_subject_is_dropped(self):
        rows = build_subject_teacher_rows([row("", "Math", "Teacher A"), row("7A", "", "Teacher A")])

        self.assertEqual(rows, [])

    def test_rows_are_sorted_for_a_human_to_read(self):
        rows = build_subject_teacher_rows(
            [row("7B", "Math", "Teacher B"), row("7A", "Science", "Teacher A"), row("7A", "Math", "Teacher A")]
        )

        self.assertEqual(
            [(item["class_code"], item["subject_name"]) for item in rows],
            [("7A", "Math"), ("7A", "Science"), ("7B", "Math")],
        )

    def test_a_filled_sheet_is_included_just_like_a_problem_one(self):
        rows = build_subject_teacher_rows(
            [
                row("7A", "Math", "Teacher A", descriptor_status="filled"),
                row("7A", "Science", "Teacher B", descriptor_status="missing"),
            ]
        )

        self.assertEqual(len(rows), 2)


class SubjectTeacherExportViewTests(TestCase):
    def setUp(self):
        self.url = reverse("journal_links:descriptor_criteria_subject_teachers_csv")
        self.user = User.objects.create_user(username="reporter", password="export-tests")
        self.user.user_permissions.add(
            Permission.objects.get(
                codename="view_jobrun", content_type=ContentType.objects.get_for_model(JobRun)
            )
        )

    def _login(self):
        self.client.force_login(User.objects.get(pk=self.user.pk))

    def _create_run(self, rows):
        started_at = timezone.now()
        return JobRun.objects.create(
            job_type=JOB_TYPE,
            status=JobRun.Status.SUCCESS,
            started_at=started_at,
            finished_at=started_at + timedelta(minutes=2),
            params_json={"trigger": "manual"},
            result_json={"summary": {}, "rows": rows, "tables": []},
        )

    def _raw(self, class_code, subject_name, teacher_name):
        return {
            "teacher_name": teacher_name,
            "class_code": class_code,
            "subject_name": subject_name,
            "module_number": 1,
            "descriptor_status": "filled",
            "criteria_status": "filled",
            "criteria_missing": 0,
            "criteria_total": 2,
            "grades_status": "ok",
            "grades_missing": 0,
            "grades_ratio": "4/4",
            "sheet_url": SHEET_URL,
        }

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_denied_without_permission(self):
        self.client.force_login(User.objects.create_user(username="guest", password="export-tests"))

        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_downloads_a_csv_named_after_the_table(self):
        self._create_run([self._raw("7A", "Math", "Teacher A")])
        self._login()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("subject-teachers.csv", response["Content-Disposition"])

    def test_the_header_matches_the_contract(self):
        self._create_run([self._raw("7A", "Math", "Teacher A")])
        self._login()

        body = self.client.get(self.url).content.decode("utf-8-sig")
        header = body.splitlines()[0]

        self.assertEqual(
            header,
            "class_code,subject_name,teacher_name,teacher_role,group_name,source_sheet_name,is_active",
        )

    def test_exports_every_sheet_not_only_the_problem_ones(self):
        self._create_run(
            [self._raw("7A", "Math", "Teacher A"), self._raw("7B", "Science", "Teacher B")]
        )
        self._login()

        body = self.client.get(self.url).content.decode("utf-8-sig")

        self.assertIn("7A,Math,Teacher A", body)
        self.assertIn("7B,Science,Teacher B", body)

    def test_a_class_filter_narrows_the_export(self):
        self._create_run(
            [self._raw("7A", "Math", "Teacher A"), self._raw("7B", "Science", "Teacher B")]
        )
        self._login()

        body = self.client.get(self.url, {"class_code": "7A"}).content.decode("utf-8-sig")

        self.assertIn("7A,Math", body)
        self.assertNotIn("7B,Science", body)

    def test_without_any_run_the_file_holds_only_the_header(self):
        self._login()

        body = self.client.get(self.url).content.decode("utf-8-sig")

        self.assertEqual(len([line for line in body.splitlines() if line.strip()]), 1)
