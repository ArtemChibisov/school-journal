from django.test import SimpleTestCase

from google_integration.fakes import FakeSheetsClient
from school_years.imports.subject_teachers import (
    SHEET_ASSIGNMENTS,
    SHEET_TEACHERS,
    is_english_class,
    parse_subject_teachers,
)

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


def teacher(name_ru="Юлия Галкина", name_en="Yuliya Galkina", is_active="yes", notes=""):
    return [name_ru, name_en, is_active, notes]


def assignment(
    class_code="1А",
    subject_name="Искусство | Arts",
    teacher_name="Юлия Галкина",
    group_name="",
    is_active="yes",
    teacher_role="",
    source_sheet_name="",
):
    return [
        class_code,
        subject_name,
        teacher_name,
        teacher_role,
        group_name,
        source_sheet_name,
        is_active,
    ]


class ParseTestCase(SimpleTestCase):
    SPREADSHEET = "subject-teachers-book"

    def parse(self, *, teachers=None, assignments=None, sheets=None):
        client = FakeSheetsClient()
        book = {} if sheets is None else sheets
        if sheets is None:
            book[SHEET_TEACHERS] = [TEACHER_HEADERS] + (
                teachers if teachers is not None else [teacher()]
            )
            book[SHEET_ASSIGNMENTS] = [ASSIGNMENT_HEADERS] + (
                assignments if assignments is not None else [assignment()]
            )
        client.add_spreadsheet(self.SPREADSHEET, book)
        return parse_subject_teachers(client, self.SPREADSHEET)

    def codes(self, result):
        return [item.code for item in result.issues.items]


class EnglishClassTests(SimpleTestCase):
    def test_a_class_with_cyrillic_k_is_taught_in_english(self):
        self.assertTrue(is_english_class("4К1"))
        self.assertTrue(is_english_class("11К"))

    def test_a_class_with_latin_k_counts_too(self):
        self.assertTrue(is_english_class("4K1"))

    def test_other_classes_are_not(self):
        for code in ("1А", "5Б", "0M", "3Д"):
            self.assertFalse(is_english_class(code), code)


class WorkbookTests(ParseTestCase):
    def test_a_clean_workbook_parses(self):
        result = self.parse()

        self.assertTrue(result.is_usable)
        self.assertEqual(result.teachers_total, 1)
        self.assertEqual(result.assignments_total, 1)
        self.assertEqual(self.codes(result), [])

    def test_a_missing_sheet_stops_everything(self):
        result = self.parse(sheets={SHEET_TEACHERS: [TEACHER_HEADERS, teacher()]})

        self.assertFalse(result.is_usable)
        self.assertIn("MISSING_REQUIRED_SHEET", self.codes(result))
        self.assertIn(SHEET_ASSIGNMENTS, result.issues.items[0].message)

    def test_renamed_headers_are_reported_per_sheet(self):
        result = self.parse(
            sheets={
                SHEET_TEACHERS: [["имя", "name", "is_active"], ["Юлия Галкина", "Yuliya Galkina", "yes"]],
                SHEET_ASSIGNMENTS: [ASSIGNMENT_HEADERS, assignment()],
            }
        )

        self.assertIn("INVALID_HEADERS", self.codes(result))

    def test_an_extra_column_is_only_mentioned(self):
        result = self.parse(
            sheets={
                SHEET_TEACHERS: [TEACHER_HEADERS + ["кабинет"], teacher() + ["204"]],
                SHEET_ASSIGNMENTS: [ASSIGNMENT_HEADERS, assignment()],
            }
        )

        self.assertIn("EXTRA_COLUMNS_IGNORED", self.codes(result))
        self.assertTrue(result.is_usable)
        self.assertEqual(result.teachers_total, 1)

    def test_blank_rows_are_skipped(self):
        result = self.parse(teachers=[teacher(), ["", "", "", ""]])

        self.assertEqual(result.teachers_total, 1)
        self.assertEqual(self.codes(result), [])


