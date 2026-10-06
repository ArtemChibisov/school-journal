from django.test import TestCase

from google_integration.fakes import FakeSheetsClient
from google_integration.ids import spreadsheet_url
from jobs.models import JobRun
from school_years.imports.roster import parse_roster
from school_years.imports.roster_sync import JOB_TYPE, import_class_roster, sync_class_groups
from school_years.models import ClassGroup, SchoolYear

CLASS_HEADERS = ["class_code", "grade_number", "tutor_name", "is_active"]
STUDENT_HEADERS = ["class_code", "student_order", "student_name", "is_active", "notes"]

BOOK_ID = "roster-book"


def workbook(classes: list[list], students: list[list]) -> FakeSheetsClient:
    sheets = FakeSheetsClient()
    sheets.add_spreadsheet(
        BOOK_ID,
        {
            "classes": [CLASS_HEADERS, *classes],
            "students": [STUDENT_HEADERS, *students],
        },
    )
    return sheets


def codes(result) -> list[str]:
    return [issue.code for issue in result.issues.items]


class ParseRosterHappyPathTests(TestCase):
    def setUp(self):
        self.sheets = workbook(
            classes=[
                ["4А", "4", "Алена Прокушева", "yes"],
                ["0M", "0", "Мария Читава", "yes"],
            ],
            students=[
                ["4А", "2", "Оливия Арутюнян", "yes", ""],
                ["4А", "1", "Эдвард Цуриков", "yes", ""],
                ["0M", "1", "Ева Багманова", "yes", ""],
            ],
        )
        self.result = parse_roster(self.sheets, BOOK_ID)

    def test_reads_every_valid_class(self):
        self.assertEqual(self.result.classes_valid, 2)
        self.assertEqual(self.result.classes_total, 2)
        self.assertEqual(self.result.classes_active, 2)

    def test_counts_students(self):
        self.assertEqual(self.result.students_total, 3)
        self.assertEqual(self.result.students_active, 3)

    def test_classes_are_ordered_by_grade(self):
        self.assertEqual([item.class_code for item in self.result.classes], ["0M", "4А"])

    def test_students_are_ordered_by_student_order(self):
        fourth = next(item for item in self.result.classes if item.class_code == "4А")

        self.assertEqual(
            [student.student_name for student in fourth.students],
            ["Эдвард Цуриков", "Оливия Арутюнян"],
        )

    def test_no_critical_issues(self):
        self.assertEqual(self.result.issues.critical(), [])
        self.assertTrue(self.result.is_usable)


class ParseRosterWorkbookProblemTests(TestCase):
    def test_missing_sheet_makes_the_workbook_unusable(self):
        sheets = FakeSheetsClient()
        sheets.add_spreadsheet(BOOK_ID, {"classes": [CLASS_HEADERS]})

        result = parse_roster(sheets, BOOK_ID)

        self.assertIn("MISSING_REQUIRED_SHEET", codes(result))
        self.assertFalse(result.is_usable)

    def test_renamed_header_is_reported(self):
        sheets = FakeSheetsClient()
        sheets.add_spreadsheet(
            BOOK_ID,
            {
                "classes": [["class", "grade_number", "tutor_name", "is_active"]],
                "students": [STUDENT_HEADERS],
            },
        )

        result = parse_roster(sheets, BOOK_ID)

        self.assertIn("INVALID_HEADERS", codes(result))
        self.assertIn("class_code", result.issues.items[0].message)

    def test_extra_columns_are_reported_as_info_only(self):
        sheets = FakeSheetsClient()
        sheets.add_spreadsheet(
            BOOK_ID,
            {
                "classes": [[*CLASS_HEADERS, "comment"], ["4А", "4", "Алена", "yes", "что-то"]],
                "students": [STUDENT_HEADERS, ["4А", "1", "Оливия", "yes", ""]],
            },
        )

        result = parse_roster(sheets, BOOK_ID)

        self.assertIn("EXTRA_COLUMNS_IGNORED", codes(result))
        self.assertEqual(result.classes_valid, 1)

    def test_blank_rows_are_skipped(self):
        sheets = workbook(
            classes=[["4А", "4", "Алена", "yes"], ["", "", "", ""]],
            students=[["4А", "1", "Оливия", "yes", ""], ["", "", "", "", ""]],
        )

        result = parse_roster(sheets, BOOK_ID)

        self.assertEqual(result.classes_total, 1)
        self.assertEqual(result.students_total, 1)


