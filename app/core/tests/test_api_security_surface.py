"""API security surface: JSON-only, CSP/nosniff, fail-closed throttles, JSON error handlers."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.models import Event


User = get_user_model()


@override_settings(
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
    FRONTEND_URL="https://app.photobox.test",
    SECURE_SSL_REDIRECT=False,
)
class ApiSecuritySurfaceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="StrongPassword123!",
            name="Owner",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(user=self.user, business_name="Studio")
        self.gallery = Event.objects.create(
            workspace=self.workspace,
            title="Wedding",
            slug="wed-sec",
            is_published=True,
        )
        self.gallery.set_pin("secret9")

    def test_accept_html_never_returns_browsable_forms(self):
        url = reverse("gallery_public:detail", args=[self.gallery.share_code])
        res = self.client.get(url, HTTP_ACCEPT="text/html")
        content_type = res.get("Content-Type", "")
        body = res.content.decode("utf-8", errors="replace")
        self.assertNotIn("text/html", content_type)
        self.assertNotIn("<form", body.lower())
        self.assertIn("application/json", content_type)
        self.assertIn("nosniff", res.get("X-Content-Type-Options", ""))
        self.assertIn("default-src", res.get("Content-Security-Policy", ""))

    def test_accept_star_returns_json(self):
        url = reverse("gallery_public:detail", args=[self.gallery.share_code])
        res = self.client.get(url, HTTP_ACCEPT="*/*")
        self.assertIn("application/json", res.get("Content-Type", ""))
        self.assertNotIn("<html", res.content.decode("utf-8", errors="replace").lower())

    def test_unknown_path_returns_json_404(self):
        res = self.client.get("/robots.txt")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("application/json", res.get("Content-Type", ""))
        self.assertEqual(res.json()["detail"], "Not found.")
        self.assertIn("nosniff", res.get("X-Content-Type-Options", ""))

    def test_health_omits_debug_flag(self):
        res = self.client.get(reverse("health-check"))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotIn("debug", res.json())

    def test_guest_access_cache_error_fail_closed_skips_hasher(self):
        url = reverse("gallery_public:guest-access", args=[self.gallery.share_code])
        with patch(
            "rest_framework.throttling.SimpleRateThrottle.allow_request",
            side_effect=ConnectionError("redis down"),
        ):
            with patch("gallery.models.check_password") as mocked:
                res = self.client.post(url, {"pin": "secret9"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        mocked.assert_not_called()
