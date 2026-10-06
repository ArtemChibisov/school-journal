"""«Всё ли работает» — в одном месте.

Раньше это выяснялось обходом четырёх страниц, а `/readyz` отдавал голый JSON. Когда что-то
сломалось, обходить четыре места — ровно то, чего человек в этот момент делать не хочет.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from jobs.models import JobRun
from school_years.models import AssistantPrincipal, ClassGroup, SchoolYear
from webapp.system_status import system_status

MAIL_READY = {
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    "EMAIL_HOST": "smtp.yandex.ru",
    "EMAIL_HOST_USER": "info@school.ge",
    "EMAIL_HOST_PASSWORD": "sixteencharacter",
    "DEFAULT_FROM_EMAIL": "info@school.ge",
    "TELEGRAM_BOT_TOKEN": "token",
}


def _by_name(status, name):
    return next(check for check in status["checks"] if check["name"] == name)


class ChecksTests(TestCase):
    def test_the_database_is_answering(self):
        self.assertEqual(_by_name(system_status(), "База данных")["state"], "ok")

    def test_without_a_school_year_nothing_works(self):
        check = _by_name(system_status(), "Учебный год")

        self.assertEqual(check["state"], "error")
        self.assertIn("Не создан", check["summary"])

    @override_settings(**MAIL_READY)
    def test_configured_mail_is_reported_with_its_sender(self):
        check = _by_name(system_status(), "Почта")

        self.assertEqual(check["state"], "ok")
        self.assertIn("info@school.ge", check["summary"])

    @override_settings(EMAIL_HOST_PASSWORD="")
    def test_mail_without_a_password_is_an_error(self):
        self.assertEqual(_by_name(system_status(), "Почта")["state"], "error")

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_a_missing_telegram_bot_is_a_warning_not_an_error(self):
        """Без него не уйдут напоминания, но отчёты соберутся."""

        check = _by_name(system_status(), "Telegram")

        self.assertEqual(check["state"], "warning")

    def test_a_parallel_without_a_завуч_is_named(self):
        year = SchoolYear.objects.create(name="26/27", is_active=True)
        ClassGroup.objects.create(
            school_year=year, class_code="9К", grade_number=9, tutor_name="Т"
        )
        AssistantPrincipal.objects.all().delete()

        check = _by_name(system_status(), "Завучи")

        self.assertEqual(check["state"], "warning")
        self.assertIn("9", check["summary"])

    def test_covered_parallels_are_in_order(self):
        year = SchoolYear.objects.create(name="26/27", is_active=True)
        ClassGroup.objects.create(
            school_year=year, class_code="4А", grade_number=4, tutor_name="Т"
        )

        self.assertEqual(_by_name(system_status(), "Завучи")["state"], "ok")

    def test_failed_runs_of_the_last_day_are_counted(self):
        now = timezone.now()
        JobRun.objects.create(job_type="x", status=JobRun.Status.SUCCESS, started_at=now)
        JobRun.objects.create(job_type="x", status=JobRun.Status.FAILED, started_at=now)

        check = _by_name(system_status(), "Запуски за сутки")

        self.assertEqual(check["state"], "warning")
        self.assertIn("1 из 2", check["summary"])

    def test_the_worst_check_decides_the_verdict(self):
        status = system_status()

        self.assertEqual(status["overall"], "error")
        self.assertTrue(status["problems"])


class PageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("tech", password="x")
        self.user.user_permissions.add(Permission.objects.get(codename="view_jobrun"))
        self.client.force_login(self.user)

    def test_the_page_lists_every_check(self):
        response = self.client.get(reverse("system_state"))

        self.assertEqual(response.status_code, 200)
        for name in ("База данных", "Google", "Почта", "Завучи"):
            self.assertContains(response, name)

    def test_without_the_permission_it_is_closed(self):
        self.user.user_permissions.clear()
        self.client.force_login(get_user_model().objects.get(pk=self.user.pk))

        self.assertEqual(self.client.get(reverse("system_state")).status_code, 403)