class ParseRosterClassProblemTests(TestCase):
    def _parse(self, classes, students=None):
        sheets = workbook(classes=classes, students=students or [])
        return parse_roster(sheets, BOOK_ID)

    def test_empty_class_code(self):
        result = self._parse([["", "4", "Алена", "yes"]])

        self.assertIn("EMPTY_CLASS_CODE", codes(result))

    def test_unrecognised_active_flag(self):
        result = self._parse([["4А", "4", "Алена", "может быть"]])

        self.assertIn("INVALID_ACTIVE_FLAG", codes(result))
        self.assertEqual(result.classes_valid, 0)

    def test_inactive_class_is_skipped_as_info(self):
        result = self._parse([["4А", "4", "Алена", "no"]])

        self.assertIn("INACTIVE_CLASS_IGNORED", codes(result))
        self.assertEqual(result.issues.critical(), [])
        self.assertEqual(result.classes_active, 0)

    def test_duplicate_class_code_drops_both_rows(self):
        result = self._parse(
            [["4А", "4", "Алена", "yes"], ["4а", "4", "Другая", "yes"]],
            [["4А", "1", "Оливия", "yes", ""]],
        )

        self.assertIn("DUPLICATE_CLASS_CODE", codes(result))
        self.assertEqual(result.classes_valid, 0)

    def test_grade_out_of_range(self):
        result = self._parse([["4А", "12", "Алена", "yes"]])

        self.assertIn("INVALID_GRADE_NUMBER", codes(result))

    def test_grade_not_a_number(self):
        result = self._parse([["4А", "четыре", "Алена", "yes"]])

        self.assertIn("INVALID_GRADE_NUMBER", codes(result))

    def test_grade_accepts_a_spreadsheet_float(self):
        result = self._parse([["4А", "4.0", "Алена", "yes"]], [["4А", "1", "Оливия", "yes", ""]])

        self.assertEqual(result.classes[0].grade_number, 4)

    def test_missing_tutor(self):
        result = self._parse([["4А", "4", "", "yes"]])

        self.assertIn("MISSING_TUTOR", codes(result))

    def test_class_without_students(self):
        result = self._parse([["4А", "4", "Алена", "yes"]], [])

        self.assertIn("MISSING_STUDENTS", codes(result))
        self.assertEqual(result.classes_valid, 0)

    def test_one_broken_class_does_not_block_the_others(self):
        result = self._parse(
            [["4А", "4", "", "yes"], ["5Б", "5", "Аня", "yes"]],
            [["4А", "1", "Оливия", "yes", ""], ["5Б", "1", "Эдвард", "yes", ""]],
        )

        self.assertEqual([item.class_code for item in result.classes], ["5Б"])
        self.assertTrue(result.is_usable)


