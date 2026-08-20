from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.client_auth import (
    encode_gallery_access_session_cookie,
    issue_gallery_access_token,
)
from gallery.models import (
    Event,
    GalleryAccessRole,
    GalleryAccessSession,
    Photo,
    Scene,
)


User = get_user_model()


@override_settings(
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
    CLOUDINARY_CLOUD_NAME="photobox-test",
    CLOUDFLARE_R2_DOMAIN="media.example.test",
)
class ClientGallerySerializationSafetyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="xss-owner@example.com",
            password="StrongPassword123!",
            name="XSS Owner",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.owner,
            business_name="Unsafe Text Studio",
        )
        self.gallery = Event.objects.create(
            workspace=self.workspace,
            title='<script>alert("gallery")</script>',
            slug="safe-gallery-slug",
            is_published=True,
        )
        self.scene = Scene.objects.create(
            event=self.gallery,
            title='<img src=x onerror=alert("scene")>',
        )
        self.photo = Photo.objects.create(
            scene=self.scene,
            original_filename='"><img src=x onerror=alert("file")>.jpg',
            file_size_bytes=1024,
            r2_object_key="raw/tenant_1/scene_1/safe.jpg",
            status="READY",
            is_processed=True,
        )

        self.client = APIClient()
        session = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email="client@example.com",
            role=GalleryAccessRole.CLIENT,
        )
        self.client.cookies["gallery_access"] = issue_gallery_access_token(
            gallery_id=self.gallery.id,
            email=session.email,
            role=session.role,
        )
        self.client.cookies["gallery_session"] = (
            encode_gallery_access_session_cookie(session.id)
        )

    def test_client_gallery_payload_escapes_scriptable_titles_and_filename(self):
        response = self.client.get(
            reverse("gallery_public:detail", args=[self.gallery.id])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        gallery_payload = response.data["gallery"]
        scene_payload = gallery_payload["scenes"][0]
        photo_payload = scene_payload["photos"][0]

        for value in (
            gallery_payload["title"],
            scene_payload["title"],
            photo_payload["original_filename"],
        ):
            self.assertNotIn("<", value)
            self.assertNotIn(">", value)
            self.assertNotIn("onerror", value.lower())
            self.assertNotIn("<script", value.lower())

        self.gallery.refresh_from_db()
        self.scene.refresh_from_db()
        self.photo.refresh_from_db()
        self.assertEqual(self.gallery.title, '<script>alert("gallery")</script>')
        self.assertEqual(self.scene.title, '<img src=x onerror=alert("scene")>')
        self.assertEqual(
            self.photo.original_filename,
            '"><img src=x onerror=alert("file")>.jpg',
        )

    def test_client_gallery_payload_excludes_internal_keys_and_download_urls(self):
        response = self.client.get(
            reverse("gallery_public:detail", args=[self.gallery.id])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        photo_payload = response.data["gallery"]["scenes"][0]["photos"][0]

        self.assertNotIn("r2_object_key", photo_payload)
        self.assertNotIn("web_r2_object_key", photo_payload)
        self.assertNotIn("download_url", photo_payload)
        self.assertIn("delivery_url", photo_payload)

    def test_client_gallery_payload_rejects_scriptable_legacy_delivery_url(self):
        self.photo.r2_object_key = ""
        self.photo.optimized_url = "javascript:alert(1)"
        self.photo.save(update_fields=["r2_object_key", "optimized_url"])

        response = self.client.get(
            reverse("gallery_public:detail", args=[self.gallery.id])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        photo_payload = response.data["gallery"]["scenes"][0]["photos"][0]
        self.assertIsNone(photo_payload["delivery_url"])

    def test_client_gallery_payload_rejects_scriptable_cover_urls(self):
        self.gallery.cover_image_url = "javascript:alert(1)"
        self.gallery.cover_photo = "data:text/html,<script>alert(1)</script>"
        self.gallery.save(update_fields=["cover_image_url", "cover_photo"])

        response = self.client.get(
            reverse("gallery_public:detail", args=[self.gallery.id])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        gallery_payload = response.data["gallery"]
        self.assertIsNone(gallery_payload["cover_image_url"])
        self.assertIsNone(gallery_payload["cover_photo"])
