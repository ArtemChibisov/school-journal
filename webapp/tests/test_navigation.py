"""Меню и разделы: одно описание, две отрисовки."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from webapp.navigation import SECTIONS, visible_sections


class StructureTests(TestCase):
    def test_every_page_says_what_it_is_for(self):
        for section in SECTIONS:
            self.assertTrue(section.description, section.title)
            for item in section.items:
                self.assertTrue(item.description, f"{section.title} / {item.title}")

    def test_every_page_is_reachable(self):
        """Имя маршрута, которого нет, тихо выпало бы из меню — пусть падает здесь."""

        from django.urls import reverse as resolve

        for section in SECTIONS:
            for item in section.items:
                resolve(item.url_name)

    def test_no_page_appears_twice(self):
        names = [item.url_name for section in SECTIONS for item in section.items]

        self.assertEqual(sorted(names), sorted(set(names)))


class VisibilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("zavuch", password="x")

    def _sections(self, path=""):
        return visible_sections(get_user_model().objects.get(pk=self.user.pk), path)

    def test_a_user_without_rights_sees_nothing(self):
        """Каждая страница панели чего-то требует — пустых пунктов в меню не остаётся."""

        titles = {item["title"] for section in self._sections() for item in section["items"]}

        self.assertEqual(titles, set())

    def test_rights_open_the_pages_they_cover(self):
        self.user.user_permissions.add(Permission.objects.get(codename="view_jobrun"))

        titles = {item["title"] for section in self._sections() for item in section["items"]}

        self.assertIn("Отчет заполненности", titles)
        self.assertNotIn("Контакты родителей", titles)

    def test_an_empty_section_is_not_shown(self):
        self.assertEqual(self._sections(), [])

    def test_rights_bring_the_section_back(self):
        self.user.user_permissions.add(Permission.objects.get(codename="view_jobrun"))

        keys = {section["key"] for section in self._sections()}

        self.assertEqual(keys, {"module", "year", "system"})

    def test_the_current_page_is_marked(self):
        self.user.user_permissions.add(Permission.objects.get(codename="view_jobrun"))

        sections = self._sections("/reminders/")
        active = [
            item["title"]
            for section in sections
            for item in section["items"]
            if item["active"]
        ]

        self.assertEqual(active, ["Напоминания"])

    def test_pages_under_a_shared_prefix_do_not_light_up_together(self):
        """«Классы и таблицы» живут на /links/, как и две страницы проверок."""

        self.user.user_permissions.add(
            *Permission.objects.filter(codename__in=["view_jobrun", "view_classsheetlink"])
        )

        active = [
            item["title"]
            for section in self._sections("/links/grade-values/")
            for item in section["items"]
            if item["active"]
        ]

        self.assertEqual(active, ["Проверка оценок"])


class SidebarTests(TestCase):
    """В меню видны все разделы, а страницы — только текущего.

    Девятнадцать ссылок подряд читаются как одна длинная строка: ищешь нужную глазами каждый
    раз заново. Раскрытый раздел — тот, в котором находишься.
    """

    def setUp(self):
        self.user = get_user_model().objects.create_user("zavuch", password="x")
        self.user.user_permissions.add(
            *Permission.objects.filter(codename__in=["view_jobrun", "view_parentcontact"])
        )
        self.client.force_login(self.user)

    def test_only_the_current_section_shows_its_pages(self):
        response = self.client.get(reverse("section", args=["year"])).content.decode()

        self.assertIn("Состав классов", response)
        self.assertNotIn("Сохранение в PDF", response)

    def test_every_section_heading_is_always_there(self):
        response = self.client.get(reverse("section", args=["year"])).content.decode()

        for title in ("Работа с модулем", "Настройки года", "Служебное"):
            self.assertIn(title, response)

    def test_a_page_inside_a_section_keeps_it_open(self):
        response = self.client.get(reverse("pipeline:parent_mail_settings")).content.decode()

        self.assertIn("Контакты родителей", response)
        self.assertIn("Состав классов", response)


class PageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("zavuch", password="x")
        self.user.user_permissions.add(Permission.objects.get(codename="view_jobrun"))
        self.client.force_login(self.user)

    def test_the_home_page_lists_the_sections(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Работа с модулем")
        self.assertContains(response, "Кто из преподавателей ещё не заполнил")

    def test_a_section_page_lists_its_pages(self):
        response = self.client.get(reverse("section", args=["module"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Сохранение в PDF")

    def test_a_section_that_does_not_exist(self):
        self.assertEqual(self.client.get(reverse("section", args=["nope"])).status_code, 404)

    def test_a_section_the_user_may_not_see(self):
        response = self.client.get(reverse("section", args=["criteria"]))

        self.assertEqual(response.status_code, 404)

    def test_the_home_page_needs_a_login(self):
        self.client.logout()

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 302)
