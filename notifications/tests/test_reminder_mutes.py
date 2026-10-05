from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from jobs.models import JobRun
from notifications.models import ReminderMute, TeacherContact
from notifications.reminder_mutes import (
    current_module,
    is_muted,
    muted_teacher_ids,
    resolve_teacher,
    set_muted,
)
from school_years.models import SchoolModule, SchoolYear, Teacher
from validation.descriptor_criteria_fill import JOB_TYPE as REPORT_JOB_TYPE


class MuteTestCase(TestCase):
    def setUp(self):
        self.year = SchoolYear.objects.create(name="26/27", is_active=True)
        today = timezone.localdate()
        self.module = SchoolModule.objects.create(
            school_year=self.year,
            number=SchoolModule.Number.MODULE_1,
            starts_on=today - timedelta(days=30),
            ends_on=today + timedelta(days=5),
        )
        self.galkina = Teacher.objects.create(
            school_year=self.year, name_ru="Юлия Галкина", name_en="Yuliya Galkina"
        )
        self.mikheev = Teacher.objects.create(school_year=self.year, name_ru="Антон Михеев")


class CurrentModuleTests(MuteTestCase):
    def test_the_running_module_is_chosen(self):
        self.assertEqual(current_module(), self.module)

    def test_the_latest_module_is_used_once_the_running_one_has_closed(self):
        self.module.starts_on = date(2026, 1, 1)
        self.module.ends_on = date(2026, 2, 1)
        self.module.save()
        later = SchoolModule.objects.create(
            school_year=self.year,
            number=SchoolModule.Number.MODULE_2,
            starts_on=date(2026, 2, 2),
            ends_on=date(2026, 3, 1),
        )

        self.assertEqual(current_module(), later)

    def test_no_module_at_all_is_not_an_error(self):
        SchoolModule.objects.all().delete()

        self.assertIsNone(current_module())


class ResolveTeacherTests(MuteTestCase):
    def test_the_russian_spelling_finds_the_person(self):
        self.assertEqual(resolve_teacher("Юлия Галкина", school_year=self.year), self.galkina)

    def test_the_english_spelling_finds_the_same_person(self):
        self.assertEqual(resolve_teacher("Yuliya Galkina", school_year=self.year), self.galkina)

    def test_spacing_and_case_do_not_matter(self):
        self.assertEqual(resolve_teacher("  юлия   галкина ", school_year=self.year), self.galkina)

    def test_an_unknown_name_resolves_to_nobody(self):
        self.assertIsNone(resolve_teacher("Кто-то Новый", school_year=self.year))

    def test_two_names_in_one_cell_resolve_to_nobody(self):
        self.assertIsNone(resolve_teacher("Мария Зуева, Дарья Рэпп", school_year=self.year))

    def test_an_empty_name_resolves_to_nobody(self):
        self.assertIsNone(resolve_teacher("", school_year=self.year))


class SetMutedTests(MuteTestCase):
    def test_muting_and_unmuting(self):
        set_muted(module=self.module, teacher=self.mikheev, muted=True)
        self.assertTrue(is_muted("Антон Михеев", module=self.module))

        set_muted(module=self.module, teacher=self.mikheev, muted=False)
        self.assertFalse(is_muted("Антон Михеев", module=self.module))

    def test_muting_twice_keeps_one_record(self):
        set_muted(module=self.module, teacher=self.mikheev, muted=True)
        set_muted(module=self.module, teacher=self.mikheev, muted=True)

        self.assertEqual(ReminderMute.objects.count(), 1)

    def test_one_tick_silences_both_spellings(self):
        set_muted(module=self.module, teacher=self.galkina, muted=True)

        self.assertTrue(is_muted("Юлия Галкина", module=self.module))
        self.assertTrue(is_muted("Yuliya Galkina", module=self.module))

    def test_the_mute_belongs_to_one_module_only(self):
        set_muted(module=self.module, teacher=self.mikheev, muted=True)
        next_module = SchoolModule.objects.create(
            school_year=self.year,
            number=SchoolModule.Number.MODULE_2,
            starts_on=self.module.ends_on + timedelta(days=1),
            ends_on=self.module.ends_on + timedelta(days=30),
        )

        self.assertFalse(is_muted("Антон Михеев", module=next_module))

    def test_an_unknown_name_is_never_muted(self):
        set_muted(module=self.module, teacher=self.mikheev, muted=True)

        self.assertFalse(is_muted("Кто-то Новый", module=self.module))

    def test_without_a_module_nobody_is_muted(self):
        set_muted(module=self.module, teacher=self.mikheev, muted=True)

        self.assertFalse(is_muted("Антон Михеев", module=None))

    def test_the_id_list_is_what_the_screen_ticks(self):
        set_muted(module=self.module, teacher=self.mikheev, muted=True)

        self.assertEqual(muted_teacher_ids(self.module), {self.mikheev.pk})


