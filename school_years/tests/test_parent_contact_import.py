from django.test import SimpleTestCase, TestCase

from google_integration.fakes import FakeSheetsClient
from jobs.models import JobRun
from pipeline.models import ParentContact
from school_years.imports.parent_contact_sync import JOB_TYPE, import_parent_contacts
from school_years.imports.parent_contacts import SHEET_NAME, parse_parent_contacts
from school_years.models import ClassGroup, SchoolYear

BOOK = "1ParentContactsBookIdentifier"
HEADER = ["class_code", "student_name", "parent_email_1", "parent_email_2", "is_active", "notes"]


def _sheets(rows, sheet_name=SHEET_NAME):
    client = FakeSheetsClient()
    client.add_spreadsheet(BOOK, {sheet_name: [HEADER, *rows]})
    return client


def _codes(result):
    return [issue["code"] for issue in result.as_dict()["issues"]]


class ParseTests(SimpleTestCase):
    def test_a_full_row_is_read(self):
        result = parse_parent_contacts(
            _sheets([["4А", "Мария Иванова", "dad@mail.ru", "mum@mail.ru", "TRUE", ""]]), BOOK
        )

        self.assertEqual(len(result.rows), 1)
        row = result.rows[0]
        self.assertEqual(row.class_code, "4А")
        self.assertEqual(row.emails, ("dad@mail.ru", "mum@mail.ru"))
        self.assertTrue(row.is_active)

    def test_one_address_is_enough(self):
        """The school fills what it knows; a single address is a fact, not a mistake."""

        result = parse_parent_contacts(
            _sheets([["4А", "Мария Иванова", "", "mum@mail.ru", "TRUE", ""]]), BOOK
        )

        self.assertEqual(result.rows[0].emails, ("mum@mail.ru",))
        self.assertNotIn("INVALID_EMAIL", _codes(result))

    def test_no_address_at_all_is_noted_not_refused(self):
        result = parse_parent_contacts(_sheets([["4А", "Егор Белышев", "", "", "TRUE", ""]]), BOOK)

        self.assertEqual(len(result.rows), 1)
        self.assertEqual(_codes(result), ["NO_CONTACTS"])
        self.assertTrue(result.is_usable)
        self.assertEqual(result.as_dict()["summary"]["without_contacts"], 1)

    def test_a_placeholder_address_is_left_alone(self):
        """`changethisemail@now.cc` is a real address as far as this is concerned."""

        result = parse_parent_contacts(
            _sheets([["4А", "Малика Калау", "changethisemail@now.cc", "", "TRUE", ""]]), BOOK
        )

        self.assertEqual(result.rows[0].emails, ("changethisemail@now.cc",))
        self.assertEqual(_codes(result), [])

    def test_something_that_is_not_an_address(self):
        result = parse_parent_contacts(
            _sheets([["4А", "Мария Иванова", "позвонить маме", "", "TRUE", ""]]), BOOK
        )

        self.assertIn("INVALID_EMAIL", _codes(result))
        self.assertEqual(result.rows[0].emails, ())

    def test_the_same_address_twice_is_sent_once(self):
        result = parse_parent_contacts(
            _sheets([["4А", "Мария Иванова", "one@mail.ru", "One@Mail.ru", "TRUE", ""]]), BOOK
        )

        self.assertEqual(result.rows[0].emails, ("one@mail.ru",))
        self.assertIn("REPEATED_EMAIL", _codes(result))

    def test_a_row_without_a_class_cannot_be_acted_on(self):
        result = parse_parent_contacts(_sheets([["", "Мария Иванова", "a@b.ru", "", "TRUE", ""]]), BOOK)

        self.assertEqual(result.rows, [])
        self.assertIn("INCOMPLETE_ROW", _codes(result))

    def test_the_same_student_twice_in_one_class(self):
        result = parse_parent_contacts(
            _sheets(
                [
                    ["4А", "Мария Иванова", "first@mail.ru", "", "TRUE", ""],
                    ["4А", "мария иванова", "second@mail.ru", "", "TRUE", ""],
                ]
            ),
            BOOK,
        )

        self.assertEqual(len(result.rows), 1)
        self.assertIn("DUPLICATE_STUDENT", _codes(result))

    def test_two_students_of_one_name_in_different_classes_are_two_people(self):
        result = parse_parent_contacts(
            _sheets(
                [
                    ["4А", "Мария Иванова", "a@mail.ru", "", "TRUE", ""],
                    ["4К1", "Мария Иванова", "b@mail.ru", "", "TRUE", ""],
                ]
            ),
            BOOK,
        )

        self.assertEqual(len(result.rows), 2)
        self.assertNotIn("DUPLICATE_STUDENT", _codes(result))

    def test_blank_lines_are_skipped(self):
        result = parse_parent_contacts(
            _sheets([["", "", "", "", "", ""], ["4А", "Мария Иванова", "a@b.ru", "", "TRUE", ""]]),
            BOOK,
        )

        self.assertEqual(len(result.rows), 1)
        self.assertEqual(_codes(result), [])

    def test_a_sheet_named_anything_else_is_not_found(self):
        result = parse_parent_contacts(_sheets([], sheet_name="Контакты"), BOOK)

        self.assertIn("MISSING_REQUIRED_SHEET", _codes(result))
        self.assertFalse(result.is_usable)

    def test_a_missing_column_stops_the_whole_table(self):
        client = FakeSheetsClient()
        client.add_spreadsheet(BOOK, {SHEET_NAME: [["class_code", "student_name"], ["4А", "Мария"]]})

        result = parse_parent_contacts(client, BOOK)

        self.assertIn("MISSING_REQUIRED_COLUMN", _codes(result))
        self.assertFalse(result.is_usable)


