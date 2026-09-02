import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.client_auth import hash_magic_link_token, issue_gallery_access_token
from gallery.models import (
    ClientAllowlist,
    Event,
    GalleryAccessRole,
    GalleryAccessSession,
    GalleryMagicLink,
    Photo,
    Scene,
    VisibilityChoices,
)
from gallery.throttles import GuestAccessThrottle, MagicLinkSendThrottle


User = get_user_model()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://app.photobox.test",
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
)
class DualLaneGalleryAuthTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="StrongPassword123!",
            name="Owner",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Studio",
        )
        self.gallery = Event.objects.create(
            workspace=self.workspace,
            title="Wedding Day",
            slug="wedding-day",
            is_published=True,
        )
        self.public_scene = Scene.objects.create(
            event=self.gallery,
            title="Ceremony",
            visibility=VisibilityChoices.PUBLIC,
        )
        self.client_scene = Scene.objects.create(
            event=self.gallery,
            title="Private Portraits",
            visibility=VisibilityChoices.CLIENT_ONLY,
        )
        self.public_photo = Photo.objects.create(
            scene=self.public_scene,
            visibility=VisibilityChoices.PUBLIC,
            original_filename="public.jpg",
            file_size_bytes=100,
            status="READY",
            is_processed=True,
            r2_object_key="gallery/public.jpg",
        )
        self.client_photo = Photo.objects.create(
            scene=self.client_scene,
            visibility=VisibilityChoices.CLIENT_ONLY,
            original_filename="client.jpg",
            file_size_bytes=100,
            status="READY",
            is_processed=True,
            r2_object_key="gallery/client.jpg",
        )
        ClientAllowlist.objects.create(gallery=self.gallery, email="bride@example.com")

    def _set_gallery_cookie(self, role, email="viewer@example.com", gallery_id=None):
        token = issue_gallery_access_token(
            gallery_id=gallery_id or self.gallery.id,
            email=email,
            role=role,
        )
        self.client.cookies["gallery_access"] = token

    def test_magic_link_request_creates_hashed_single_use_token_for_allowlisted_email(self):
        response = self.client.post(
            reverse("gallery_public:magic-link-request", args=[self.gallery.id]),
            {"email": "Bride@Example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(GalleryMagicLink.objects.count(), 1)

        record = GalleryMagicLink.objects.get()
        self.assertEqual(record.email, "bride@example.com")
        self.assertEqual(len(record.token_hash), 64)

        match = re.search(r"token=([A-Za-z0-9_\-]+)", mail.outbox[0].body)
        self.assertIsNotNone(match)
        raw_token = match.group(1)
        self.assertNotEqual(record.token_hash, raw_token)

    def test_magic_link_consume_creates_client_session_sets_cookie_and_is_single_use(self):
        raw_token = "raw-token-value"
        GalleryMagicLink.objects.create(
            gallery=self.gallery,
            email="bride@example.com",
            token_hash=hash_magic_link_token(raw_token),
            expires_at=timezone.now() + timedelta(minutes=15),
        )

        response = self.client.post(
            reverse("gallery_public:magic-link-consume"),
            {"token": raw_token},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            GalleryAccessSession.objects.filter(
                gallery=self.gallery,
                email="bride@example.com",
                role=GalleryAccessRole.CLIENT,
            ).count(),
            1,
        )
        self.assertEqual(GalleryMagicLink.objects.count(), 0)
        self.assertIn("gallery_access", response.cookies)
        self.assertTrue(response.cookies["gallery_access"]["httponly"])
        self.assertTrue(response.cookies["gallery_access"]["secure"])

        second = self.client.post(
            reverse("gallery_public:magic-link-consume"),
            {"token": raw_token},
            format="json",
        )
        self.assertEqual(second.status_code, status.HTTP_403_FORBIDDEN)

    def test_magic_link_consume_rejects_unpublished_gallery(self):
        raw_token = "raw-token-for-revoked-gallery"
        GalleryMagicLink.objects.create(
            gallery=self.gallery,
            email="bride@example.com",
            token_hash=hash_magic_link_token(raw_token),
            expires_at=timezone.now() + timedelta(minutes=15),
        )
        self.gallery.is_published = False
        self.gallery.save(update_fields=["is_published"])

        response = self.client.post(
            reverse("gallery_public:magic-link-consume"),
            {"token": raw_token},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(GalleryAccessSession.objects.count(), 0)
        self.assertEqual(GalleryMagicLink.objects.count(), 0)

    def test_guest_access_without_pin_is_rejected_when_gallery_has_pin(self):
        self.gallery.set_pin("4920")
        response = self.client.post(
            reverse("gallery_public:guest-access", args=[self.gallery.id]),
            {"email": "guest@example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(GalleryAccessSession.objects.count(), 0)

    def test_guest_access_with_wrong_pin_is_rejected(self):
        self.gallery.set_pin("4920")
        response = self.client.post(
            reverse("gallery_public:guest-access", args=[self.gallery.id]),
            {"email": "guest@example.com", "pin": "0000"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(GalleryAccessSession.objects.count(), 0)

    def test_guest_access_with_correct_pin_creates_session(self):
        self.gallery.set_pin("4920")
        response = self.client.post(
            reverse("gallery_public:guest-access", args=[self.gallery.id]),
            {"email": "guest@example.com", "pin": "4920"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            GalleryAccessSession.objects.filter(
                gallery=self.gallery,
                email="guest@example.com",
                role=GalleryAccessRole.GUEST,
            ).count(),
            1,
        )

    def test_guest_access_creates_guest_session_and_cookie(self):
        response = self.client.post(
            reverse("gallery_public:guest-access", args=[self.gallery.id]),
            {"email": "guest@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            GalleryAccessSession.objects.filter(
                gallery=self.gallery,
                email="guest@example.com",
                role=GalleryAccessRole.GUEST,
            ).count(),
            1,
        )
        self.assertIn("gallery_access", response.cookies)
        self.assertEqual(response.data["role"], GalleryAccessRole.GUEST)

    @patch.object(MagicLinkSendThrottle, "THROTTLE_RATES", {"magic_link_send": "1/minute"})
    def test_magic_link_request_is_rate_limited(self):
        first = self.client.post(
            reverse("gallery_public:magic-link-request", args=[self.gallery.id]),
            {"email": "bride@example.com"},
            format="json",
        )
        second = self.client.post(
            reverse("gallery_public:magic-link-request", args=[self.gallery.id]),
            {"email": "bride@example.com"},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @patch.object(GuestAccessThrottle, "THROTTLE_RATES", {"guest_access": "1/minute"})
    def test_guest_access_is_rate_limited(self):
        first = self.client.post(
            reverse("gallery_public:guest-access", args=[self.gallery.id]),
            {"email": "guest@example.com"},
            format="json",
        )
        second = self.client.post(
            reverse("gallery_public:guest-access", args=[self.gallery.id]),
            {"email": "another@example.com"},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_guest_gallery_view_filters_out_client_only_content(self):
        self._set_gallery_cookie(GalleryAccessRole.GUEST, email="guest@example.com")

        response = self.client.get(reverse("gallery_public:detail", args=[self.gallery.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["access"]["role"], GalleryAccessRole.GUEST)
        self.assertEqual(len(response.data["gallery"]["scenes"]), 1)
        self.assertEqual(response.data["gallery"]["scenes"][0]["title"], "Ceremony")
        self.assertEqual(len(response.data["gallery"]["scenes"][0]["photos"]), 1)

    def test_client_gallery_view_includes_client_only_content(self):
        self._set_gallery_cookie(GalleryAccessRole.CLIENT, email="bride@example.com")

        response = self.client.get(reverse("gallery_public:detail", args=[self.gallery.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["access"]["role"], GalleryAccessRole.CLIENT)
        self.assertEqual(len(response.data["gallery"]["scenes"]), 2)

    def test_gallery_view_rejects_scope_mismatch(self):
        other_gallery = Event.objects.create(
            workspace=self.workspace,
            title="Other",
            slug="other",
            is_published=True,
        )
        self._set_gallery_cookie(
            GalleryAccessRole.CLIENT,
            email="bride@example.com",
            gallery_id=other_gallery.id,
        )

        response = self.client.get(reverse("gallery_public:detail", args=[self.gallery.id]))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
