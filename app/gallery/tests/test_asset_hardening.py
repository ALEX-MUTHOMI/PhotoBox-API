import hashlib
import hmac
import io
import json
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image as PILImage
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.models import Event, Photo, Scene


User = get_user_model()
FAST_LANE_URL = reverse("gallery:fastlane-photo-list")
HEAVY_LANE_URL = reverse("bulk-ingest")
R2_WEBHOOK_URL = reverse("r2-ingestion-webhook")
TEST_WEBHOOK_SECRET = "test-asset-hardening-webhook-secret"


def _create_user(email, password="HardenedPass123!"):
    return User.objects.create_user(
        email=email,
        password=password,
        name=email.split("@")[0],
        accepted_terms=True,
    )


def _create_full_tenant(
    user,
    *,
    business_name="Studio",
    event_title="Wedding",
    event_slug="wedding",
    scene_title="Ceremony",
):
    workspace = Workspace.objects.create(user=user, business_name=business_name)
    event = Event.objects.create(workspace=workspace, title=event_title, slug=event_slug)
    scene = Scene.objects.create(event=event, title=scene_title)
    return workspace, event, scene


def _generate_valid_image(filename="hardening_test.jpg"):
    buffer = io.BytesIO()
    PILImage.new("RGB", (64, 64), color=(0, 128, 255)).save(buffer, "JPEG")
    buffer.seek(0)
    return SimpleUploadedFile(filename, buffer.read(), content_type="image/jpeg")


def _sign_webhook(timestamp: int, payload_bytes: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("ascii") + payload_bytes,
        hashlib.sha256,
    ).hexdigest()


def _post_signed_webhook(client, payload: dict, *, timestamp: int | None = None):
    transmitted_ts = timestamp if timestamp is not None else None
    signed_ts = int(time.time()) if transmitted_ts is None else int(transmitted_ts)
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "HTTP_X_CLOUDFLARE_SIGNATURE": _sign_webhook(signed_ts, payload_bytes),
    }
    if transmitted_ts is not None:
        headers["HTTP_WEBHOOK_TIMESTAMP"] = str(transmitted_ts)
    return client.post(
        R2_WEBHOOK_URL,
        data=payload_bytes,
        content_type="application/json",
        **headers,
    )


class CrossTenantAssetHijackTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.attacker = _create_user("attacker@evil.com")
        self.attacker_workspace, _, _ = _create_full_tenant(
            self.attacker,
            business_name="Attacker Studio",
            event_slug="attacker-event",
        )
        self.client.force_authenticate(self.attacker)

        self.victim = _create_user("victim@studio.com")
        _, _, self.victim_scene = _create_full_tenant(
            self.victim,
            business_name="Victim Studio",
            event_slug="victim-event",
        )

    @patch("gallery.tasks.process_fast_lane_asset")
    def test_fast_lane_rejects_cross_tenant_scene_upload(self, mock_task):
        response = self.client.post(
            FAST_LANE_URL,
            {
                "scene": str(self.victim_scene.id),
                "image_file": _generate_valid_image(),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        mock_task.delay.assert_not_called()
        self.assertEqual(Photo.objects.filter(scene=self.victim_scene).count(), 0)
        self.attacker_workspace.refresh_from_db()
        self.assertEqual(self.attacker_workspace.storage_used_bytes, 0)

    def test_heavy_lane_rejects_cross_tenant_manifest(self):
        response = self.client.post(
            HEAVY_LANE_URL,
            {
                "scene_id": str(self.victim_scene.id),
                "files": [
                    {
                        "filename": "stolen_shot.jpg",
                        "file_size": 1024,
                        "client_reference_id": "ref-001",
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Photo.objects.filter(scene=self.victim_scene).exists())


@override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET)
class HeavyLaneSizeMismatchQuarantineTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user = _create_user("uploader@studio.com")
        _, _, scene = _create_full_tenant(user, business_name="Upload Studio", event_slug="upload-gig")
        self.asset = Photo.objects.create(
            scene=scene,
            original_filename="wedding_hero.jpg",
            file_size_bytes=5 * 1024 * 1024,
            r2_object_key="raw/tenant_1/scene_1/uuid_hero.jpg",
            status="PENDING",
            is_processed=False,
        )

    @patch("ingestion.views.r2_object_size", return_value=5 * 1024 * 1024 * 1024)
    def test_heavy_lane_size_mismatch_quarantines_asset(self, _mock_head):
        response = _post_signed_webhook(
            self.client,
            {
                "action": "PutObject",
                "r2_object_key": self.asset.r2_object_key,
                "size": 5 * 1024 * 1024 * 1024,
            },
            timestamp=int(time.time()),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "quarantined")
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "QUARANTINED")
        self.assertFalse(self.asset.is_processed)

    @patch("ingestion.views.r2_object_size", return_value=1024)
    def test_smaller_actual_size_transitions_to_ready(self, _mock_head):
        response = _post_signed_webhook(
            self.client,
            {
                "action": "PutObject",
                "r2_object_key": self.asset.r2_object_key,
                "size": 1024,
            },
            timestamp=int(time.time()),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "success")
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "READY")
        self.assertTrue(self.asset.is_processed)


@override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET)
class CloudflareWebhookReplayAttackTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user = _create_user("replay@studio.com")
        _, _, scene = _create_full_tenant(user, business_name="Replay Studio", event_slug="replay-gig")
        self.asset = Photo.objects.create(
            scene=scene,
            original_filename="keynote_01.jpg",
            file_size_bytes=2048,
            r2_object_key="raw/tenant_replay/scene_1/keynote_01.jpg",
            status="PENDING",
            is_processed=False,
        )

    def test_stale_timestamp_is_rejected(self):
        response = _post_signed_webhook(
            self.client,
            {
                "action": "PutObject",
                "r2_object_key": self.asset.r2_object_key,
                "size": 2048,
            },
            timestamp=int(time.time()) - (15 * 60),
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "PENDING")

    def test_missing_timestamp_is_rejected(self):
        response = _post_signed_webhook(
            self.client,
            {
                "action": "PutObject",
                "r2_object_key": self.asset.r2_object_key,
                "size": 2048,
            },
            timestamp=None,
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "PENDING")


@override_settings(
    CLOUDINARY_CLOUD_NAME="photobox-prod",
    CLOUDFLARE_R2_DOMAIN="cdn.photobox-vault.com",
)
class CloudinaryURLStructuralTests(TestCase):
    def setUp(self):
        user = _create_user("delivery@studio.com")
        _, _, self.scene = _create_full_tenant(user, business_name="Delivery Studio", event_slug="delivery-gig")

    def test_delivery_url_uses_cloudinary_fetch_proxy(self):
        photo = Photo.objects.create(
            scene=self.scene,
            original_filename="ceremony_001.jpg",
            file_size_bytes=8_000_000,
            r2_object_key="fast-lane/tenant_1/photo_abc/ceremony_001.jpg",
            status="READY",
            is_processed=True,
        )

        url = photo.delivery_url

        self.assertIsNotNone(url)
        self.assertIn("photobox-prod", url)
        self.assertIn("/image/fetch/", url)
        self.assertNotIn("/image/upload/", url)
        self.assertIn("q_auto", url)
        self.assertIn("f_webp", url)
        self.assertIn("cdn.photobox-vault.com", url)
        self.assertIn(photo.r2_object_key, url)