def report_row(teacher_name, class_code="4А", subject="Drama", criteria="missing"):
    return {
        "teacher_name": teacher_name,
        "class_code": class_code,
        "subject_name": subject,
        "module_number": 1,
        "descriptor_status": "filled",
        "criteria_status": criteria,
        "criteria_missing": 1,
        "criteria_total": 2,
        "grades_status": "not_applicable",
        "grades_missing": 0,
        "grades_ratio": "—",
        "sheet_url": "https://docs.google.com/spreadsheets/d/journal/edit",
    }


class ReminderSendingTests(MuteTestCase):
    def setUp(self):
        super().setUp()
        TeacherContact.objects.create(name="Антон Михеев", chat_id="111", is_active=True)
        TeacherContact.objects.create(name="Юлия Галкина", chat_id="222", is_active=True)
        self.source = JobRun.objects.create(
            job_type=REPORT_JOB_TYPE,
            status=JobRun.Status.SUCCESS,
            started_at=timezone.now(),
            result_json={
                "rows": [report_row("Антон Михеев"), report_row("Юлия Галкина", class_code="1А")]
            },
        )

    def _send(self):
        from unittest.mock import patch

        from notifications.descriptor_fill_reminders import send_descriptor_fill_reminders

        with patch("notifications.descriptor_fill_reminders.send_telegram") as send:
            job = send_descriptor_fill_reminders(self.source)
        return job, send

    def test_everyone_is_reminded_by_default(self):
        job, send = self._send()

        self.assertEqual(job.result_json["summary"]["sent"], 2)
        self.assertEqual(job.result_json["summary"]["skipped_muted"], 0)
        self.assertEqual(send.call_count, 2)

    def test_a_muted_teacher_gets_nothing(self):
        set_muted(module=self.module, teacher=self.mikheev, muted=True)

        job, send = self._send()

        self.assertEqual(job.result_json["summary"]["sent"], 1)
        self.assertEqual(job.result_json["summary"]["skipped_muted"], 1)
        self.assertEqual(send.call_count, 1)
        self.assertNotIn("111", str(send.call_args_list))

    def test_unmuting_puts_the_teacher_back(self):
        set_muted(module=self.module, teacher=self.mikheev, muted=True)
        set_muted(module=self.module, teacher=self.mikheev, muted=False)

        job, _ = self._send()

        self.assertEqual(job.result_json["summary"]["sent"], 2)


class ReminderListViewTests(MuteTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("notifications:reminder_list")
        self.toggle_url = reverse("notifications:toggle_reminder_mute")
        self.user = get_user_model().objects.create_user("zavuch", password="reminder-tests")
        JobRun.objects.create(
            job_type=REPORT_JOB_TYPE,
            status=JobRun.Status.SUCCESS,
            started_at=timezone.now(),
            result_json={"rows": [report_row("Антон Михеев")]},
        )

    def _grant(self, *codenames):
        self.user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_denied_without_permission(self):
        self.client.force_login(self.user)

        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_lists_the_teacher_and_the_module(self):
        self._grant("view_jobrun")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Антон Михеев")
        self.assertContains(response, "только в этом модуле")

    def test_a_viewer_cannot_toggle(self):
        self._grant("view_jobrun")

        response = self.client.post(self.toggle_url, {"teacher_id": self.mikheev.pk, "muted": "1"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(ReminderMute.objects.count(), 0)

    def test_turning_reminders_off_and_on(self):
        self._grant("view_jobrun", "send_reminders")

        self.client.post(self.toggle_url, {"teacher_id": self.mikheev.pk, "muted": "1"})
        self.assertTrue(is_muted("Антон Михеев", module=self.module))

        self.client.post(self.toggle_url, {"teacher_id": self.mikheev.pk, "muted": "0"})
        self.assertFalse(is_muted("Антон Михеев", module=self.module))

    def test_a_name_missing_from_the_directory_cannot_be_toggled(self):
        JobRun.objects.all().delete()
        JobRun.objects.create(
            job_type=REPORT_JOB_TYPE,
            status=JobRun.Status.SUCCESS,
            started_at=timezone.now(),
            result_json={"rows": [report_row("Мария Зуева, Дарья Рэпп")]},
        )
        self._grant("view_jobrun", "send_reminders")

        response = self.client.get(self.url)

        self.assertContains(response, "нет в справочнике")
