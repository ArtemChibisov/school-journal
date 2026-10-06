from datetime import date

from django.test import SimpleTestCase

from pipeline.parent_letter import body_for, letter_for, subject_for
from school_years.models import SchoolModule, SchoolYear

YEAR = SchoolYear(name="26/27")


def _module(number, starts=date(2026, 9, 1), ends=date(2026, 10, 2)):
    return SchoolModule(school_year=YEAR, number=number, starts_on=starts, ends_on=ends)


class SubjectTests(SimpleTestCase):
    def test_the_module_is_named_the_way_a_person_would(self):
        subject = subject_for(
            student_name="Мария Иванова", module=_module(SchoolModule.Number.MODULE_1)
        )

        self.assertEqual(subject, "Отчёт по первому модулю 2026-2027 — Мария Иванова")

    def test_each_module_has_its_own_word(self):
        words = [
            subject_for(student_name="Х", module=_module(n)).split()[2]
            for n in ("1", "2", "3", "4", "5", "6")
        ]

        self.assertEqual(
            words, ["первому", "второму", "третьему", "четвёртому", "пятому", "шестому"]
        )

    def test_the_final_module_is_not_numbered(self):
        subject = subject_for(student_name="Мария Иванова", module=_module(SchoolModule.Number.FINAL))

        self.assertEqual(subject, "Итоговый отчёт 2026-2027 — Мария Иванова")


class BodyTests(SimpleTestCase):
    def test_the_module_and_its_dates_are_in_the_letter(self):
        body = body_for(student_name="Мария Иванова", module=_module(SchoolModule.Number.MODULE_1))

        self.assertIn("за первый учебный модуль", body)
        self.assertIn("01.09 – 02.10.2026", body)

    def test_the_name_is_never_declined(self):
        """A surname cannot be declined by code, so it is never put where Russian would."""

        body = body_for(
            student_name="Мария Иванова", module=_module(SchoolModule.Number.MODULE_1), class_code="4А"
        )

        self.assertIn("— Мария Иванова, 4А.", body)
        self.assertNotIn("Отчёт Мария Иванова", body)

    def test_the_class_is_optional(self):
        body = body_for(student_name="Мария Иванова", module=_module(SchoolModule.Number.MODULE_1))

        self.assertIn("— Мария Иванова.", body)

    def test_a_module_without_dates_still_reads_as_a_sentence(self):
        body = body_for(
            student_name="Мария Иванова",
            module=SchoolModule(school_year=YEAR, number=SchoolModule.Number.MODULE_2),
        )

        self.assertIn("отчёт за второй учебный модуль — Мария Иванова.", body)
        self.assertNotIn("()", body)

    def test_the_school_signs_every_letter(self):
        body = body_for(student_name="Мария Иванова", module=_module(SchoolModule.Number.MODULE_1))

        self.assertTrue(body.endswith("ОЦ Школа Проектор"))


class LetterTests(SimpleTestCase):
    def test_both_halves_come_back_together(self):
        subject, body = letter_for(
            student_name="Мария Иванова", module=_module(SchoolModule.Number.MODULE_1), class_code="4А"
        )

        self.assertIn("Мария Иванова", subject)
        self.assertIn("Мария Иванова, 4А", body)