class ParseRosterStudentProblemTests(TestCase):
    def _parse(self, students):
        sheets = workbook(classes=[["4А", "4", "Алена", "yes"]], students=students)
        return parse_roster(sheets, BOOK_ID)

    def test_student_in_an_unknown_class(self):
        result = self._parse([["4А", "1", "Оливия", "yes", ""], ["9Я", "1", "Кто-то", "yes", ""]])

        self.assertIn("STUDENT_CLASS_NOT_FOUND", codes(result))
        self.assertEqual(result.classes_valid, 1)

    def test_missing_student_name(self):
        result = self._parse([["4А", "1", "", "yes", ""]])

        self.assertIn("MISSING_STUDENT_NAME", codes(result))

    def test_invalid_student_order(self):
        result = self._parse([["4А", "ноль", "Оливия", "yes", ""]])

        self.assertIn("INVALID_STUDENT_ORDER", codes(result))

    def test_student_order_below_one(self):
        result = self._parse([["4А", "0", "Оливия", "yes", ""]])

        self.assertIn("INVALID_STUDENT_ORDER", codes(result))

    def test_duplicate_student_order(self):
        result = self._parse(
            [["4А", "1", "Оливия", "yes", ""], ["4А", "1", "Эдвард", "yes", ""]]
        )

        self.assertIn("DUPLICATE_STUDENT_ORDER", codes(result))

    def test_duplicate_student_name_ignoring_case(self):
        result = self._parse(
            [["4А", "1", "Оливия Арутюнян", "yes", ""], ["4А", "2", "оливия  арутюнян", "yes", ""]]
        )

        self.assertIn("DUPLICATE_STUDENT_NAME", codes(result))

    def test_inactive_student_is_skipped_as_info(self):
        result = self._parse(
            [["4А", "1", "Оливия", "yes", ""], ["4А", "2", "Ушедший", "no", ""]]
        )

        self.assertIn("INACTIVE_STUDENT_IGNORED", codes(result))
        self.assertEqual(result.issues.critical(), [])
        self.assertEqual(len(result.classes[0].students), 1)

    def test_order_gap_is_a_warning_and_keeps_the_class(self):
        result = self._parse(
            [["4А", "1", "Оливия", "yes", ""], ["4А", "5", "Эдвард", "yes", ""]]
        )

        self.assertIn("STUDENT_ORDER_GAP", codes(result))
        self.assertEqual(result.issues.critical(), [])
        self.assertEqual(result.classes_valid, 1)


class ClassCodeNormalisationTests(TestCase):
    def test_non_breaking_space_is_normalised_before_matching(self):
        sheets = workbook(
            classes=[["4А", "4", "Алена", "yes"]],
            students=[["4 А", "1", "Оливия", "yes", ""]],
        )

        result = parse_roster(sheets, BOOK_ID)

        # The student row carries a non-breaking space, but "4 А" is still a different code
        # than "4А" once whitespace is collapsed, so this must be reported, not silently joined.
        self.assertIn("STUDENT_CLASS_NOT_FOUND", codes(result))

    def test_surrounding_whitespace_does_not_break_the_join(self):
        sheets = workbook(
            classes=[["  4А  ", "4", "Алена", "yes"]],
            students=[["4А ", "1", "Оливия", "yes", ""]],
        )

        result = parse_roster(sheets, BOOK_ID)

        self.assertEqual(result.classes_valid, 1)
        self.assertEqual(result.classes[0].class_code, "4А")

    def test_cyrillic_and_latin_codes_are_different_classes(self):
        sheets = workbook(
            classes=[["4А", "4", "Алена", "yes"], ["4A", "4", "Аня", "yes"]],
            students=[["4А", "1", "Оливия", "yes", ""], ["4A", "1", "Эдвард", "yes", ""]],
        )

        result = parse_roster(sheets, BOOK_ID)

        self.assertEqual(result.classes_valid, 2)
        self.assertNotIn("DUPLICATE_CLASS_CODE", codes(result))


