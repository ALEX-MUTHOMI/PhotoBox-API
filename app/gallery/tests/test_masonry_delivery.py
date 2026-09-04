"""Phase B: web derivatives always, sized signed tiles, EXIF strip, no RAW fetch."""

import io
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
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
from gallery.cloudinary_delivery import TILE_TRANSFORM, is_safe_r2_object_key
from gallery.models import Event, GalleryAccessRole, GalleryAccessSession, Photo, Scene, VisibilityChoices
from gallery.storage import DOWNLOAD_URL_TTL_SECONDS
from gallery.tasks import generate_photo_web_derivative


User = get_user_model()


@override_settings(
    CLOUDINARY_CLOUD_NAME="test-cloud",
    CLOUDFLARE_R2_DOMAIN="cdn.example.test",
    CLOUDFLARE_R2_BUCKET_NAME="bucket",
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
)
class MasonryDeliveryTests(TestCase):
    def setUp(self):
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
            slug="wed",
            is_published=True,
            allow_downloads=False,
        )
        self.gallery.set_pin("secret9")
        self.scene = Scene.objects.create(
            event=self.gallery,
            title="Ceremony",
            visibility=VisibilityChoices.PUBLIC,
        )
        self.photo = Photo.objects.create(
            scene=self.scene,
            visibility=VisibilityChoices.PUBLIC,
            original_filename="a.jpg",
            file_size_bytes=100,
            status="READY",
            is_processed=True,
            r2_object_key="gallery/original.jpg",
            web_r2_object_key="gallery/web/a.webp",
            width=2000,
            height=1500,
            blurhash="LEHV6nWB2yk8pyo0adR*.7kCMdnj",
        )

    def test_tile_url_uses_web_key_and_w_480_never_raw(self):
        url = self.photo.delivery_url_tile
        self.assertIsNotNone(url)
        self.assertIn("w_480", url)
        self.assertIn("gallery/web/a.webp", url)
        self.assertNotIn("gallery/original.jpg", url)
        self.assertIn("s--", url)  # signed fragment

    def test_missing_web_key_yields_null_tile_not_raw(self):
        self.photo.web_r2_object_key = None
        self.photo.save(update_fields=["web_r2_object_key"])
        self.assertIsNone(self.photo.delivery_url_tile)
        self.assertIsNone(self.photo.delivery_url)

    def test_download_url_not_cloudinary_and_ttl_capped(self):
        self.assertLessEqual(DOWNLOAD_URL_TTL_SECONDS, 300)
        with patch("gallery.storage.get_r2_client") as mock_client:
            mock_client.return_value.generate_presigned_url.return_value = (
                "https://r2.example/obj?X-Amz-Expires=60"
            )
            url = self.photo.download_url
            self.assertIsNotNone(url)
            self.assertNotIn("cloudinary.com", url)

    def test_unsafe_keys_rejected(self):
        self.assertFalse(is_safe_r2_object_key("../x"))
        self.assertFalse(is_safe_r2_object_key("https://evil/x"))
        self.assertTrue(is_safe_r2_object_key("gallery/web/a.webp"))

    def test_guest_payload_omits_download_and_exif(self):
        client = APIClient()
        session = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email="guest@example.com",
            role=GalleryAccessRole.GUEST,
        )
        token = issue_gallery_access_token(
            self.gallery.id,
            "guest@example.com",
            GalleryAccessRole.GUEST,
            pin_version=self.gallery.pin_version,
        )
        client.cookies["gallery_access"] = token
        client.cookies["gallery_session"] = encode_gallery_access_session_cookie(session.id)
        res = client.get(reverse("gallery_public:detail", args=[self.gallery.share_code]))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        photo_payload = res.data["gallery"]["scenes"][0]["photos"][0]
        self.assertIsNone(photo_payload.get("download_url"))
        self.assertNotIn("exif_data", photo_payload)
        self.assertNotIn("client_phone", res.data["gallery"])
        self.assertIn("w_480", photo_payload["delivery_url"] or "")

    @patch("gallery.storage.get_r2_client")
    def test_web_derivative_without_watermark_and_strips_exif(self, mock_r2):
        from pathlib import Path

        img = PILImage.new("RGB", (32, 32), color=(10, 20, 30))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        raw = buf.getvalue()

        body = MagicMock()
        body.read.side_effect = [raw, b""]
        body.__enter__ = MagicMock(return_value=body)
        body.__exit__ = MagicMock(return_value=False)
        mock_r2.return_value.get_object.return_value = {"Body": body}

        captured = {}

        def capture_upload(path, key, content_type=None):
            captured["bytes"] = Path(path).read_bytes()
            captured["key"] = key
            return True

        photo = Photo.objects.create(
            scene=self.scene,
            visibility=VisibilityChoices.PUBLIC,
            original_filename="b.jpg",
            file_size_bytes=len(raw),
            status="READY",
            is_processed=True,
            r2_object_key="gallery/b.jpg",
            media_type="IMAGE",
        )
        with patch("gallery.storage.upload_local_file_to_r2", side_effect=capture_upload):
            result = generate_photo_web_derivative(str(photo.id))
        self.assertEqual(result["status"], "completed")
        photo.refresh_from_db()
        self.assertTrue(photo.web_r2_object_key)
        self.assertTrue(captured.get("bytes"))
        derived = PILImage.open(io.BytesIO(captured["bytes"]))
        derived_exif = derived.getexif()
        self.assertNotIn(34853, derived_exif)
        self.assertFalse(derived_exif.get(34853))
        self.assertIn(TILE_TRANSFORM.split(",")[0], "w_480")
