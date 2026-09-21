from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from jobs.labels import job_type_label, labelled_job_types
from jobs.models import JobLog, JobRun


class JobTypeLabelTests(TestCase):
    def test_known_type_gets_a_russian_name(self):
        self.assertEqual(job_type_label("class_roster_import"), "Импорт состава классов")

    def test_unknown_type_falls_back_to_its_raw_value(self):
        self.assertEqual(job_type_label("brand_new_job"), "brand_new_job")

    def test_empty_type_is_a_dash(self):
        self.assertEqual(job_type_label(""), "—")

    def test_choices_are_sorted_by_the_human_name(self):
        choices = labelled_job_types(["run_validation", "class_roster_import"])

        self.assertEqual([label for _, label in choices], ["Импорт состава классов", "Проверка значений"])


class EventLogViewTests(TestCase):
    def setUp(self):
        self.url = reverse("event_log")
        self.user = get_user_model().objects.create_user("zavuch", password="event-log-tests")
        self.run = JobRun.objects.create(job_type="class_roster_import", started_at=timezone.now())

    def _grant_view(self):
        self.user.user_permissions.add(*Permission.objects.filter(codename="view_jobrun"))
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)

    def _log(self, message, *, level=JobLog.Level.ERROR, job_run=None, ago=None, context=None):
        entry = JobLog.objects.create(
            job_run=job_run or self.run,
            level=level,
            message=message,
            context_json=context or {},
        )
        if ago is not None:
            # auto_now_add wins on create, so an older timestamp has to be written afterwards.
            JobLog.objects.filter(pk=entry.pk).update(ts=timezone.now() - ago)
            entry.refresh_from_db()
        return entry

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_denied_without_permission(self):
        self.client.force_login(self.user)

        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_shows_problems_by_default(self):
        self._log("Google API call failed: 404", level=JobLog.Level.ERROR)
        self._log("Обычный шаг", level=JobLog.Level.INFO)
        self._grant_view()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "jobs/event_log.html")
        self.assertContains(response, "Google API call failed")
        self.assertNotContains(response, "Обычный шаг")

    def test_all_levels_can_be_shown(self):
        self._log("Обычный шаг", level=JobLog.Level.INFO)
        self._grant_view()

        response = self.client.get(self.url, {"level": "all"})

        self.assertContains(response, "Обычный шаг")

    def test_errors_only_hides_warnings(self):
        self._log("Предупреждение", level=JobLog.Level.WARNING)
        self._log("Ошибка", level=JobLog.Level.ERROR)
        self._grant_view()

        response = self.client.get(self.url, {"level": "errors"})

        self.assertContains(response, "Ошибка")
        self.assertNotContains(response, "Предупреждение")

    def test_an_unknown_level_falls_back_to_the_default(self):
        self._log("Обычный шаг", level=JobLog.Level.INFO)
        self._grant_view()

        response = self.client.get(self.url, {"level": "нет-такого"})

        self.assertEqual(response.context["filters"]["level"], "problems")
        self.assertNotContains(response, "Обычный шаг")

    def test_the_period_hides_older_events(self):
        self._log("Старая ошибка", ago=timedelta(days=30))
        self._log("Свежая ошибка")
        self._grant_view()

        response = self.client.get(self.url, {"period": "week"})

        self.assertContains(response, "Свежая ошибка")
        self.assertNotContains(response, "Старая ошибка")

    def test_all_time_shows_older_events(self):
        self._log("Старая ошибка", ago=timedelta(days=30))
        self._grant_view()

        response = self.client.get(self.url, {"period": "all"})

        self.assertContains(response, "Старая ошибка")

    def test_filters_by_job_type(self):
        other_run = JobRun.objects.create(job_type="class_register_scan", started_at=timezone.now())
        self._log("Ошибка импорта")
        self._log("Ошибка поиска журналов", job_run=other_run)
        self._grant_view()

        response = self.client.get(self.url, {"job_type": "class_register_scan"})

        self.assertContains(response, "Ошибка поиска журналов")
        self.assertNotContains(response, "Ошибка импорта")

    def test_searches_in_the_message(self):
        self._log("Таблица недоступна")
        self._log("Лист не найден")
        self._grant_view()

        response = self.client.get(self.url, {"q": "недоступна"})

        self.assertContains(response, "Таблица недоступна")
        self.assertNotContains(response, "Лист не найден")

    def test_newest_event_comes_first(self):
        self._log("Позавчерашняя", ago=timedelta(days=2))
        self._log("Вчерашняя", ago=timedelta(days=1))
        self._grant_view()

        response = self.client.get(self.url)

        messages = [entry.message for entry in response.context["page_obj"]]
        self.assertEqual(messages, ["Вчерашняя", "Позавчерашняя"])

    def test_shows_the_job_name_in_russian(self):
        self._log("Ошибка импорта")
        self._grant_view()

        response = self.client.get(self.url)

        self.assertContains(response, "Импорт состава классов")

    def test_counters_cover_the_last_day_regardless_of_the_filter(self):
        self._log("Свежая ошибка")
        self._log("Свежее предупреждение", level=JobLog.Level.WARNING)
        self._log("Старая ошибка", ago=timedelta(days=5))
        self._grant_view()

        response = self.client.get(self.url, {"period": "day", "level": "errors"})

        self.assertEqual(response.context["counters"]["errors"], 1)
        self.assertEqual(response.context["counters"]["warnings"], 1)
        self.assertEqual(response.context["counters"]["runs"], 1)

    def test_technical_context_is_available_for_the_administrator(self):
        self._log("Google API call failed", context={"file_id": "abc123"})
        self._grant_view()

        response = self.client.get(self.url)

        self.assertContains(response, "Подробности для администратора")
        self.assertContains(response, "abc123")

    def test_empty_log_says_so(self):
        self._grant_view()

        response = self.client.get(self.url)

        self.assertContains(response, "событий нет")

    def test_pagination_keeps_the_filters(self):
        for number in range(60):
            self._log(f"Ошибка {number}")
        self._grant_view()

        response = self.client.get(self.url, {"q": "Ошибка", "page": 2})

        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertIn("q=", response.context["querystring"])
        self.assertNotIn("page=", response.context["querystring"])
