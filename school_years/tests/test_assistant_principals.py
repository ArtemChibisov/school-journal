"""Who signs a class's reports, and the screen that decides it.

The name used to be a table inside the legacy helper, keyed by the class codes of a year that
had ended. Nineteen of this year's thirty-two classes matched nothing in it, so their reports
printed «Завуч | Assistant Principal:» and stopped there - and nothing on any screen said so.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from school_years.assistant_principals import coverage, principal_name_for_class
from school_years.models import AssistantPrincipal, ClassGroup, SchoolYear

BELOV = "Александр Белов | Alexander Belov"
CHERKASOVA = "Лидия Черкасова | Lydia Cherkasova"


class ResolveTests(TestCase):
    def setUp(self):
        self.year = SchoolYear.objects.create(name="26/27", is_active=True)
        AssistantPrincipal.objects.all().delete()

    def _class(self, class_code, grade_number, **overrides):
        return ClassGroup.objects.create(
            school_year=self.year,
            class_code=class_code,
            grade_number=grade_number,
            tutor_name="Тьютор",
            **overrides,
        )

    def test_the_name_comes_from_the_parallel(self):
        AssistantPrincipal.objects.create(name=BELOV, parallels=[4, 5, 6])
        self._class("4К1", 4)

        self.assertEqual(principal_name_for_class(self.year, "4К1"), BELOV)

    def test_every_class_of_a_parallel_gets_the_same_person(self):
        """The case the old table got wrong: 4А had a завуч and 4К1 did not."""

        AssistantPrincipal.objects.create(name=BELOV, parallels=[4])
        for code in ("4А", "4К1", "4К2"):
            self._class(code, 4)

        names = {principal_name_for_class(self.year, code) for code in ("4А", "4К1", "4К2")}

        self.assertEqual(names, {BELOV})

    def test_a_parallel_nobody_covers_gives_nothing_rather_than_a_guess(self):
        AssistantPrincipal.objects.create(name=BELOV, parallels=[4])
        self._class("9К", 9)

        self.assertEqual(principal_name_for_class(self.year, "9К"), "")

    def test_someone_switched_off_does_not_sign(self):
        AssistantPrincipal.objects.create(name=BELOV, parallels=[4], is_active=False)
        self._class("4А", 4)

        self.assertEqual(principal_name_for_class(self.year, "4А"), "")

    def test_a_class_the_roster_does_not_know(self):
        AssistantPrincipal.objects.create(name=BELOV, parallels=[4])

        self.assertEqual(principal_name_for_class(self.year, "4Z"), "")

    def test_an_unusual_split_needs_no_new_concept(self):
        """One parallel moved to another завуч is just a different list."""

        AssistantPrincipal.objects.create(name=CHERKASOVA, parallels=[0, 1, 2, 3, 4])
        AssistantPrincipal.objects.create(name=BELOV, parallels=[5, 6])
        self._class("4А", 4)
        self._class("5А", 5)

        self.assertEqual(principal_name_for_class(self.year, "4А"), CHERKASOVA)
        self.assertEqual(principal_name_for_class(self.year, "5А"), BELOV)

    def test_coverage_names_the_parallels_nobody_signs_for(self):
        AssistantPrincipal.objects.create(name=BELOV, parallels=[4])
        self._class("4А", 4)
        self._class("9К", 9)

        rows = {row["parallel"]: row["principal"] for row in coverage(self.year)}

        self.assertEqual(rows, {4: BELOV, 9: ""})

    def test_coverage_lists_the_classes_of_each_parallel(self):
        AssistantPrincipal.objects.create(name=BELOV, parallels=[4])
        for code in ("4К2", "4А", "4К1"):
            self._class(code, 4)

        self.assertEqual(coverage(self.year)[0]["classes"], ["4А", "4К1", "4К2"])


class SeedTests(TestCase):
    def test_the_three_from_the_legacy_table_are_there_from_the_start(self):
        """Carried over by migration: the people were right, only the keys had gone stale."""

        names = set(AssistantPrincipal.objects.values_list("name", flat=True))

        self.assertIn(CHERKASOVA, names)
        self.assertIn(BELOV, names)
        self.assertIn("Ольга Ткачук | Olga Tkachuk", names)

    def test_the_seeded_split_covers_every_parallel_the_school_has(self):
        covered = set()
        for principal in AssistantPrincipal.objects.all():
            covered |= set(principal.parallel_numbers)

        self.assertEqual(covered, set(range(0, 12)))


class ScreenTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("zavuch", password="x")
        self.user.user_permissions.add(
            *Permission.objects.filter(codename__in=["view_jobrun", "change_classsheetlink"])
        )
        self.client.force_login(self.user)
        self.year = SchoolYear.objects.create(name="26/27", is_active=True)
        ClassGroup.objects.create(
            school_year=self.year, class_code="4К1", grade_number=4, tutor_name="Т"
        )
        self.url = reverse("school_years:assistant_principals")

    def test_the_page_shows_who_covers_what(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "4К1")

    def test_someone_can_be_added(self):
        AssistantPrincipal.objects.all().delete()

        self.client.post(self.url, {"name": BELOV, "parallels": ["4", "5"], "is_active": "on"})

        principal = AssistantPrincipal.objects.get()
        self.assertEqual(principal.name, BELOV)
        self.assertEqual(principal.parallel_numbers, [4, 5])

    def test_someone_can_be_changed(self):
        AssistantPrincipal.objects.all().delete()
        principal = AssistantPrincipal.objects.create(name=BELOV, parallels=[4])

        self.client.post(
            self.url, {"pk": principal.pk, "name": BELOV, "parallels": ["7"], "is_active": "on"}
        )

        principal.refresh_from_db()
        self.assertEqual(principal.parallel_numbers, [7])
        self.assertEqual(AssistantPrincipal.objects.count(), 1)

    def test_someone_can_be_removed(self):
        AssistantPrincipal.objects.all().delete()
        principal = AssistantPrincipal.objects.create(name=BELOV, parallels=[4])

        self.client.post(self.url, {"delete": principal.pk})

        self.assertFalse(AssistantPrincipal.objects.exists())

    def test_removing_one_leaves_the_others(self):
        AssistantPrincipal.objects.all().delete()
        going = AssistantPrincipal.objects.create(name=BELOV, parallels=[4])
        staying = AssistantPrincipal.objects.create(name=CHERKASOVA, parallels=[0])

        self.client.post(self.url, {"delete": going.pk})

        self.assertEqual(list(AssistantPrincipal.objects.all()), [staying])

    def test_a_removal_does_not_also_save_the_form(self):
        """The delete button sits in its own form, so nothing is edited on the way out."""

        AssistantPrincipal.objects.all().delete()
        principal = AssistantPrincipal.objects.create(name=BELOV, parallels=[4])
        other = AssistantPrincipal.objects.create(name=CHERKASOVA, parallels=[0, 1, 2, 3])

        self.client.post(self.url, {"delete": principal.pk})

        other.refresh_from_db()
        self.assertEqual(other.parallel_numbers, [0, 1, 2, 3])

    def test_without_the_permission_nobody_can_be_removed(self):
        AssistantPrincipal.objects.all().delete()
        principal = AssistantPrincipal.objects.create(name=BELOV, parallels=[4])
        self.user.user_permissions.remove(Permission.objects.get(codename="change_classsheetlink"))
        self.client.force_login(get_user_model().objects.get(pk=self.user.pk))

        response = self.client.post(self.url, {"delete": principal.pk})

        self.assertEqual(response.status_code, 403)
        self.assertTrue(AssistantPrincipal.objects.filter(pk=principal.pk).exists())

    def test_without_the_permission_nothing_is_changed(self):
        self.user.user_permissions.remove(Permission.objects.get(codename="change_classsheetlink"))
        self.client.force_login(get_user_model().objects.get(pk=self.user.pk))
        before = AssistantPrincipal.objects.count()

        response = self.client.post(self.url, {"name": "Кто-то", "parallels": ["4"]})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(AssistantPrincipal.objects.count(), before)
