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
    VERDICT_EMPTY,
    VERDICT_LETTER_NOT_IN_SCALE,
    VERDICT_OK,
    VERDICT_UNKNOWN,
    classify_grade,
)


class ClassifyGradeTests(SimpleTestCase):
    def test_a_percentage_is_understood_in_both_bands(self):
        for band in (BAND_PRIMARY, BAND_MIDDLE_HIGH):
            self.assertEqual(classify_grade("75", grade_band=band).verdict, VERDICT_OK, band)

    def test_a_letter_works_in_the_senior_scale(self):
        verdict = classify_grade("A", grade_band=BAND_MIDDLE_HIGH)

        self.assertEqual(verdict.verdict, VERDICT_OK)
        self.assertIn("Meets expectations", verdict.label)

    def test_a_letter_breaks_the_primary_scale(self):
        """The legacy builder raises on it, so it has to be reported, not quietly allowed."""

        verdict = classify_grade("A", grade_band=BAND_PRIMARY)

        self.assertEqual(verdict.verdict, VERDICT_LETTER_NOT_IN_SCALE)
        self.assertTrue(verdict.is_problem)

    def test_letters_are_case_insensitive(self):
        self.assertEqual(
            classify_grade("a", grade_band=BAND_MIDDLE_HIGH).verdict, VERDICT_OK
        )

    def test_a_starred_letter_is_understood(self):
        self.assertEqual(
            classify_grade("A*", grade_band=BAND_MIDDLE_HIGH).verdict, VERDICT_OK
        )

    def test_words_are_not_grades(self):
        for value in ("зачёт", "отлично", "н/а", "сдал"):
            self.assertEqual(
                classify_grade(value, grade_band=BAND_MIDDLE_HIGH).verdict,
                VERDICT_UNKNOWN,
                value,
            )

    def test_an_empty_cell_is_not_a_bad_value(self):
        for value in ("", "   ", "-", None):
            self.assertEqual(
                classify_grade(value, grade_band=BAND_MIDDLE_HIGH).verdict,
                VERDICT_EMPTY,
                repr(value),
            )

    def test_an_em_dash_is_reported_because_the_report_drops_it(self):
        self.assertEqual(
            classify_grade("—", grade_band=BAND_MIDDLE_HIGH).verdict, VERDICT_UNKNOWN
        )

    def test_a_comma_decimal_is_reported_because_the_report_drops_it(self):
        self.assertEqual(
            classify_grade("7,5", grade_band=BAND_MIDDLE_HIGH).verdict, VERDICT_UNKNOWN
        )

    def test_the_senior_boundary_is_where_the_legacy_rules_put_it(self):
        """94 lands under a column labelled 50-89%. Reproduced on purpose, not endorsed."""

        self.assertIn(
            "50-89", classify_grade("94", grade_band=BAND_MIDDLE_HIGH).label
        )
        self.assertIn(
            "90-100", classify_grade("95", grade_band=BAND_MIDDLE_HIGH).label
        )

    def test_a_percentage_above_the_scale_is_not_understood(self):
        self.assertEqual(
            classify_grade("5000", grade_band=BAND_MIDDLE_HIGH).verdict, VERDICT_UNKNOWN
        )


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
        self.assertEqual(self.check([["75"], ["90"], ["A"]]), [])

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

    def test_a_letter_is_reported_for_a_junior_class(self):
        problems = self.check([["A"]], band=BAND_PRIMARY)

        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["verdict"], VERDICT_LETTER_NOT_IN_SCALE)

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
