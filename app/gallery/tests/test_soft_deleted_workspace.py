"""Soft-deleted workspaces must not be reachable via public gallery doors."""
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.client_auth import (
    encode_gallery_access_session_cookie,
    hash_magic_link_token,
    issue_gallery_access_token,
)
from gallery.models import (
    ClientAllowlist,
    Event,
    GalleryAccessRole,
    GalleryAccessSession,
    GalleryMagicLink,
    Photo,
    Scene,
)

User = get_user_model()


@override_settings(
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
    FRONTEND_URL="https://app.photobox.test",
    SECURE_SSL_REDIRECT=False,
)
class SoftDeletedWorkspacePublicDoorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="soft@example.com",
            password="StrongPassword123!",
            name="Soft",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user, business_name="Studio"
        )
        self.gallery = Event.objects.create(
            workspace=self.workspace,
            title="Wedding",
            slug="soft-wed",
            is_published=True,
        )
        self.gallery.set_pin("secret9")
        self.scene = Scene.objects.create(event=self.gallery, title="Ceremony")
        Photo.objects.create(
            scene=self.scene,
            original_filename="a.jpg",
            status="READY",
            file_size_bytes=100,
            is_processed=True,
        )
        self.workspace.is_deleted = True
        self.workspace.save(update_fields=["is_deleted"])

    def test_public_detail_returns_404(self):
        url = reverse("gallery_public:detail", args=[self.gallery.share_code])
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_scene_list_returns_404(self):
        session = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email="guest@example.com",
            role=GalleryAccessRole.GUEST,
        )
        token = issue_gallery_access_token(
            gallery_id=self.gallery.id,
            email="guest@example.com",
            role=GalleryAccessRole.GUEST,
            pin_version=self.gallery.pin_version,
        )
        url = reverse(
            "gallery_public:scene-photos",
            args=[self.gallery.share_code, self.scene.id],
        )
        self.client.cookies["gallery_access"] = token
        self.client.cookies["gallery_session"] = encode_gallery_access_session_cookie(
            session.id
        )
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_magic_link_consume_returns_403(self):
        ClientAllowlist.objects.create(
            gallery=self.gallery, email="client@example.com"
        )
        raw = secrets.token_urlsafe(32)
        GalleryMagicLink.objects.create(
            gallery=self.gallery,
            email="client@example.com",
            token_hash=hash_magic_link_token(raw),
            expires_at=timezone.now() + timedelta(minutes=15),
        )
        url = reverse("gallery_public:magic-link-consume")
        res = self.client.post(url, {"token": raw}, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