class ImportTests(TestCase):
    def setUp(self):
        self.year = SchoolYear.objects.create(
            name="26/27",
            is_active=True,
            parent_contacts_sheet_url=f"https://docs.google.com/spreadsheets/d/{BOOK}/edit",
        )
        for code, grade in (("4А", 4), ("4К1", 4)):
            ClassGroup.objects.create(
                school_year=self.year, class_code=code, grade_number=grade, tutor_name="Тьютор"
            )

    def _import(self, rows, **kwargs):
        return import_parent_contacts(self.year, sheets=_sheets(rows), **kwargs)

    def test_contacts_are_stored(self):
        job_run = self._import([["4А", "Мария Иванова", "dad@mail.ru", "mum@mail.ru", "TRUE", ""]])

        self.assertEqual(job_run.job_type, JOB_TYPE)
        self.assertEqual(job_run.status, JobRun.Status.SUCCESS)
        contact = ParentContact.objects.get()
        self.assertEqual(contact.class_code, "4А")
        self.assertEqual(contact.parallel, 4)
        self.assertEqual(contact.parent_email_1, "dad@mail.ru")
        self.assertEqual(contact.parent_email_2, "mum@mail.ru")

    def test_the_parallel_is_read_off_the_class(self):
        self._import([["4К1", "Пётр Быков", "a@mail.ru", "", "TRUE", ""]])

        self.assertEqual(ParentContact.objects.get().parallel, 4)

    def test_two_students_of_one_name_keep_their_own_addresses(self):
        """The whole reason the class is in the key."""

        self._import(
            [
                ["4А", "Мария Иванова", "a@mail.ru", "", "TRUE", ""],
                ["4К1", "Мария Иванова", "b@mail.ru", "", "TRUE", ""],
            ]
        )

        stored = {c.class_code: c.parent_email_1 for c in ParentContact.objects.all()}
        self.assertEqual(stored, {"4А": "a@mail.ru", "4К1": "b@mail.ru"})

    def test_running_again_updates_rather_than_doubles(self):
        self._import([["4А", "Мария Иванова", "old@mail.ru", "", "TRUE", ""]])
        job_run = self._import([["4А", "Мария Иванова", "new@mail.ru", "", "TRUE", ""]])

        self.assertEqual(ParentContact.objects.count(), 1)
        self.assertEqual(ParentContact.objects.get().parent_email_1, "new@mail.ru")
        self.assertEqual(job_run.result_json["sync"]["updated"], 1)

    def test_an_unchanged_row_is_left_alone(self):
        self._import([["4А", "Мария Иванова", "a@mail.ru", "", "TRUE", ""]])
        job_run = self._import([["4А", "Мария Иванова", "a@mail.ru", "", "TRUE", ""]])

        self.assertEqual(job_run.result_json["sync"]["unchanged"], 1)

    def test_a_student_who_left_the_table_is_switched_off_not_deleted(self):
        self._import([["4А", "Мария Иванова", "a@mail.ru", "", "TRUE", ""]])
        self._import([["4А", "Пётр Быков", "b@mail.ru", "", "TRUE", ""]])

        gone = ParentContact.objects.get(student_name="Мария Иванова")
        self.assertFalse(gone.is_active)
        self.assertEqual(gone.parent_email_1, "a@mail.ru")

    def test_a_row_imported_from_a_file_gains_its_class(self):
        """The CSV shape has no class column; the sheet fills it in rather than duplicating."""

        ParentContact.objects.create(
            parallel=4, class_code="", student_name="Мария Иванова", parent_email_1="old@mail.ru"
        )

        self._import([["4А", "Мария Иванова", "new@mail.ru", "", "TRUE", ""]])

        contact = ParentContact.objects.get()
        self.assertEqual(contact.class_code, "4А")
        self.assertEqual(contact.parent_email_1, "new@mail.ru")

    def test_a_student_with_no_address_is_still_stored(self):
        job_run = self._import([["4А", "Егор Белышев", "", "", "TRUE", ""]])

        contact = ParentContact.objects.get()
        self.assertTrue(contact.is_active)
        self.assertEqual(contact.parent_email_1, "")
        self.assertEqual(job_run.result_json["summary"]["without_contacts"], 1)

    def test_a_class_with_a_bad_row_is_left_exactly_as_it_was(self):
        self._import([["4А", "Мария Иванова", "a@mail.ru", "", "TRUE", ""]])

        job_run = self._import(
            [
                ["4А", "Мария Иванова", "changed@mail.ru", "", "TRUE", ""],
                ["4А", "Пётр Быков", "не почта", "", "TRUE", ""],
            ]
        )

        self.assertEqual(ParentContact.objects.get().parent_email_1, "a@mail.ru")
        self.assertIn("4А", job_run.result_json["sync"]["classes_skipped"])
        self.assertEqual(job_run.status, JobRun.Status.PARTIAL)

    def test_a_class_the_roster_does_not_know_is_imported_and_named(self):
        job_run = self._import([["9Z", "Кто-то Новый", "a@mail.ru", "", "TRUE", ""]])

        self.assertEqual(ParentContact.objects.count(), 1)
        self.assertEqual(job_run.result_json["sync"]["classes_unknown"], ["9Z"])

    def test_a_dry_run_changes_nothing(self):
        job_run = self._import(
            [["4А", "Мария Иванова", "a@mail.ru", "", "TRUE", ""]], dry_run=True
        )

        self.assertEqual(ParentContact.objects.count(), 0)
        self.assertEqual(job_run.status, JobRun.Status.SUCCESS)
        self.assertEqual(job_run.result_json["summary"]["active"], 1)

    def test_without_a_link_the_run_says_so(self):
        self.year.parent_contacts_sheet_url = ""
        self.year.save()

        job_run = import_parent_contacts(self.year, sheets=_sheets([]))

        self.assertEqual(job_run.status, JobRun.Status.FAILED)
        self.assertIn("не указана", job_run.result_json["error"])
