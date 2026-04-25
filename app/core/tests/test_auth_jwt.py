from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken


User = get_user_model()


class JWTAuthenticationSecurityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="jwt@example.com",
            password="StrongPassword123!",
            name="JWT User",
            accepted_terms=True,
        )
        self.token_url = reverse("user:token")
        self.refresh_url = reverse("user:token_refresh")
        self.me_url = reverse("user:me")

    def test_cookie_based_refresh_flow_rotates_cookie(self):
        login_response = self.client.post(
            self.token_url,
            {"email": self.user.email, "password": "StrongPassword123!"},
            format="json",
        )

        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        self.assertIn("refresh", login_response.cookies)
        self.assertNotIn("refresh", login_response.data)

        refresh_client = APIClient()
        refresh_client.cookies["refresh"] = login_response.cookies["refresh"].value
        refresh_response = refresh_client.post(self.refresh_url, {}, format="json")

        self.assertEqual(refresh_response.status_code, status.HTTP_200_OK)
        self.assertIn("access", refresh_response.data)
        self.assertNotIn("refresh", refresh_response.data)
        self.assertIn("refresh", refresh_response.cookies)

    def test_refresh_token_blacklisted_after_rotation(self):
        refresh = str(RefreshToken.for_user(self.user))

        first_response = self.client.post(
            self.refresh_url,
            {"refresh": refresh},
            format="json",
        )
        self.assertEqual(first_response.status_code, status.HTTP_200_OK)

        second_response = APIClient().post(
            self.refresh_url,
            {"refresh": refresh},
            format="json",
        )

        self.assertEqual(second_response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_expired_access_token_returns_401(self):
        token = AccessToken.for_user(self.user)
        token.set_exp(from_time=timezone.now(), lifetime=timedelta(seconds=-1))

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_no_token_returns_401(self):
        response = APIClient().get(self.me_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
