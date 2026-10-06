from datetime import date

from django.test import SimpleTestCase, TestCase

from school_years.models import ClassGroup, SchoolModule, SchoolYear
from school_years.report_targets import (
    docx_target_for_class,
    format_module_dates,
    is_primary_grade,
    module_number_for_reports,
)

JUNIOR_FOLDER = "https://drive.google.com/drive/folders/junior0303030303"
SENIOR_FOLDER = "https://drive.google.com/drive/folders/senior4114114114"


class FormatModuleDatesTests(SimpleTestCase):
    def test_the_header_reads_the_way_it_always_has(self):
        module = SchoolModule(starts_on=date(2026, 9, 1), ends_on=date(2026, 10, 2))

        self.assertEqual(format_module_dates(module), "1 сентября - 2 октября")

    def test_a_module_inside_one_month(self):
        module = SchoolModule(starts_on=date(2026, 3, 3), ends_on=date(2026, 3, 28))

        self.assertEqual(format_module_dates(module), "3 марта - 28 марта")

    def test_without_dates_there_is_nothing_to_print(self):
        self.assertEqual(format_module_dates(SchoolModule()), "")


class ModuleNumberTests(SimpleTestCase):
    def test_a_numbered_module_gives_its_number(self):
        self.assertEqual(
            module_number_for_reports(SchoolModule(number=SchoolModule.Number.MODULE_3)), 3
        )

    def test_the_final_module_has_no_number(self):
        self.assertIsNone(
            module_number_for_reports(SchoolModule(number=SchoolModule.Number.FINAL))
        )


class PrimaryGradeTests(SimpleTestCase):
    def test_zero_to_three_are_primary(self):
        for grade in (0, 1, 2, 3):
            self.assertTrue(is_primary_grade(grade), grade)

    def test_four_and_up_are_not(self):
        for grade in (4, 7, 11):
            self.assertFalse(is_primary_grade(grade), grade)


class DocxTargetTests(TestCase):
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

    def _class(self, class_code, grade_number, **overrides):
        return ClassGroup.objects.create(
            school_year=self.year,
            class_code=class_code,
            grade_number=grade_number,
            tutor_name="Тьютор",
            **overrides,
        )

    def test_a_senior_class_takes_everything_from_the_module(self):
        self._class("7А", 7)

        target = docx_target_for_class(self.module, "7А")

        self.assertTrue(target.is_ready)
        self.assertEqual(target.docx_folder_url, SENIOR_FOLDER)
        self.assertEqual(target.module_number, 1)
        self.assertEqual(target.module_dates, "1 сентября - 2 октября")
        self.assertFalse(target.is_primary)

    def test_a_junior_class_goes_to_the_junior_folder(self):
        self._class("2А", 2)

        target = docx_target_for_class(self.module, "2А")

        self.assertEqual(target.docx_folder_url, JUNIOR_FOLDER)
        self.assertTrue(target.is_primary)

    def test_a_class_the_roster_does_not_know_is_refused_by_name(self):
        target = docx_target_for_class(self.module, "9Z")

        self.assertFalse(target.is_ready)
        self.assertIn("9Z", target.missing)

    def test_the_year_folder_is_used_when_the_module_has_none(self):
        """Setting it once for the year is enough; a module overrides only if it wants to."""

        self.module.docx_folder_4_11_url = ""
        self.module.save()
        self.year.docx_root_4_11_url = "https://drive.google.com/drive/folders/yearrootsenior"
        self.year.save()
        self._class("7А", 7)

        target = docx_target_for_class(self.module, "7А")

        self.assertTrue(target.is_ready)
        self.assertEqual(target.docx_folder_url, "https://drive.google.com/drive/folders/yearrootsenior")

    def test_the_module_folder_wins_over_the_year_one(self):
        self.year.docx_root_4_11_url = "https://drive.google.com/drive/folders/yearrootsenior"
        self.year.save()
        self._class("7А", 7)

        self.assertEqual(docx_target_for_class(self.module, "7А").docx_folder_url, SENIOR_FOLDER)

    def test_with_no_folder_anywhere_the_run_is_refused(self):
        self.module.docx_folder_4_11_url = ""
        self.module.save()
        self._class("7А", 7)

        target = docx_target_for_class(self.module, "7А")

        self.assertFalse(target.is_ready)
        self.assertIn("4-11", target.missing)
        self.assertIn("ни у модуля", target.missing)

    def test_the_other_folder_missing_does_not_block_a_senior_class(self):
        self.module.docx_folder_0_3_url = ""
        self.module.save()
        self._class("7А", 7)

        self.assertTrue(docx_target_for_class(self.module, "7А").is_ready)

    def test_an_inactive_class_is_not_used(self):
        self._class("7А", 7, is_active=False)

        self.assertFalse(docx_target_for_class(self.module, "7А").is_ready)