class TeacherSheetTests(ParseTestCase):
    def test_a_row_without_any_name_is_rejected(self):
        result = self.parse(
            teachers=[teacher(), ["", "", "yes", "забыли вписать"]],
            assignments=[assignment()],
        )

        self.assertIn("EMPTY_TEACHER_ROW", self.codes(result))
        self.assertEqual(result.teachers_total, 1)

    def test_only_one_spelling_is_enough(self):
        result = self.parse(
            teachers=[teacher(name_en="")],
            assignments=[assignment()],
        )

        self.assertEqual(result.teachers_total, 1)
        self.assertEqual(self.codes(result), [])

    def test_an_unreadable_active_flag_is_rejected(self):
        result = self.parse(
            teachers=[teacher(is_active="может быть")],
            assignments=[assignment()],
        )

        self.assertIn("INVALID_ACTIVE_FLAG", self.codes(result))

    def test_the_same_spelling_twice_is_rejected(self):
        result = self.parse(
            teachers=[teacher(), teacher(name_ru="Юлия Галкина", name_en="Julia Galkina")],
            assignments=[assignment()],
        )

        self.assertIn("DUPLICATE_TEACHER_NAME", self.codes(result))
        self.assertEqual(result.teachers_total, 1)

    def test_a_spelling_repeated_across_columns_is_caught(self):
        result = self.parse(
            teachers=[teacher(), teacher(name_ru="Иван Петров", name_en="Yuliya Galkina")],
            assignments=[assignment()],
        )

        self.assertIn("DUPLICATE_TEACHER_NAME", self.codes(result))

    def test_an_inactive_teacher_is_kept_but_counted_apart(self):
        result = self.parse(
            teachers=[teacher(), teacher(name_ru="Иван Петров", name_en="", is_active="no")],
            assignments=[assignment()],
        )

        self.assertEqual(result.teachers_total, 2)
        self.assertEqual(result.teachers_active, 1)

    def test_an_unused_active_teacher_is_only_noted(self):
        result = self.parse(
            teachers=[teacher(), teacher(name_ru="Иван Петров", name_en="")],
            assignments=[assignment()],
        )

        self.assertIn("UNUSED_TEACHER", self.codes(result))
        self.assertTrue(result.is_usable)

    def test_an_unused_inactive_teacher_is_not_even_noted(self):
        result = self.parse(
            teachers=[teacher(), teacher(name_ru="Иван Петров", name_en="", is_active="no")],
            assignments=[assignment()],
        )

        self.assertNotIn("UNUSED_TEACHER", self.codes(result))


