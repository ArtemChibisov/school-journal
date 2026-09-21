from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from google_integration.ids import folder_url, spreadsheet_url
from journal_links.models import DescriptorCriteriaReportTarget
from school_years.forms import SchoolYearForm, filled_link_count, missing_for_generation
from school_years.models import SchoolYear

ROSTER_URL = spreadsheet_url("1ZF69YxwebacQEs6sadKwXXyUNvGBy_QpwxhGWEH1laQ")
FOLDER_URL = folder_url("1EWQ3fTwkvlbfgzq-vOSt_U7kLA1ddJfS")


class SchoolYearFormTests(TestCase):
    def test_saves_with_only_a_name(self):
        form = SchoolYearForm(data={"name": "25/26", "is_active": "on"})

        self.assertTrue(form.is_valid(), form.errors)
        year = form.save()
        self.assertEqual(year.name, "25/26")

    def test_accepts_a_spreadsheet_link(self):
        form = SchoolYearForm(data={"name": "25/26", "class_roster_sheet_url": ROSTER_URL})

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["class_roster_sheet_url"], ROSTER_URL)

    def test_rejects_a_folder_link_in_a_spreadsheet_field(self):
        form = SchoolYearForm(data={"name": "25/26", "class_roster_sheet_url": FOLDER_URL})

        self.assertFalse(form.is_valid())
        self.assertIn("class_roster_sheet_url", form.errors)

    def test_rejects_a_spreadsheet_link_in_a_folder_field(self):
        form = SchoolYearForm(data={"name": "25/26", "register_root_0_3_url": ROSTER_URL})

        self.assertFalse(form.is_valid())
        self.assertIn("register_root_0_3_url", form.errors)

    def test_error_text_is_for_a_human(self):
        form = SchoolYearForm(data={"name": "25/26", "class_roster_sheet_url": "не ссылка"})

        self.assertFalse(form.is_valid())
        message = form.errors["class_roster_sheet_url"][0]
        self.assertIn("указана неверно", message)

    def test_whitespace_around_a_link_is_trimmed(self):
        form = SchoolYearForm(data={"name": "25/26", "class_roster_sheet_url": f"  {ROSTER_URL}  "})

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["class_roster_sheet_url"], ROSTER_URL)


class CompletenessTests(TestCase):
    def test_lists_what_blocks_generation(self):
        year = SchoolYear.objects.create(name="25/26", class_roster_sheet_url=ROSTER_URL)

        missing = missing_for_generation(year)

        self.assertNotIn("Таблица состава классов", missing)
        self.assertIn("Шаблон class register", missing)
        self.assertEqual(len(missing), 4)

    def test_nothing_missing_when_the_required_links_are_set(self):
        year = SchoolYear.objects.create(
            name="25/26",
            class_roster_sheet_url=ROSTER_URL,
            subject_teacher_sheet_url=ROSTER_URL,
            class_register_template_url=ROSTER_URL,
            register_root_0_3_url=FOLDER_URL,
            register_root_4_11_url=FOLDER_URL,
        )

        self.assertEqual(missing_for_generation(year), [])

    def test_counts_filled_links(self):
        year = SchoolYear.objects.create(name="25/26", class_roster_sheet_url=ROSTER_URL)

        filled, total = filled_link_count(year)

        self.assertEqual(filled, 1)
        self.assertEqual(total, 13)


class SchoolYearSettingsViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("zavuch", password="settings-tests")
        self.url = reverse("school_years:school_year_settings")

    def _grant(self, *codenames):
        self.user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        self.user = get_user_model().objects.get(pk=self.user.pk)

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_denied_without_permission(self):
        self.client.force_login(self.user)

        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_renders_an_empty_form_when_no_year_exists(self):
        self._grant("view_jobrun")
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "school_years/school_year_settings.html")
        self.assertContains(response, "Таблица состава классов")

    def test_viewer_without_edit_rights_sees_no_save_button(self):
        self._grant("view_jobrun")
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertContains(response, "нет прав на изменение настроек")
        self.assertFalse(response.context["can_edit"])

    def test_viewer_cannot_save(self):
        self._grant("view_jobrun")
        self.client.force_login(self.user)

        response = self.client.post(self.url, {"name": "25/26"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(SchoolYear.objects.count(), 0)

    def test_creates_a_year_with_the_roster_link(self):
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)

        response = self.client.post(
            self.url,
            {"name": "25/26", "is_active": "on", "class_roster_sheet_url": ROSTER_URL},
        )

        self.assertEqual(response.status_code, 302)
        year = SchoolYear.objects.get()
        self.assertEqual(year.class_roster_sheet_url, ROSTER_URL)
        self.assertEqual(year.created_by, self.user)
        self.assertEqual(year.updated_by, self.user)

    def test_updates_an_existing_year(self):
        year = SchoolYear.objects.create(name="25/26")
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)

        self.client.post(
            self.url,
            {"school_year": year.pk, "name": "25/26", "class_roster_sheet_url": ROSTER_URL},
        )

        year.refresh_from_db()
        self.assertEqual(year.class_roster_sheet_url, ROSTER_URL)
        self.assertEqual(SchoolYear.objects.count(), 1)

    def test_bad_link_is_reported_and_nothing_is_saved(self):
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)

        response = self.client.post(
            self.url,
            {"name": "25/26", "class_roster_sheet_url": "https://example.com/not-a-sheet"},
            follow=True,
        )

        self.assertEqual(SchoolYear.objects.count(), 0)
        self.assertContains(response, "указана неверно")

    def test_shows_what_is_still_missing(self):
        SchoolYear.objects.create(name="25/26", class_roster_sheet_url=ROSTER_URL)
        self._grant("view_jobrun")
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertContains(response, "ещё не готов к созданию журналов")
        self.assertContains(response, "Шаблон class register")

    def test_confirms_readiness_when_required_links_are_set(self):
        SchoolYear.objects.create(
            name="25/26",
            class_roster_sheet_url=ROSTER_URL,
            subject_teacher_sheet_url=ROSTER_URL,
            class_register_template_url=ROSTER_URL,
            register_root_0_3_url=FOLDER_URL,
            register_root_4_11_url=FOLDER_URL,
        )
        self._grant("view_jobrun")
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertContains(response, "Всё необходимое для создания журналов заполнено")

    def test_new_shows_a_blank_form_even_when_a_year_exists(self):
        SchoolYear.objects.create(name="25/26", class_roster_sheet_url=ROSTER_URL)
        self._grant("view_jobrun", "change_classsheetlink")
        self.client.force_login(self.user)

        response = self.client.get(self.url, {"school_year": "new"})

        self.assertIsNone(response.context["school_year"])
        self.assertNotContains(response, ROSTER_URL)


class MonitoringTargetBridgeTests(TestCase):
    """Saving the year must keep the legacy export target in step with it."""

    def setUp(self):
        self.user = get_user_model().objects.create_user("zavuch", password="bridge-tests")
        self.user.user_permissions.add(
            *Permission.objects.filter(codename__in=["view_jobrun", "change_classsheetlink"])
        )
        self.user = get_user_model().objects.get(pk=self.user.pk)
        self.client.force_login(self.user)
        self.url = reverse("school_years:school_year_settings")
        self.monitoring = spreadsheet_url("1MUzMjIYgbdTQjx2IJJTTt36qV-Ci1ml-H1o_dEoKBh8")

    def _post(self, monitoring_url, school_year=None):
        data = {"name": "26/27", "monitoring_sheet_url": monitoring_url}
        if school_year:
            data["school_year"] = school_year.pk
        return self.client.post(self.url, data)

    def test_creates_the_target_when_there_is_none(self):
        self._post(self.monitoring)

        target = DescriptorCriteriaReportTarget.objects.get()
        self.assertEqual(target.google_sheet_url, self.monitoring)
        self.assertTrue(target.is_active)

    def test_updates_the_existing_target(self):
        DescriptorCriteriaReportTarget.objects.create(
            name="Старый", google_sheet_url=spreadsheet_url("old-spreadsheet-id"), is_active=True
        )

        self._post(self.monitoring)

        self.assertEqual(DescriptorCriteriaReportTarget.objects.count(), 1)
        self.assertEqual(DescriptorCriteriaReportTarget.objects.get().google_sheet_url, self.monitoring)

    def test_empty_monitoring_link_leaves_the_target_alone(self):
        DescriptorCriteriaReportTarget.objects.create(
            name="Старый", google_sheet_url=spreadsheet_url("old-spreadsheet-id"), is_active=True
        )

        self._post("")

        self.assertEqual(
            DescriptorCriteriaReportTarget.objects.get().google_sheet_url,
            spreadsheet_url("old-spreadsheet-id"),
        )

    def test_saving_twice_does_not_multiply_targets(self):
        self._post(self.monitoring)
        year = SchoolYear.objects.get()

        self._post(self.monitoring, school_year=year)

        self.assertEqual(DescriptorCriteriaReportTarget.objects.count(), 1)