class SyncClassGroupsTests(TestCase):
    def setUp(self):
        self.school_year = SchoolYear.objects.create(name="25/26")

    def _sync(self, classes, students):
        result = parse_roster(workbook(classes=classes, students=students), BOOK_ID)
        return result, sync_class_groups(self.school_year, result)

    def test_creates_missing_classes(self):
        _, summary = self._sync(
            [["4А", "4", "Алена", "yes"]], [["4А", "1", "Оливия", "yes", ""]]
        )

        self.assertEqual(summary.created, 1)
        group = ClassGroup.objects.get(school_year=self.school_year, class_code="4А")
        self.assertEqual(group.grade_number, 4)
        self.assertEqual(group.tutor_name, "Алена")
        self.assertEqual(group.grade_band, ClassGroup.GradeBand.MIDDLE_HIGH_4_11)

    def test_second_run_changes_nothing(self):
        args = ([["4А", "4", "Алена", "yes"]], [["4А", "1", "Оливия", "yes", ""]])
        self._sync(*args)

        _, summary = self._sync(*args)

        self.assertEqual(summary.created, 0)
        self.assertEqual(summary.updated, 0)
        self.assertEqual(summary.unchanged, 1)

    def test_updates_a_changed_tutor(self):
        self._sync([["4А", "4", "Алена", "yes"]], [["4А", "1", "Оливия", "yes", ""]])

        _, summary = self._sync([["4А", "4", "Аня", "yes"]], [["4А", "1", "Оливия", "yes", ""]])

        self.assertEqual(summary.updated, 1)
        self.assertEqual(ClassGroup.objects.get(class_code="4А").tutor_name, "Аня")

    def test_grade_band_follows_a_grade_change_across_the_boundary(self):
        self._sync([["3Д", "3", "Алена", "yes"]], [["3Д", "1", "Оливия", "yes", ""]])
        self.assertEqual(
            ClassGroup.objects.get(class_code="3Д").grade_band,
            ClassGroup.GradeBand.PRIMARY_0_3,
        )

        self._sync([["3Д", "4", "Алена", "yes"]], [["3Д", "1", "Оливия", "yes", ""]])

        self.assertEqual(
            ClassGroup.objects.get(class_code="3Д").grade_band,
            ClassGroup.GradeBand.MIDDLE_HIGH_4_11,
        )

    def test_class_marked_inactive_is_deactivated(self):
        self._sync([["4А", "4", "Алена", "yes"]], [["4А", "1", "Оливия", "yes", ""]])

        _, summary = self._sync([["4А", "4", "Алена", "no"]], [])

        self.assertEqual(summary.deactivated, 1)
        self.assertFalse(ClassGroup.objects.get(class_code="4А").is_active)

    def test_class_removed_from_the_sheet_is_deactivated(self):
        self._sync(
            [["4А", "4", "Алена", "yes"], ["5Б", "5", "Аня", "yes"]],
            [["4А", "1", "Оливия", "yes", ""], ["5Б", "1", "Эдвард", "yes", ""]],
        )

        _, summary = self._sync([["4А", "4", "Алена", "yes"]], [["4А", "1", "Оливия", "yes", ""]])

        self.assertEqual(summary.deactivated, 1)
        self.assertFalse(ClassGroup.objects.get(class_code="5Б").is_active)

    def test_class_with_an_error_keeps_its_previous_state(self):
        self._sync([["4А", "4", "Алена", "yes"]], [["4А", "1", "Оливия", "yes", ""]])

        _, summary = self._sync([["4А", "4", "", "yes"]], [["4А", "1", "Оливия", "yes", ""]])

        self.assertEqual(summary.deactivated, 0)
        self.assertEqual(summary.skipped_with_errors, 1)
        group = ClassGroup.objects.get(class_code="4А")
        self.assertTrue(group.is_active)
        self.assertEqual(group.tutor_name, "Алена")

    def test_reactivates_a_class_that_came_back(self):
        self._sync([["4А", "4", "Алена", "yes"]], [["4А", "1", "Оливия", "yes", ""]])
        self._sync([["4А", "4", "Алена", "no"]], [])

        _, summary = self._sync([["4А", "4", "Алена", "yes"]], [["4А", "1", "Оливия", "yes", ""]])

        self.assertEqual(summary.updated, 1)
        self.assertTrue(ClassGroup.objects.get(class_code="4А").is_active)