class AssignmentSheetTests(ParseTestCase):
    def test_a_row_without_a_class_is_rejected(self):
        result = self.parse(assignments=[assignment(class_code="")])

        self.assertIn("EMPTY_CLASS_CODE", self.codes(result))
        self.assertEqual(result.assignments_total, 0)

    def test_a_row_without_a_subject_is_rejected(self):
        result = self.parse(assignments=[assignment(subject_name="")])

        self.assertIn("MISSING_SUBJECT_NAME", self.codes(result))

    def test_an_active_row_without_a_teacher_is_rejected(self):
        result = self.parse(assignments=[assignment(teacher_name="")])

        self.assertIn("MISSING_TEACHER_NAME", self.codes(result))

    def test_an_inactive_row_is_skipped_without_complaint(self):
        result = self.parse(assignments=[assignment(), assignment(subject_name="", is_active="no")])

        self.assertEqual(result.assignments_total, 1)
        self.assertIn("INACTIVE_ASSIGNMENT_IGNORED", self.codes(result))
        self.assertNotIn("MISSING_SUBJECT_NAME", self.codes(result))

    def test_an_exact_duplicate_is_rejected(self):
        result = self.parse(assignments=[assignment(), assignment()])

        self.assertIn("DUPLICATE_ASSIGNMENT", self.codes(result))
        self.assertEqual(result.assignments_total, 1)

    def test_two_teachers_on_one_subject_are_allowed(self):
        result = self.parse(
            teachers=[teacher(), teacher(name_ru="Иван Петров", name_en="")],
            assignments=[assignment(), assignment(teacher_name="Иван Петров")],
        )

        self.assertEqual(result.assignments_total, 2)
        self.assertNotIn("DUPLICATE_ASSIGNMENT", self.codes(result))

    def test_groups_of_one_subject_are_allowed(self):
        result = self.parse(
            teachers=[teacher(), teacher(name_ru="Иван Петров", name_en="")],
            assignments=[
                assignment(group_name="Группа 1"),
                assignment(group_name="Группа 2", teacher_name="Иван Петров"),
            ],
        )

        self.assertEqual(result.assignments_total, 2)
        self.assertEqual(self.codes(result), [])

    def test_mixing_a_group_and_the_whole_class_is_rejected(self):
        result = self.parse(
            teachers=[teacher(), teacher(name_ru="Иван Петров", name_en="")],
            assignments=[
                assignment(group_name=""),
                assignment(group_name="Группа 2", teacher_name="Иван Петров"),
            ],
        )

        self.assertIn("MIXED_GROUP_AND_WHOLE_CLASS_ASSIGNMENT", self.codes(result))

    def test_the_sheet_name_defaults_to_the_subject(self):
        result = self.parse(assignments=[assignment()])

        self.assertEqual(result.assignments[0].source_sheet_name, "Искусство | Arts")

    def test_the_role_defaults_to_subject_teacher(self):
        result = self.parse(assignments=[assignment()])

        self.assertEqual(result.assignments[0].teacher_role, "subject_teacher")


class JoinTests(ParseTestCase):
    def test_a_teacher_missing_from_the_directory_is_rejected(self):
        result = self.parse(assignments=[assignment(teacher_name="Кто-то Новый")])

        self.assertIn("UNKNOWN_TEACHER", self.codes(result))
        self.assertIn("Кто-то Новый", result.issues.items[0].message)

    def test_either_spelling_matches_the_same_person(self):
        result = self.parse(
            assignments=[assignment(), assignment(class_code="4К1", teacher_name="Yuliya Galkina")],
        )

        self.assertEqual(self.codes(result), [])

    def test_an_english_class_needs_the_english_spelling(self):
        result = self.parse(
            teachers=[teacher(name_en="")],
            assignments=[assignment(class_code="4К1")],
        )

        self.assertIn("MISSING_ENGLISH_NAME", self.codes(result))

    def test_a_russian_class_needs_the_russian_spelling(self):
        result = self.parse(
            teachers=[teacher(name_ru="", name_en="Yuliya Galkina")],
            assignments=[assignment(class_code="1А", teacher_name="Yuliya Galkina")],
        )

        self.assertIn("MISSING_RUSSIAN_NAME", self.codes(result))

    def test_one_spelling_is_enough_when_only_those_classes_are_taught(self):
        result = self.parse(
            teachers=[teacher(name_en="")],
            assignments=[assignment(class_code="1А")],
        )

        self.assertEqual(self.codes(result), [])

    def test_teaching_both_sides_requires_both_spellings(self):
        result = self.parse(
            assignments=[assignment(class_code="1А"), assignment(class_code="4К1")],
        )

        self.assertEqual(self.codes(result), [])

    def test_the_report_lists_what_was_read(self):
        result = self.parse(
            assignments=[assignment(class_code="1А"), assignment(class_code="4К1")],
        )
        payload = result.as_dict()

        self.assertEqual(payload["classes"], ["1А", "4К1"])
        self.assertEqual(payload["subjects"], ["Искусство | Arts"])
        self.assertEqual(payload["teachers_total"], 1)
