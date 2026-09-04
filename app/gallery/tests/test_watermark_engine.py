import hashlib
import hmac
import io
import json
import time
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image as PILImage
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.client_auth import (
    encode_gallery_access_session_cookie,
    issue_gallery_access_token,
)
from gallery.models import ClientAllowlist, Event, GalleryAccessRole, GalleryAccessSession, Photo, Scene
from gallery.tasks import generate_photo_web_derivative


User = get_user_model()
TEST_SECRET = "test-webhook-secret-do-not-use-in-prod"


class _FakeStreamingBody:
    def __init__(self, payload: bytes):
        self.buffer = io.BytesIO(payload)

    def read(self, size=-1):
        return self.buffer.read(size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.buffer.close()
        return False


@override_settings(
    CLOUDINARY_CLOUD_NAME="test-cloud",
    CLOUDFLARE_R2_DOMAIN="test-r2-domain.example.com",
    CLOUDFLARE_R2_ENDPOINT="https://test.r2.cloudflarestorage.com",
    CLOUDFLARE_R2_BUCKET_NAME="test-bucket",
    CLOUDFLARE_ACCESS_KEY_ID="test-key",
    CLOUDFLARE_SECRET_ACCESS_KEY="test-secret",
    CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET,
)
class WatermarkEngineTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="brand@example.com",
            password="StrongPassword123!",
            name="Brand Owner",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Brand Studio",
            watermark_logo=self._png_upload("watermark.png", size=(320, 140)),
            watermark_opacity=45,
        )
        self.gallery = Event.objects.create(
            workspace=self.workspace,
            title="Watermark Gallery",
            slug="watermark-gallery",
            is_published=True,
            cover_photo="https://cdn.example.com/covers/hero.jpg",
            typography_theme="modern-serif",
            color_theme="sandstone",
        )
        self.scene = Scene.objects.create(event=self.gallery, title="Ceremony")
        self.photo = Photo.objects.create(
            scene=self.scene,
            original_filename="hero.jpg",
            file_size_bytes=4096,
            status="READY",
            is_processed=True,
            r2_object_key="raw/tenant_1/scene_1/hero.jpg",
        )
        self.webhook_url = reverse("r2-ingestion-webhook")

    def _image_bytes(self, image_format, size=(512, 384), color=(40, 80, 120, 255)):
        stream = io.BytesIO()
        mode = "RGBA" if image_format == "PNG" else "RGB"
        image = PILImage.new(mode, size, color=color)
        save_kwargs = {"format": image_format}
        if image_format != "PNG":
            save_kwargs["quality"] = 95
        image.save(stream, **save_kwargs)
        return stream.getvalue()

    def _png_upload(self, filename, size=(128, 64)):
        return SimpleUploadedFile(
            filename,
            self._image_bytes("PNG", size=size),
            content_type="image/png",
        )

    def _post_webhook(self, payload, timestamp=None):
        ts = timestamp or int(time.time())
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(
            TEST_SECRET.encode("utf-8"),
            f"{ts}.".encode("ascii") + payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        return self.client.post(
            self.webhook_url,
            data=payload_bytes,
            content_type="application/json",
            HTTP_X_CLOUDFLARE_SIGNATURE=signature,
            HTTP_WEBHOOK_TIMESTAMP=str(ts),
        )

    def test_workspace_watermark_requires_real_png_payload(self):
        user = User.objects.create_user(
            email="badmark@example.com",
            password="StrongPassword123!",
            name="Bad Mark",
            accepted_terms=True,
        )
        invalid_logo = SimpleUploadedFile(
            "fake.png",
            self._image_bytes("JPEG"),
            content_type="image/png",
        )
        workspace = Workspace(
            user=user,
            business_name="Bad PNG Studio",
            watermark_logo=invalid_logo,
        )

        with self.assertRaises(ValidationError):
            workspace.full_clean()

    @patch("gallery.storage.upload_local_file_to_r2")
    @patch("gallery.storage.get_r2_client")
    def test_generate_web_derivative_uploads_webp_and_preserves_original_key(
        self,
        mock_get_r2_client,
        mock_upload,
    ):
        mock_client = MagicMock()
        mock_client.get_object.return_value = {
            "Body": _FakeStreamingBody(self._image_bytes("JPEG", size=(2800, 1800)))
        }
        mock_get_r2_client.return_value = mock_client

        def fake_upload(path, key, content_type="application/octet-stream"):
            with PILImage.open(path) as generated:
                self.assertEqual(generated.format, "WEBP")
                self.assertLessEqual(max(generated.size), 2400)
            self.assertEqual(content_type, "image/webp")
            self.assertTrue(key.startswith("web/tenant_"))
            return True

        mock_upload.side_effect = fake_upload

        result = generate_photo_web_derivative(photo_id=str(self.photo.id))

        self.assertEqual(result["status"], "completed")
        self.photo.refresh_from_db()
        self.assertEqual(self.photo.r2_object_key, "raw/tenant_1/scene_1/hero.jpg")
        self.assertTrue(self.photo.web_r2_object_key.startswith("web/tenant_"))
        self.assertEqual(self.photo.width, 2800)
        self.assertEqual(self.photo.height, 1800)

    def test_delivery_url_prefers_web_derivative_key_when_available(self):
        self.photo.web_r2_object_key = "web/tenant_1/gallery_test/photo_test.webp"
        self.photo.save(update_fields=["web_r2_object_key"])

        url = self.photo.delivery_url

        self.assertIn(self.photo.web_r2_object_key, url)
        self.assertNotIn(self.photo.r2_object_key, url)

    def test_public_gallery_detail_exposes_branding_fields(self):
        ClientAllowlist.objects.create(gallery=self.gallery, email="client@example.com")
        session = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email="client@example.com",
            role=GalleryAccessRole.CLIENT,
        )
        self.client.cookies["gallery_access"] = issue_gallery_access_token(
            gallery_id=self.gallery.id,
            email="client@example.com",
            role="CLIENT",
        )
        self.client.cookies["gallery_session"] = encode_gallery_access_session_cookie(session.id)

        response = self.client.get(reverse("gallery_public:detail", args=[self.gallery.share_code]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["gallery"]["cover_photo"], "https://cdn.example.com/covers/hero.jpg")
        self.assertEqual(response.data["gallery"]["typography_theme"], "modern-serif")
        self.assertEqual(response.data["gallery"]["color_theme"], "sandstone")