class ImportClassRosterJobTests(TestCase):
    def setUp(self):
        self.sheets = workbook(
            classes=[["4А", "4", "Алена", "yes"]],
            students=[["4А", "1", "Оливия", "yes", ""]],
        )
        self.school_year = SchoolYear.objects.create(
            name="25/26",
            class_roster_sheet_url=spreadsheet_url(BOOK_ID),
        )

    def test_successful_run(self):
        job_run = import_class_roster(self.school_year, sheets=self.sheets)

        self.assertEqual(job_run.job_type, JOB_TYPE)
        self.assertEqual(job_run.status, JobRun.Status.SUCCESS)
        self.assertEqual(job_run.result_json["classes_valid"], 1)
        self.assertEqual(job_run.result_json["sync"]["created"], 1)
        self.assertEqual(ClassGroup.objects.count(), 1)

    def test_partial_when_one_class_is_broken(self):
        sheets = workbook(
            classes=[["4А", "4", "Алена", "yes"], ["5Б", "5", "", "yes"]],
            students=[["4А", "1", "Оливия", "yes", ""], ["5Б", "1", "Эдвард", "yes", ""]],
        )

        job_run = import_class_roster(self.school_year, sheets=sheets)

        self.assertEqual(job_run.status, JobRun.Status.PARTIAL)
        self.assertEqual(ClassGroup.objects.count(), 1)

    def test_failed_when_the_workbook_is_unusable(self):
        sheets = FakeSheetsClient()
        sheets.add_spreadsheet(BOOK_ID, {"classes": [CLASS_HEADERS]})

        job_run = import_class_roster(self.school_year, sheets=sheets)

        self.assertEqual(job_run.status, JobRun.Status.FAILED)
        self.assertEqual(ClassGroup.objects.count(), 0)

    def test_failed_with_a_user_message_when_the_link_is_missing(self):
        year = SchoolYear.objects.create(name="26/27")

        job_run = import_class_roster(year, sheets=self.sheets)

        self.assertEqual(job_run.status, JobRun.Status.FAILED)
        self.assertIn("не указана таблица", job_run.result_json["error"]["user_message"])

    def test_dry_run_reports_without_writing(self):
        job_run = import_class_roster(self.school_year, sheets=self.sheets, dry_run=True)

        self.assertEqual(job_run.status, JobRun.Status.SUCCESS)
        self.assertEqual(job_run.result_json["classes_valid"], 1)
        self.assertEqual(job_run.result_json["sync"]["created"], 0)
        self.assertEqual(ClassGroup.objects.count(), 0)

    def test_issues_are_stored_for_the_report(self):
        sheets = workbook(
            classes=[["4А", "4", "", "yes"]],
            students=[["4А", "1", "Оливия", "yes", ""]],
        )

        job_run = import_class_roster(self.school_year, sheets=sheets)

        issues = job_run.result_json["issues"]
        self.assertTrue(any(item["code"] == "MISSING_TUTOR" for item in issues))
        self.assertTrue(any("не указан тьютор" in item["message"] for item in issues))



class StudentSyncTests(TestCase):
    """Лист `students` читался и проверялся всегда — и никуда не записывался.

    Система знала, что учеников 367, и не знала ни одного по имени.
    """

    def _import(self, students):
        year = SchoolYear.objects.create(
            name="26/27", is_active=True, class_roster_sheet_url=spreadsheet_url(BOOK_ID)
        )
        sheets = workbook(classes=[["2Б", "2", "Тьютор", "yes"]], students=students)
        return import_class_roster(year, sheets=sheets)

    def test_students_are_stored_in_their_order(self):
        from school_years.models import Student

        self._import([["2Б", "2", "Пётр Быков", "yes", ""], ["2Б", "1", "Мария Иванова", "yes", ""]])

        names = list(
            Student.objects.filter(is_active=True)
            .order_by("student_order")
            .values_list("student_name", flat=True)
        )
        self.assertEqual(names, ["Мария Иванова", "Пётр Быков"])

    def test_a_student_who_left_is_switched_off_not_deleted(self):
        from school_years.models import Student

        self._import([["2Б", "1", "Мария Иванова", "yes", ""]])
        year = SchoolYear.objects.first()
        import_class_roster(
            year,
            sheets=workbook(
                classes=[["2Б", "2", "Тьютор", "yes"]],
                students=[["2Б", "1", "Пётр Быков", "yes", ""]],
            ),
        )

        self.assertFalse(Student.objects.get(student_name="Мария Иванова").is_active)
        self.assertTrue(Student.objects.get(student_name="Пётр Быков").is_active)

    def test_running_again_does_not_double_anybody(self):
        from school_years.models import Student

        self._import([["2Б", "1", "Мария Иванова", "yes", ""]])
        year = SchoolYear.objects.first()
        import_class_roster(
            year,
            sheets=workbook(
                classes=[["2Б", "2", "Тьютор", "yes"]],
                students=[["2Б", "1", "Мария Иванова", "yes", ""]],
            ),
        )

        self.assertEqual(Student.objects.count(), 1)
