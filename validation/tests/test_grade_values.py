from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from openpyxl import Workbook

from school_years.models import ClassGroup, SchoolYear
from validation.grade_value_check import check_sheet_grades, grade_band_for_class
from validation.grade_values import (
    BAND_MIDDLE_HIGH,
    BAND_PRIMARY,
    MIDDLE_HIGH_SCALE,
    PRIMARY_SCALE,
    VERDICT_EMPTY,
    VERDICT_LETTER_AND_NUMBER,
    VERDICT_LEVEL,
    VERDICT_LOST,
    VERDICT_NUMBER,
    classify_grade,
)


class ClassifyGradeTests(SimpleTestCase):
    """The ordinary case first: teachers pick a level from the dropdown and that text lands
    in the cell. Nineteen thousand of the grades in the real journals look like this."""

    def test_the_text_of_a_level_is_the_normal_grade(self):
        for label, _, _ in MIDDLE_HIGH_SCALE:
            verdict = classify_grade(label, grade_band=BAND_MIDDLE_HIGH)
            self.assertEqual(verdict.verdict, VERDICT_LEVEL, label)
            self.assertFalse(verdict.is_problem)

    def test_each_band_has_its_own_levels(self):
        primary_label = PRIMARY_SCALE[0][0]

        self.assertEqual(
            classify_grade(primary_label, grade_band=BAND_PRIMARY).verdict, VERDICT_LEVEL
        )
        self.assertEqual(
            classify_grade(primary_label, grade_band=BAND_MIDDLE_HIGH).verdict, VERDICT_LOST
        )

    def test_a_number_falls_into_a_band(self):
        verdict = classify_grade(75, grade_band=BAND_MIDDLE_HIGH)

        self.assertEqual(verdict.verdict, VERDICT_NUMBER)
        self.assertIn("Meets expectations", verdict.label)

    def test_a_number_written_as_text_is_still_a_number(self):
        self.assertEqual(classify_grade("75", grade_band=BAND_MIDDLE_HIGH).verdict, VERDICT_NUMBER)

    def test_the_boundary_is_where_enrich_mark_puts_it(self):
        """`low < value <= high`, so 49 belongs to the band below, not above."""

        self.assertIn("Beginner", classify_grade(49, grade_band=BAND_MIDDLE_HIGH).label)
        self.assertIn("Meets", classify_grade(50, grade_band=BAND_MIDDLE_HIGH).label)

    def test_a_percentage_and_a_letter_together_survive(self):
        """The renderer digs a letter out of these and prints them, so they are fine."""

        for value in ("86, A", "70 - B", "65 C", "91, A*", "55, D"):
            verdict = classify_grade(value, grade_band=BAND_MIDDLE_HIGH)
            self.assertEqual(verdict.verdict, VERDICT_LETTER_AND_NUMBER, value)

    def test_a_lone_letter_is_a_grade(self):
        self.assertEqual(
            classify_grade("B", grade_band=BAND_MIDDLE_HIGH).verdict, VERDICT_LETTER_AND_NUMBER
        )

    def test_words_are_reported(self):
        for value in ("Да", "Нет", "Не оценивается", "зачёт"):
            verdict = classify_grade(value, grade_band=BAND_MIDDLE_HIGH)
            self.assertEqual(verdict.verdict, VERDICT_LOST, value)
            self.assertTrue(verdict.is_problem)

    def test_a_sentence_is_reported_even_when_it_contains_a_letter(self):
        """`a` reads as the letter grade A, and the parent would be shown A instead of text."""

        verdict = classify_grade(
            "No grades to generate a fianl grade", grade_band=BAND_MIDDLE_HIGH
        )

        self.assertEqual(verdict.verdict, VERDICT_LOST)

    def test_teacher_feedback_in_a_grade_cell_is_reported(self):
        verdict = classify_grade(
            "Varvara didn't submit her notebook, so I couldn't assess her notes.",
            grade_band=BAND_MIDDLE_HIGH,
        )

        self.assertEqual(verdict.verdict, VERDICT_LOST)

    def test_a_comma_decimal_is_reported(self):
        """openpyxl hands it over as text, and enrich_mark only buckets real numbers."""

        self.assertEqual(classify_grade("40,5", grade_band=BAND_MIDDLE_HIGH).verdict, VERDICT_LOST)

    def test_every_kind_of_dash_means_no_grade(self):
        """Teachers write the dash several ways - and the report draws one just like it."""

        for value in ("-", "--", "–", "—", "−"):
            self.assertEqual(
                classify_grade(value, grade_band=BAND_MIDDLE_HIGH).verdict,
                VERDICT_EMPTY,
                value,
            )

    def test_an_empty_cell_is_not_a_bad_value(self):
        for value in ("", "   ", "-", " — ", None):
            self.assertEqual(
                classify_grade(value, grade_band=BAND_MIDDLE_HIGH).verdict,
                VERDICT_EMPTY,
                repr(value),
            )

    def test_a_number_outside_the_scale_is_reported(self):
        self.assertEqual(classify_grade(5000, grade_band=BAND_MIDDLE_HIGH).verdict, VERDICT_LOST)


