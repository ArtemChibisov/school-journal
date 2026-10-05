from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from notifications.invites import (
    build_invites,
    contact_for_teacher,
    invite_link,
    resolve_contact_by_name,
)
from notifications.models import TeacherContact
from school_years.models import SchoolYear, Teacher

BOT = "ProjectorDescriptorBot"


class InviteTestCase(TestCase):
    def setUp(self):
        self.year = SchoolYear.objects.create(name="26/27", is_active=True)
        self.galkina = Teacher.objects.create(
            school_year=self.year, name_ru="Юлия Галкина", name_en="Yuliya Galkina"
        )
        self.mikheev = Teacher.objects.create(school_year=self.year, name_ru="Антон Михеев")


class InviteLinkTests(TestCase):
    def test_the_link_carries_the_token(self):
        self.assertEqual(
            invite_link("abc123", username=BOT),
            f"https://t.me/{BOT}?start=register_abc123",
        )

    def test_without_a_bot_name_there_is_no_link(self):
        self.assertEqual(invite_link("abc123", username=""), "")

    def test_without_a_token_there_is_no_link(self):
        self.assertEqual(invite_link("", username=BOT), "")


class BuildInvitesTests(InviteTestCase):
    def test_a_contact_and_a_link_per_teacher(self):
        with self.settings(TELEGRAM_BOT_USERNAME=BOT):
            rows = build_invites(self.year)

        self.assertEqual(len(rows), 2)
        self.assertEqual(TeacherContact.objects.count(), 2)
        for row in rows:
            self.assertFalse(row["registered"])
            self.assertIn("start=register_", row["link"])

    def test_a_second_run_keeps_the_same_link(self):
        with self.settings(TELEGRAM_BOT_USERNAME=BOT):
            first = {row["name"]: row["link"] for row in build_invites(self.year)}
            second = {row["name"]: row["link"] for row in build_invites(self.year)}

        self.assertEqual(first, second)
        self.assertEqual(TeacherContact.objects.count(), 2)

    def test_a_registered_teacher_gets_no_new_link(self):
        TeacherContact.objects.create(name="Антон Михеев", chat_id="555", is_active=True)

        with self.settings(TELEGRAM_BOT_USERNAME=BOT):
            rows = build_invites(self.year)

        registered = next(row for row in rows if row["name"] == "Антон Михеев")
        self.assertTrue(registered["registered"])
        self.assertEqual(registered["link"], "")

    def test_a_registered_teacher_is_not_deactivated(self):
        """The old command switched is_active off, which silenced whoever already signed up."""

        contact = TeacherContact.objects.create(name="Антон Михеев", chat_id="555", is_active=True)

        with self.settings(TELEGRAM_BOT_USERNAME=BOT):
            build_invites(self.year)

        contact.refresh_from_db()
        self.assertTrue(contact.is_active)
        self.assertEqual(contact.chat_id, "555")

    def test_one_person_gets_one_invite_despite_two_spellings(self):
        TeacherContact.objects.create(name="Yuliya Galkina", chat_id="", is_active=False)

        with self.settings(TELEGRAM_BOT_USERNAME=BOT):
            rows = build_invites(self.year)

        self.assertEqual(len(rows), 2)
        self.assertEqual(TeacherContact.objects.filter(name__icontains="galkina").count(), 1)

    def test_an_inactive_teacher_is_not_invited(self):
        Teacher.objects.create(school_year=self.year, name_ru="Бывший Сотрудник", is_active=False)

        with self.settings(TELEGRAM_BOT_USERNAME=BOT):
            rows = build_invites(self.year)

        self.assertNotIn("Бывший Сотрудник", [row["name"] for row in rows])

    def test_those_waiting_come_first(self):
        TeacherContact.objects.create(name="Антон Михеев", chat_id="555", is_active=True)

        with self.settings(TELEGRAM_BOT_USERNAME=BOT):
            rows = build_invites(self.year)

        self.assertFalse(rows[0]["registered"])
        self.assertTrue(rows[-1]["registered"])


class ResolveContactTests(InviteTestCase):
    def test_a_name_from_a_journal_finds_the_contact(self):
        contact = TeacherContact.objects.create(name="Юлия Галкина", chat_id="1", is_active=True)

        self.assertEqual(resolve_contact_by_name("Юлия Галкина"), contact)

    def test_the_other_spelling_finds_the_same_contact(self):
        contact = TeacherContact.objects.create(name="Юлия Галкина", chat_id="1", is_active=True)

        self.assertEqual(resolve_contact_by_name("Yuliya Galkina"), contact)

    def test_an_unknown_name_finds_nothing(self):
        self.assertIsNone(resolve_contact_by_name("Кто-то Новый"))

    def test_contact_for_teacher_tries_both_spellings(self):
        contact = TeacherContact.objects.create(name="Yuliya Galkina", chat_id="1", is_active=True)

        self.assertEqual(contact_for_teacher(self.galkina), contact)


class InviteViewTests(InviteTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("notifications:teacher_invites")
        self.csv_url = reverse("notifications:teacher_invites_csv")
        self.user = get_user_model().objects.create_user("zavuch", password="invite-tests")

    def _grant(self, *codenames):
        self.user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_denied_without_permission(self):
        self.client.force_login(self.user)

        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_lists_a_link_per_teacher(self):
        self._grant("send_reminders")

        with self.settings(TELEGRAM_BOT_USERNAME=BOT):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Юлия Галкина")
        self.assertContains(response, "start=register_")

    def test_says_when_the_bot_name_is_missing(self):
        self._grant("send_reminders")

        with self.settings(TELEGRAM_BOT_USERNAME=""):
            response = self.client.get(self.url)

        self.assertContains(response, "TELEGRAM_BOT_USERNAME")

    def test_the_csv_holds_the_same_rows(self):
        self._grant("send_reminders")

        with self.settings(TELEGRAM_BOT_USERNAME=BOT):
            response = self.client.get(self.csv_url)

        body = response.content.decode("utf-8-sig")
        self.assertIn("telegram-invites.csv", response["Content-Disposition"])
        self.assertIn("Юлия Галкина", body)
        self.assertIn("start=register_", body)
