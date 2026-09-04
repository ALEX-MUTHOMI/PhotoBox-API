"""Phase A: PIN-only guest access, lockout-before-hash, headers, logout, pin_version."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.client_auth import (
    encode_gallery_access_session_cookie,
    issue_gallery_access_token,
)
from gallery.models import Event, GalleryAccessRole, GalleryAccessSession, Scene, VisibilityChoices


User = get_user_model()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://app.photobox.test",
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
    GALLERY_PIN_MAX_FAILED_ATTEMPTS=10,
    GALLERY_PIN_LOCKOUT_SECONDS=900,
    GALLERY_PIN_IP_ATTEMPTS_PER_MINUTE=5,
    GALLERY_PIN_IP_WINDOW_SECONDS=60,
    TRUST_CLOUDFLARE_CLIENT_IP=False,
    SECURE_REFERRER_POLICY="no-referrer",
    CORS_ALLOW_ALL_ORIGINS=False,
    CORS_ALLOW_CREDENTIALS=True,
    CORS_ALLOWED_ORIGINS=["https://app.photobox.test", "http://localhost:3000"],
)
class GuestPinOnlyTests(TestCase):
    def setUp(self):
        cache.clear()
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
            title="Wedding Day",
            slug="wedding-day",
            is_published=True,
        )
        self.gallery.set_pin("wedding42")
        Scene.objects.create(
            event=self.gallery,
            title="Ceremony",
            visibility=VisibilityChoices.PUBLIC,
        )

    def _guest_url(self, code=None):
        return reverse("gallery_public:guest-access", args=[code or self.gallery.share_code])

    def _detail_url(self, code=None):
        return reverse("gallery_public:detail", args=[code or self.gallery.share_code])

    def test_four_digit_pin_rejected(self):
        res = self.client.post(
            self._guest_url(),
            {"pin": "1234"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_guest_without_pin_no_cookies(self):
        with patch("gallery.models.check_password") as mocked:
            res = self.client.post(self._guest_url(), {}, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertNotIn("gallery_access", res.cookies)
        mocked.assert_not_called()

    def test_wrong_pin_no_cookies_and_check_password_gated_after_lock(self):
        from django.contrib.auth.hashers import check_password as real_check_password

        with patch("gallery.models.check_password", wraps=real_check_password) as mocked:
            for _ in range(5):
                res = self.client.post(
                    self._guest_url(),
                    {"pin": "wrong!!"},
                    format="json",
                    REMOTE_ADDR="203.0.113.10",
                )
                self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
                self.assertNotIn("gallery_access", res.cookies)
            # 6th from same IP hits per-IP cheap gate before hash
            res = self.client.post(
                self._guest_url(),
                {"pin": "wrong!!"},
                format="json",
                REMOTE_ADDR="203.0.113.10",
            )
            self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
            self.assertEqual(mocked.call_count, 5)

    def test_spoofed_xff_does_not_bypass_ip_limit(self):
        for i in range(5):
            self.client.post(
                self._guest_url(),
                {"pin": "wrong!!"},
                format="json",
                REMOTE_ADDR="198.51.100.1",
                HTTP_X_FORWARDED_FOR=f"203.0.113.{i}",
            )
        res = self.client.post(
            self._guest_url(),
            {"pin": "wrong!!"},
            format="json",
            REMOTE_ADDR="198.51.100.1",
            HTTP_X_FORWARDED_FOR="203.0.113.99",
        )
        self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_global_lockout_blocks_any_ip_without_hashing(self):
        # Exhaust gallery-global failures from many IPs (cap=10)
        for i in range(10):
            self.client.post(
                self._guest_url(),
                {"pin": "wrong!!"},
                format="json",
                REMOTE_ADDR=f"198.51.100.{i}",
            )
        with patch("gallery.models.check_password") as mocked:
            res = self.client.post(
                self._guest_url(),
                {"pin": "wrong!!"},
                format="json",
                REMOTE_ADDR="198.51.100.200",
            )
            self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
            mocked.assert_not_called()

    def test_redis_down_fail_closed_skips_hasher(self):
        from gallery.pin_gate import PinGateDecision, pin_gate_precheck

        with patch("gallery.pin_gate.cache.get", side_effect=ConnectionError("redis down")):
            decision = pin_gate_precheck(self.gallery.id, "203.0.113.10")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status, "redis_unavailable")

        with patch(
            "gallery.client_views.pin_gate_precheck",
            return_value=PinGateDecision(False, "redis_unavailable", retry_after=900),
        ):
            with patch("gallery.models.check_password") as mocked:
                res = self.client.post(
                    self._guest_url(),
                    {"pin": "wedding42"},
                    format="json",
                )
        self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        mocked.assert_not_called()
        self.assertNotIn("gallery_access", res.cookies)

    def test_correct_pin_without_email_sets_guest_cookies(self):
        res = self.client.post(
            self._guest_url(),
            {"pin": "wedding42"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["role"], GalleryAccessRole.GUEST)
        self.assertIn("gallery_access", res.cookies)
        self.assertTrue(res.data["email"].startswith("guest:"))
        self.assertIn("no-store", res["Cache-Control"])
        self.assertIn("Cookie", res["Vary"])
        self.assertEqual(res["Referrer-Policy"], "no-referrer")

    def test_guest_access_idempotent_with_existing_session(self):
        first = self.client.post(self._guest_url(), {"pin": "wedding42"}, format="json")
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        second = self.client.post(self._guest_url(), {"pin": "wedding42"}, format="json")
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(
            GalleryAccessSession.objects.filter(
                gallery=self.gallery, role=GalleryAccessRole.GUEST
            ).count(),
            1,
        )

    def test_anonymous_detail_has_no_title_cover_photos(self):
        res = self.client.get(self._detail_url())
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        body = res.json()
        self.assertNotIn("title", body)
        self.assertNotIn("cover_photo", body)
        self.assertNotIn("scenes", body)
        self.assertIn("no-store", res["Cache-Control"])
        self.assertIn("Cookie", res["Vary"])

    def test_unknown_and_unpublished_share_code_identical_404(self):
        unknown = self.client.get(reverse("gallery_public:detail", args=["AbCdEfGhIj"]))
        self.gallery.is_published = False
        self.gallery.save(update_fields=["is_published"])
        unpublished = self.client.get(self._detail_url())
        self.assertEqual(unknown.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(unpublished.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(unknown.json(), unpublished.json())

    def test_anonymous_uuid_detail_is_404(self):
        res = self.client.get(
            reverse("gallery_public:detail-uuid-trap", args=[self.gallery.id])
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_pin_rotate_invalidates_old_guest_cookie(self):
        login = self.client.post(self._guest_url(), {"pin": "wedding42"}, format="json")
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.gallery.set_pin("newpin99")
        res = self.client.get(self._detail_url())
        self.assertIn(res.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        again = self.client.post(self._guest_url(), {"pin": "newpin99"}, format="json")
        self.assertEqual(again.status_code, status.HTTP_200_OK)

    def test_logout_clears_cookies(self):
        login = self.client.post(self._guest_url(), {"pin": "wedding42"}, format="json")
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        logout = self.client.post(
            reverse("gallery_public:guest-logout", args=[self.gallery.share_code]),
            format="json",
        )
        self.assertEqual(logout.status_code, status.HTTP_200_OK)
        for name in ("gallery_access", "gallery_session"):
            self.assertIn(name, logout.cookies)
            morsel = logout.cookies[name]
            max_age = morsel.get("max-age")
            expires = str(morsel.get("expires") or "")
            self.assertTrue(
                max_age in ("0", 0) or "1970" in expires or morsel.value in ("", None),
                f"{name} was not deleted: value={morsel.value!r} max-age={max_age!r}",
            )
        # Test client honors Set-Cookie deletes — do not manually clear the jar.
        res = self.client.get(self._detail_url())
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_credentialed_cors_from_unknown_origin_is_rejected(self):
        res = self.client.get(
            self._detail_url(),
            HTTP_ORIGIN="https://evil.example",
        )
        self.assertNotEqual(res.get("Access-Control-Allow-Origin"), "https://evil.example")
        preflight = self.client.options(
            self._detail_url(),
            HTTP_ORIGIN="https://evilphotobox.app",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        )
        self.assertNotEqual(
            preflight.get("Access-Control-Allow-Origin"),
            "https://evilphotobox.app",
        )
