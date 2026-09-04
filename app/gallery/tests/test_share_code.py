"""Phase A: share_code minting, uniqueness, collision retry cap."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import Workspace
from gallery.models import Event
from gallery.share_code import (
    SHARE_CODE_ALPHABET,
    SHARE_CODE_LENGTH,
    generate_share_code,
    is_valid_share_code_format,
)


User = get_user_model()


class ShareCodeUnitTests(TestCase):
    def test_generate_share_code_is_base62_and_length_10(self):
        code = generate_share_code()
        self.assertEqual(len(code), SHARE_CODE_LENGTH)
        self.assertTrue(all(ch in SHARE_CODE_ALPHABET for ch in code))
        self.assertTrue(is_valid_share_code_format(code))

    def test_rejects_sequential_and_short_tokens(self):
        for bad in ("1", "2", "aaa", "abcdefg", "!!!!!!!!!!!!"):
            self.assertFalse(is_valid_share_code_format(bad))


@override_settings(
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
    FRONTEND_URL="https://app.photobox.test",
)
class ShareCodeEventTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="StrongPassword123!",
            name="Owner",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(user=self.user, business_name="Studio")
        self.client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_event_create_mints_unique_share_codes(self):
        a = Event.objects.create(workspace=self.workspace, title="A", slug="a-1")
        b = Event.objects.create(workspace=self.workspace, title="B", slug="b-1")
        self.assertTrue(is_valid_share_code_format(a.share_code))
        self.assertTrue(is_valid_share_code_format(b.share_code))
        self.assertNotEqual(a.share_code, b.share_code)
        self.assertGreaterEqual(len(a.share_code), 8)
        self.assertLessEqual(len(a.share_code), 10)
        self.assertNotEqual(a.share_code, str(a.id))
        self.assertNotEqual(a.share_code, "1")

    def test_collision_raises_after_three_integrity_errors(self):
        from django.db import IntegrityError, models

        attempts = {"n": 0}

        def exploding(self, *args, **kwargs):
            attempts["n"] += 1
            raise IntegrityError(
                'duplicate key value violates unique constraint "gallery_event_share_code_key"'
            )

        event = Event(workspace=self.workspace, title="C", slug="c-dup-unique")
        with patch.object(models.Model, "save", exploding):
            with self.assertRaises(RuntimeError):
                event.save()
        self.assertEqual(attempts["n"], 3)

    def test_share_card_contains_share_code_not_pin(self):
        event = Event.objects.create(workspace=self.workspace, title="Wedding", slug="wed-1")
        event.set_pin("secret9")
        url = reverse("gallery:event-share-card", args=[event.id])
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn(event.share_code, res.data["public_url"])
        self.assertTrue(res.data["pin_set"])
        self.assertNotIn("gallery_pin", res.data)
        self.assertNotIn("gallery_pin_once", res.data)
        self.assertIn(event.share_code, res.data["whatsapp_text"])
        self.assertNotIn("secret9", res.data["whatsapp_text"])

    def test_cross_tenant_cannot_fetch_share_card(self):
        other = User.objects.create_user(
            email="other@example.com",
            password="StrongPassword123!",
            name="Other",
            accepted_terms=True,
        )
        Workspace.objects.create(user=other, business_name="Other Studio")
        event = Event.objects.create(workspace=self.workspace, title="Private", slug="priv-1")
        event.set_pin("secret9")
        other_client = APIClient()
        other_client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(other).access_token}"
        )
        res = other_client.get(reverse("gallery:event-share-card", args=[event.id]))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_returns_pin_once_share_card_never_does(self):
        res = self.client.post(
            reverse("gallery:event-list"),
            {"title": "PIN Once", "event_type": "WEDDING", "gallery_pin": "secret9"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data.get("gallery_pin_once"), "secret9")
        event_id = res.data["id"]
        card = self.client.get(reverse("gallery:event-share-card", args=[event_id]))
        self.assertNotIn("gallery_pin_once", card.data)
        self.assertNotIn("secret9", str(card.data))
        again = self.client.get(reverse("gallery:event-detail", args=[event_id]))
        self.assertNotIn("gallery_pin_once", again.data)
        self.assertNotIn("gallery_pin", again.data)

    def test_prod_cors_rejects_vercel_preview_origins(self):
        from django.core.exceptions import ImproperlyConfigured

        from core.cors_allowlist import assert_cors_origins_safe

        with self.assertRaises(ImproperlyConfigured):
            assert_cors_origins_safe(["https://pr-123.vercel.app"], debug=False)
        assert_cors_origins_safe(["https://app.photobox.test"], debug=False)
