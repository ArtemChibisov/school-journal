from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from pipeline.models import ParentLetterTemplate
from pipeline.parent_letter import (
    DEFAULT_BODY,
    DEFAULT_SUBJECT,
    context_for,
    letter_for,
    sample_letter,
    stored_template,
    unknown_placeholders,
)
from school_years.models import SchoolModule, SchoolYear

YEAR = SchoolYear(name="26/27")


def _module(number=SchoolModule.Number.MODULE_1):
    return SchoolModule(
        school_year=YEAR, number=number, starts_on=date(2026, 9, 1), ends_on=date(2026, 10, 2)
    )


class ContextTests(SimpleTestCase):
    def test_everything_a_letter_needs(self):
        context = context_for(student_name="Мария Иванова", module=_module(), class_code="4А")

        self.assertEqual(context["student"], "Мария Иванова")
        self.assertEqual(context["class"], "4А")
        self.assertEqual(context["module_name"], "первый учебный модуль")
        self.assertEqual(context["module_dative"], "первому модулю")
        self.assertEqual(context["module_number"], "1")
        self.assertEqual(context["dates"], "01.09 – 02.10.2026")
        self.assertEqual(context["year"], "2026-2027")

    def test_each_module_has_its_own_words(self):
        names = [
            context_for(student_name="Х", module=_module(n))["module_name"]
            for n in ("1", "2", "3", "4", "5", "6")
        ]

        self.assertEqual(
            names,
            [
                "первый учебный модуль",
                "второй учебный модуль",
                "третий учебный модуль",
                "четвёртый учебный модуль",
                "пятый учебный модуль",
                "шестой учебный модуль",
            ],
        )

    def test_the_final_module_reads_as_a_module_too(self):
        context = context_for(student_name="Х", module=_module(SchoolModule.Number.FINAL))

        self.assertEqual(context["module_name"], "итоговый учебный модуль")
        self.assertEqual(context["module_dative"], "итоговому модулю")
        self.assertEqual(context["module_number"], "")


class PlaceholderTests(SimpleTestCase):
    def test_the_built_in_wording_only_uses_what_exists(self):
        self.assertEqual(unknown_placeholders(DEFAULT_SUBJECT), [])
        self.assertEqual(unknown_placeholders(DEFAULT_BODY), [])

    def test_a_typo_is_named(self):
        self.assertEqual(unknown_placeholders("Привет, {studnet}!"), ["studnet"])

    def test_text_without_placeholders_is_fine(self):
        self.assertEqual(unknown_placeholders("Просто текст"), [])


class RenderTests(SimpleTestCase):
    def test_the_built_in_letter(self):
        subject, body = letter_for(
            student_name="Мария Иванова",
            module=_module(),
            class_code="4А",
            template=(DEFAULT_SUBJECT, DEFAULT_BODY),
        )

        self.assertEqual(subject, "Отчёт по первому модулю 2026-2027 — Мария Иванова")
        self.assertIn("за первый учебный модуль (01.09 – 02.10.2026) — Мария Иванова, 4А", body)
        self.assertTrue(body.endswith("ОЦ Школа Проектор"))

    def test_the_school_wording_is_used_when_it_is_set(self):
        subject, body = letter_for(
            student_name="Мария Иванова",
            module=_module(),
            class_code="4А",
            template=("Отчёт {module_number} — {student}", "Привет! Класс {class}."),
        )

        self.assertEqual(subject, "Отчёт 1 — Мария Иванова")
        self.assertEqual(body, "Привет! Класс 4А.")

    def test_a_template_that_somehow_breaks_still_produces_a_letter(self):
        """Checked on save, so getting here is unforeseen - and a letter beats none."""

        subject, body = letter_for(
            student_name="Мария Иванова",
            module=_module(),
            class_code="4А",
            template=("{studnet}", "{nonsense}"),
        )

        self.assertIn("Мария Иванова", subject)
        self.assertIn("Дорогие родители", body)

    def test_the_name_is_never_declined(self):
        _, body = letter_for(
            student_name="Мария Иванова",
            module=_module(),
            class_code="4А",
            template=(DEFAULT_SUBJECT, DEFAULT_BODY),
        )

        self.assertIn("— Мария Иванова, 4А", body)
        self.assertNotIn("Отчёт Мария Иванова", body)


class StoredTemplateTests(TestCase):
    def test_without_a_saved_one_the_built_in_wording_is_used(self):
        self.assertEqual(stored_template(), (DEFAULT_SUBJECT, DEFAULT_BODY))

    def test_a_saved_one_wins(self):
        ParentLetterTemplate.objects.create(subject="Тема {student}", body="Текст {class}")

        self.assertEqual(stored_template(), ("Тема {student}", "Текст {class}"))

    def test_an_example_is_produced_even_with_an_empty_database(self):
        """The screen always shows a letter, year set up or not."""

        subject, body = sample_letter()

        self.assertIn("Мария Иванова", subject)
        self.assertIn("Дорогие родители", body)


class LetterScreenTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("zavuch", password="x")
        self.user.user_permissions.add(
            *Permission.objects.filter(codename__in=["view_jobrun", "run_full_pipeline"])
        )
        self.client.force_login(self.user)
        self.url = reverse("pipeline:parent_mail_settings")

    def test_the_example_is_always_on_the_page(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Так письмо увидит родитель")
        self.assertContains(response, "Мария Иванова")

    def test_the_wording_can_be_changed(self):
        response = self.client.post(
            self.url, {"subject": "Новая тема {student}", "body": "Новый текст {class}"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ParentLetterTemplate.objects.count(), 1)
        self.assertEqual(stored_template()[0], "Новая тема {student}")

    def test_saving_twice_edits_rather_than_piles_up(self):
        self.client.post(self.url, {"subject": "Раз {student}", "body": "Текст"})
        self.client.post(self.url, {"subject": "Два {student}", "body": "Текст"})

        self.assertEqual(ParentLetterTemplate.objects.count(), 1)
        self.assertEqual(stored_template()[0], "Два {student}")

    def test_a_placeholder_nobody_fills_in_is_refused(self):
        response = self.client.post(self.url, {"subject": "Тема {studnet}", "body": "Текст"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Нет такой подстановки")
        self.assertFalse(ParentLetterTemplate.objects.exists())

    def test_without_the_permission_the_wording_cannot_be_changed(self):
        self.user.user_permissions.remove(Permission.objects.get(codename="run_full_pipeline"))
        self.client.force_login(get_user_model().objects.get(pk=self.user.pk))

        response = self.client.post(self.url, {"subject": "Тема", "body": "Текст"})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ParentLetterTemplate.objects.exists())
