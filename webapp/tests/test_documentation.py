"""Документация внутри панели: инструкция, до которой надо дойти, — не инструкция."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from webapp.docs import DOCUMENTS, document_by_key, render


class DocumentTests(TestCase):
    def test_both_documents_exist_on_disk(self):
        for document in DOCUMENTS:
            self.assertTrue(document.path.exists(), document.filename)

    def test_each_one_says_who_it_is_for(self):
        for document in DOCUMENTS:
            self.assertTrue(document.title)
            self.assertTrue(document.description)

    def test_markdown_becomes_html(self):
        html = render(document_by_key("product"))

        self.assertIn("<h1", html)
        self.assertIn("<table", html)

    def test_only_the_listed_files_are_exposed(self):
        """В docs/ лежат контракты и планы — панель показывает не всё подряд."""

        self.assertEqual({document.filename for document in DOCUMENTS},
                         {"product_guide.md", "technical_overview.md"})


class DocumentationPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("zavuch", password="x")
        self.client.force_login(self.user)

    def test_the_product_guide_opens(self):
        response = self.client.get(reverse("documentation", args=["product"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Рассылка родителям")

    def test_the_technical_one_opens(self):
        response = self.client.get(reverse("documentation", args=["technical"]))

        self.assertEqual(response.status_code, 200)

    def test_an_unknown_document(self):
        self.assertEqual(
            self.client.get(reverse("documentation", args=["nope"])).status_code, 404
        )

    def test_the_home_page_offers_both(self):
        response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "Как пользоваться")
        self.assertContains(response, "Техническая документация")

    def test_reading_the_docs_needs_a_login(self):
        self.client.logout()

        response = self.client.get(reverse("documentation", args=["product"]))

        self.assertEqual(response.status_code, 302)
