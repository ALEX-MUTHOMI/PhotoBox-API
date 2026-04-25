import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.models import Event, Photo, Scene
from gallery.tasks import process_fast_lane_asset


User = get_user_model()
TEST_SECRET = "test-webhook-secret-do-not-use-in-prod"


@override_settings(
    CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET,
    CLOUDFLARE_R2_ENDPOINT="https://test.r2.cloudflarestorage.com",
    CLOUDFLARE_R2_BUCKET_NAME="test-bucket",
    CLOUDFLARE_ACCESS_KEY_ID="test-key",
    CLOUDFLARE_SECRET_ACCESS_KEY="test-secret",
)
class PipelineIntegrityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("r2-ingestion-webhook")

        self.user = User.objects.create_user(
            email="pipeline@example.com",
            password="StrongPassword123!",
            name="Pipeline User",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Pipeline Studio",
            storage_used_bytes=4096,
        )
        self.event = Event.objects.create(
            workspace=self.workspace,
            title="Pipeline Event",
            slug="pipeline-event",
        )
        self.scene = Scene.objects.create(event=self.event, title="Pipeline Scene")

    def _post_webhook(self, payload, timestamp=None):
        ts = timestamp or int(time.time())
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(
            TEST_SECRET.encode("utf-8"),
            f"{ts}.".encode("ascii") + payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        return self.client.post(
            self.url,
            data=payload_bytes,
            content_type="application/json",
            HTTP_X_CLOUDFLARE_SIGNATURE=signature,
            HTTP_WEBHOOK_TIMESTAMP=str(ts),
        )

    @patch("gallery.storage.get_r2_client")
    def test_pending_photo_self_heals_when_r2_has_file(self, mock_get_r2_client):
        photo = Photo.objects.create(
            scene=self.scene,
            original_filename="ready.jpg",
            file_size_bytes=1024,
            status="PENDING",
            is_processed=False,
            r2_object_key="fast-lane/tenant_1/photo_1/ready.jpg",
        )
        mock_client = MagicMock()
        mock_client.head_object.return_value = {"ContentLength": 1024}
        mock_get_r2_client.return_value = mock_client

        result = process_fast_lane_asset(photo_id=str(photo.id))

        self.assertEqual(result["status"], "self_healed")
        photo.refresh_from_db()
        self.assertEqual(photo.status, "READY")
        self.assertTrue(photo.is_processed)

    @patch("gallery.storage.get_r2_client")
    def test_abandoned_upload_refunds_quota_and_deletes_photo(self, mock_get_r2_client):
        photo = Photo.objects.create(
            scene=self.scene,
            original_filename="abandoned.jpg",
            file_size_bytes=2048,
            status="PENDING",
            is_processed=False,
            r2_object_key="fast-lane/tenant_1/photo_1/abandoned.jpg",
        )
        mock_client = MagicMock()
        mock_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404"}},
            "HeadObject",
        )
        mock_get_r2_client.return_value = mock_client

        result = process_fast_lane_asset(photo_id=str(photo.id))

        self.assertEqual(result["status"], "abandoned_and_refunded")
        self.assertFalse(Photo.objects.filter(id=photo.id).exists())
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 2048)

    @patch("gallery.storage.get_r2_client")
    def test_quota_refund_is_clamped_at_zero(self, mock_get_r2_client):
        self.workspace.storage_used_bytes = 100
        self.workspace.save(update_fields=["storage_used_bytes"])
        photo = Photo.objects.create(
            scene=self.scene,
            original_filename="overshoot.jpg",
            file_size_bytes=500,
            status="PENDING",
            is_processed=False,
            r2_object_key="fast-lane/tenant_1/photo_1/overshoot.jpg",
        )
        mock_client = MagicMock()
        mock_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404"}},
            "HeadObject",
        )
        mock_get_r2_client.return_value = mock_client

        process_fast_lane_asset(photo_id=str(photo.id))

        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 0)

    def test_task_marks_failed_when_filename_is_unsafe(self):
        photo = Photo.objects.create(
            scene=self.scene,
            original_filename="../../etc/passwd",
            file_size_bytes=512,
            status="PENDING",
            is_processed=False,
            r2_object_key="",
        )

        result = process_fast_lane_asset(photo_id=str(photo.id))

        self.assertEqual(result["reason"], "no_safe_r2_key")
        photo.refresh_from_db()
        self.assertEqual(photo.status, "FAILED")

    def test_heavy_lane_webhook_transitions_pending_asset_to_ready(self):
        photo = Photo.objects.create(
            scene=self.scene,
            original_filename="heavy.jpg",
            file_size_bytes=1024,
            status="PENDING",
            is_processed=False,
            r2_object_key="raw/tenant_1/scene_1/heavy.jpg",
        )

        response = self._post_webhook(
            {"action": "PutObject", "r2_object_key": photo.r2_object_key, "size": 1024}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        photo.refresh_from_db()
        self.assertEqual(photo.status, "READY")
        self.assertTrue(photo.is_processed)
