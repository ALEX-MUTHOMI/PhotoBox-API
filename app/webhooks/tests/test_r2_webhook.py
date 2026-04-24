import hashlib
import hmac
import json
import time

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.models import Event, Photo, Scene


User = get_user_model()
TEST_SECRET = "test-webhook-secret-do-not-use-in-prod"


def _sign(timestamp: int, payload_bytes: bytes, secret: str = TEST_SECRET) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("ascii") + payload_bytes,
        hashlib.sha256,
    ).hexdigest()


@override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
class R2WebhookSecurityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = reverse("r2-ingestion-webhook")

        user = User.objects.create_user(
            email="webhook@example.com",
            password="StrongPassword123!",
            name="Webhook User",
            accepted_terms=True,
        )
        workspace = Workspace.objects.create(user=user, business_name="Webhook Studio")
        event = Event.objects.create(workspace=workspace, title="Webhook Event", slug="webhook-event")
        scene = Scene.objects.create(event=event, title="Webhook Scene")
        self.asset = Photo.objects.create(
            scene=scene,
            original_filename="asset.jpg",
            file_size_bytes=2048,
            r2_object_key="raw/tenant_1/scene_1/asset.jpg",
            status="PENDING",
            is_processed=False,
        )

    def _post(
        self,
        payload,
        *,
        timestamp=None,
        signed_timestamp=None,
        signature_secret=TEST_SECRET,
        include_signature=True,
    ):
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        transmitted_ts = timestamp if timestamp is not None else None
        signed_ts = (
            int(time.time())
            if signed_timestamp is None and transmitted_ts is None
            else (signed_timestamp if signed_timestamp is not None else transmitted_ts)
        )
        headers = {}
        if include_signature:
            headers["HTTP_X_CLOUDFLARE_SIGNATURE"] = _sign(
                signed_ts,
                payload_bytes,
                signature_secret,
            )
        if transmitted_ts is not None:
            headers["HTTP_WEBHOOK_TIMESTAMP"] = str(transmitted_ts)
        return self.client.post(
            self.url,
            data=payload_bytes,
            content_type="application/json",
            **headers,
        )

    def test_missing_signature_returns_403(self):
        response = self._post(
            {"action": "PutObject", "r2_object_key": self.asset.r2_object_key, "size": 2048},
            timestamp=int(time.time()),
            include_signature=False,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_signature_returns_403(self):
        response = self._post(
            {"action": "PutObject", "r2_object_key": self.asset.r2_object_key, "size": 2048},
            timestamp=int(time.time()),
            signature_secret="wrong-secret",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_missing_timestamp_returns_403(self):
        response = self._post(
            {"action": "PutObject", "r2_object_key": self.asset.r2_object_key, "size": 2048},
            timestamp=None,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "PENDING")

    def test_forged_timestamp_returns_403(self):
        signed_ts = int(time.time())
        response = self._post(
            {"action": "PutObject", "r2_object_key": self.asset.r2_object_key, "size": 2048},
            timestamp=signed_ts + 15,
            signed_timestamp=signed_ts,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "PENDING")

    def test_expired_timestamp_returns_403(self):
        response = self._post(
            {"action": "PutObject", "r2_object_key": self.asset.r2_object_key, "size": 2048},
            timestamp=int(time.time()) - 600,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "PENDING")

    def test_valid_putobject_transitions_asset_to_ready(self):
        response = self._post(
            {"action": "PutObject", "r2_object_key": self.asset.r2_object_key, "size": 1024},
            timestamp=int(time.time()),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "READY")
        self.assertTrue(self.asset.is_processed)

    def test_size_mismatch_quarantines_asset(self):
        response = self._post(
            {"action": "PutObject", "r2_object_key": self.asset.r2_object_key, "size": 999999},
            timestamp=int(time.time()),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "quarantined")
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "QUARANTINED")

    def test_unknown_key_returns_200_ignored(self):
        response = self._post(
            {"action": "PutObject", "r2_object_key": "raw/unknown.jpg", "size": 2048},
            timestamp=int(time.time()),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ignored")

    def test_non_put_action_is_ignored(self):
        response = self._post(
            {"action": "DeleteObject", "r2_object_key": self.asset.r2_object_key, "size": 2048},
            timestamp=int(time.time()),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ignored")
