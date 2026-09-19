from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class LoginFlowTests(TestCase):
    """The default LOGIN_URL used to have no route, so protected pages answered 404."""

    def setUp(self):
        self.password = "login-flow-tests-pwd"
        self.user = get_user_model().objects.create_user("zavuch", password=self.password)
        self.login_url = reverse("login")

    def test_login_page_is_reachable(self):
        response = self.client.get(self.login_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/login.html")
        self.assertContains(response, "Вход в систему")

    def test_protected_page_redirects_to_a_working_login_page(self):
        protected = reverse("journal_links:list_links")

        response = self.client.get(protected)

        self.assertRedirects(response, f"{self.login_url}?next={protected}", target_status_code=200)

    def test_valid_credentials_log_the_user_in(self):
        response = self.client.post(
            self.login_url,
            {"username": "zavuch", "password": self.password},
        )

        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_login_honours_the_next_parameter(self):
        target = reverse("job_run_list")

        response = self.client.post(
            self.login_url,
            {"username": "zavuch", "password": self.password, "next": target},
        )

        self.assertRedirects(response, target, fetch_redirect_response=False)

    def test_wrong_password_shows_an_error_and_does_not_log_in(self):
        response = self.client.post(
            self.login_url,
            {"username": "zavuch", "password": "wrong"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неверное имя пользователя или пароль")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_requires_post(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("logout"))

        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_logout_via_post_ends_the_session(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse("logout"))

        self.assertRedirects(response, self.login_url, fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
