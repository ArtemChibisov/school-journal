"""Whether the application can send at all, answered without revealing anything."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from pipeline.mail_status import mail_status

READY = {
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    "EMAIL_HOST": "smtp.yandex.ru",
    "EMAIL_PORT": 465,
    "EMAIL_HOST_USER": "info@school.ge",
    "EMAIL_HOST_PASSWORD": "sixteencharacter",
    "DEFAULT_FROM_EMAIL": "info@school.ge",
}


class MailStatusTests(TestCase):
    @override_settings(**READY)
    def test_a_configured_mailbox_is_ready(self):
        status = mail_status()

        self.assertTrue(status["is_ready"])
        self.assertEqual(status["problems"], [])
        self.assertEqual(status["security"], "SSL")

    @override_settings(**READY)
    def test_the_password_is_never_returned(self):
        status = mail_status()

        self.assertNotIn("sixteencharacter", str(status))
        self.assertTrue(status["password_set"])
        self.assertEqual(status["password_length"], 16)

    @override_settings(**{**READY, "EMAIL_HOST_PASSWORD": ""})
    def test_a_missing_password_is_named(self):
        status = mail_status()

        self.assertFalse(status["is_ready"])
        self.assertIn("SMTP_PASSWORD", " ".join(status["problems"]))

    @override_settings(**{**READY, "EMAIL_HOST_USER": '"info@school.ge"'})
    def test_quotes_left_in_the_address_are_called_out(self):
        """They fail authentication in a way that reads exactly like a wrong password."""

        status = mail_status()

        self.assertFalse(status["is_ready"])
        self.assertIn("кавычки", " ".join(status["problems"]))

    @override_settings(**{**READY, "EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend"})
    def test_a_debug_backend_is_not_quietly_accepted(self):
        status = mail_status()

        self.assertFalse(status["is_ready"])
        self.assertIn("никуда не уходят", " ".join(status["problems"]))


class TestLetterViewTests(TestCase):
    def setUp(self):
        from datetime import date

        from school_years.models import SchoolModule, SchoolYear

        self.user = get_user_model().objects.create_user("zavuch", password="x")
        self.user.user_permissions.add(
            *Permission.objects.filter(codename__in=["view_jobrun", "run_full_pipeline"])
        )
        self.client.force_login(self.user)
        year = SchoolYear.objects.create(name="26/27", is_active=True)
        SchoolModule.objects.create(
            school_year=year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=date(2026, 9, 1),
            ends_on=date(2026, 10, 2),
        )

    def test_the_page_opens(self):
        response = self.client.get(reverse("pipeline:parent_mail_settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Проверочное письмо")

    def test_a_test_letter_goes_to_one_address(self):
        self.client.post(reverse("pipeline:send_test_letter"), {"to": "me@school.ge"})

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["me@school.ge"])
        self.assertIn("Проверка", mail.outbox[0].subject)
        self.assertIn("Дорогие родители", mail.outbox[0].body)

    def test_without_an_address_nothing_is_sent(self):
        self.client.post(reverse("pipeline:send_test_letter"), {"to": "  "})

        self.assertEqual(mail.outbox, [])

    def test_without_the_permission_nothing_is_sent(self):
        self.user.user_permissions.remove(Permission.objects.get(codename="run_full_pipeline"))
        self.client.force_login(get_user_model().objects.get(pk=self.user.pk))

        response = self.client.post(reverse("pipeline:send_test_letter"), {"to": "me@school.ge"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(mail.outbox, [])
