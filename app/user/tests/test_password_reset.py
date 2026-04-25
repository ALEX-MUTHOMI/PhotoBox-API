from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APIClient

from user.throttles import PasswordResetRequestThrottle


User = get_user_model()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://app.photobox.test",
)
class PasswordResetFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="photographer@example.com",
            password="StrongPassword123!",
            name="Photographer",
            accepted_terms=True,
        )

    def test_password_reset_request_sends_email_without_enumerating_accounts(self):
        response = self.client.post(
            reverse("user:password_reset"),
            {"email": self.user.email},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("reset-password", mail.outbox[0].body)

        unknown = self.client.post(
            reverse("user:password_reset"),
            {"email": "missing@example.com"},
            format="json",
        )
        self.assertEqual(unknown.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)

    @patch.object(PasswordResetRequestThrottle, "THROTTLE_RATES", {"password_reset_request": "1/minute"})
    def test_password_reset_request_is_rate_limited(self):
        first = self.client.post(
            reverse("user:password_reset"),
            {"email": self.user.email},
            format="json",
        )
        second = self.client.post(
            reverse("user:password_reset"),
            {"email": self.user.email},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_password_reset_confirm_updates_password(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)

        response = self.client.post(
            reverse("user:password_reset_confirm"),
            {
                "uid": uid,
                "token": token,
                "password": "EvenStrongerPassword123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("EvenStrongerPassword123!"))