class GradeBandTests(TestCase):
    def setUp(self):
        self.year = SchoolYear.objects.create(name="26/27", is_active=True)

    def _class(self, code, grade):
        return ClassGroup.objects.create(
            school_year=self.year, class_code=code, grade_number=grade, tutor_name="Т"
        )

    def test_a_junior_class_uses_the_primary_scale(self):
        self._class("3С", 3)

        self.assertEqual(grade_band_for_class("3С"), BAND_PRIMARY)

    def test_a_senior_class_uses_the_other_scale(self):
        self._class("7А", 7)

        self.assertEqual(grade_band_for_class("7А"), BAND_MIDDLE_HIGH)

    def test_an_unknown_class_falls_back_to_the_senior_scale(self):
        self.assertEqual(grade_band_for_class("9Z"), BAND_MIDDLE_HIGH)


def sheet_with_grades(values, *, criteria=("Критерий 1",)):
    """A worksheet shaped like a real subject sheet, with one student row per value row."""

    wb = Workbook()
    ws = wb.active
    ws["B1"] = "Класс | Grade"
    ws["C1"] = "4А"
    ws["B5"] = "Критерии оценивания | Assessment criteria"
    for index, title in enumerate(criteria):
        ws.cell(row=5, column=3 + index, value=title)
    ws["A6"] = "ИМЯ"
    ws["B6"] = "ФАМИЛИЯ"
    for row_offset, row_values in enumerate(values):
        row = 7 + row_offset
        ws.cell(row=row, column=1, value=f"Имя{row_offset}")
        ws.cell(row=row, column=2, value=f"Фамилия{row_offset}")
        for col_offset, value in enumerate(row_values):
            ws.cell(row=row, column=3 + col_offset, value=value)
    return ws


class CheckSheetGradesTests(SimpleTestCase):
    def check(self, values, *, band=BAND_MIDDLE_HIGH, criteria=("Критерий 1",)):
        return check_sheet_grades(
            sheet_with_grades(values, criteria=criteria),
            class_code="4А",
            subject_name="Математика",
            grade_band=band,
        )

    def test_good_grades_produce_nothing(self):
        level = MIDDLE_HIGH_SCALE[2][0]
        self.assertEqual(self.check([["75"], [level], ["A"], ["86, A"]]), [])

    def test_a_word_is_reported_with_its_context(self):
        problems = self.check([["зачёт"]])

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["value"], "зачёт")
        self.assertEqual(problems[0]["class_code"], "4А")
        self.assertEqual(problems[0]["subject_name"], "Математика")
        self.assertEqual(problems[0]["criterion"], "Критерий 1")
        self.assertEqual(problems[0]["student_name"], "Имя0 Фамилия0")

    def test_empty_cells_are_not_reported(self):
        self.assertEqual(self.check([[""], [None], ["-"]]), [])

    def test_every_bad_cell_is_listed(self):
        problems = self.check(
            [["зачёт", "75"], ["80", "н/а"]], criteria=("Критерий 1", "Критерий 2")
        )

        self.assertEqual({item["value"] for item in problems}, {"зачёт", "н/а"})

    def test_a_senior_level_in_a_junior_class_is_reported(self):
        problems = self.check([[MIDDLE_HIGH_SCALE[2][0]]], band=BAND_PRIMARY)

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["verdict"], VERDICT_LOST)

    def test_the_retake_column_is_not_a_criterion(self):
        """`Пересдача` holds да/нет. The report reads it separately to highlight a comment."""

        problems = check_sheet_grades(
            sheet_with_grades([["75", "Нет"], ["80", "Да"]], criteria=("Критерий 1", "Пересдача")),
            class_code="4А",
            subject_name="Математика",
            grade_band=BAND_MIDDLE_HIGH,
        )

        self.assertEqual(problems, [])

    def test_the_comment_column_is_not_a_criterion_either(self):
        problems = check_sheet_grades(
            sheet_with_grades(
                [["75", "Отличная работа, так держать"]],
                criteria=("Критерий 1", "Комментарий"),
            ),
            class_code="4А",
            subject_name="Математика",
            grade_band=BAND_MIDDLE_HIGH,
        )

        self.assertEqual(problems, [])

    def test_columns_after_a_service_column_are_left_alone(self):
        """Everything past the first service column belongs to the report, not to grading."""

        problems = check_sheet_grades(
            sheet_with_grades(
                [["75", "комментарий учителя", "Да"]],
                criteria=("Критерий 1", "Комментарий", "Пересдача"),
            ),
            class_code="4А",
            subject_name="Математика",
            grade_band=BAND_MIDDLE_HIGH,
        )

        self.assertEqual(problems, [])

    def test_a_sheet_without_criteria_is_skipped(self):
        wb = Workbook()
        ws = wb.active
        ws["A1"] = "что-то"

        self.assertEqual(
            check_sheet_grades(ws, class_code="4А", subject_name="X", grade_band=BAND_MIDDLE_HIGH),
            [],
        )


class GradeValueViewTests(TestCase):
    def setUp(self):
        self.url = reverse("journal_links:grade_value_report")
        self.run_url = reverse("journal_links:run_grade_value_check")
        self.user = get_user_model().objects.create_user("zavuch", password="grade-tests")

    def _grant(self, *codenames):
        self.user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_denied_without_permission(self):
        self.client.force_login(self.user)

        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_explains_what_it_checks(self):
        self._grant("view_jobrun")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "само значение")

    def test_a_viewer_cannot_run_it(self):
        self._grant("view_jobrun")

        self.assertEqual(self.client.post(self.run_url).status_code, 403)
