import hashlib
import hmac
import json
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from gallery.models import Event, MediaAsset, Scene, Workspace


User = get_user_model()


@override_settings(CLOUDFLARE_WEBHOOK_SECRET="test-webhook-secret")
class CloudflareWebhookSecurityTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="photographer@test.com",
            password="password",
        )
        self.workspace = Workspace.objects.create(user=self.user, business_name="PhotoBox")
        self.event = Event.objects.create(
            workspace=self.workspace,
            title="Wedding",
            slug="wedding",
        )
        self.scene = Scene.objects.create(event=self.event, title="Ceremony")

        self.asset = MediaAsset.objects.create(
            scene=self.scene,
            original_filename="image_001.jpg",
            file_size_bytes=50000,
            status="PENDING",
            r2_object_key=f"events/{self.workspace.id}/image_001.jpg",
        )

        self.url = reverse("r2-webhook-ingress")
        self.secret = getattr(
            settings,
            "CLOUDFLARE_WEBHOOK_SECRET",
            "test-webhook-secret",
        ).encode("utf-8")

    def _generate_signature(self, payload_bytes):
        return hmac.new(self.secret, payload_bytes, hashlib.sha256).hexdigest()

    def _post(self, payload, *, signature=None, timestamp=None):
        payload_bytes = json.dumps(payload).encode("utf-8")
        headers = {}
        if signature is not None:
            headers["HTTP_X_CLOUDFLARE_SIGNATURE"] = signature
        if timestamp is not None:
            headers["HTTP_WEBHOOK_TIMESTAMP"] = str(timestamp)
        return self.client.post(
            self.url,
            payload_bytes,
            content_type="application/json",
            **headers,
        )

    def test_valid_cloudflare_signature_success(self):
        payload = {
            "r2_object_key": self.asset.r2_object_key,
            "size": 50000,
            "action": "PutObject",
        }
        response = self._post(
            payload,
            signature=self._generate_signature(json.dumps(payload).encode("utf-8")),
            timestamp=int(time.time()),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "READY")
        self.assertTrue(self.asset.is_processed)

    def test_missing_signature_rejection(self):
        response = self._post(
            {
                "r2_object_key": self.asset.r2_object_key,
                "size": 50000,
                "action": "PutObject",
            },
            signature=None,
            timestamp=int(time.time()),
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_missing_timestamp_rejection(self):
        payload = {
            "r2_object_key": self.asset.r2_object_key,
            "size": 50000,
            "action": "PutObject",
        }
        response = self._post(
            payload,
            signature=self._generate_signature(json.dumps(payload).encode("utf-8")),
            timestamp=None,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_signature_rejection(self):
        response = self._post(
            {
                "r2_object_key": self.asset.r2_object_key,
                "size": 50000,
                "action": "PutObject",
            },
            signature="fake_hacker_signature_123",
            timestamp=int(time.time()),
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_payload_tampering_rejection(self):
        original_payload = {
            "r2_object_key": self.asset.r2_object_key,
            "size": 50000,
            "action": "PutObject",
        }
        signature = self._generate_signature(json.dumps(original_payload).encode("utf-8"))

        tampered_payload = original_payload.copy()
        tampered_payload["size"] = 999999

        response = self._post(
            tampered_payload,
            signature=signature,
            timestamp=int(time.time()),
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_replay_attack_idempotency(self):
        payload = {
            "r2_object_key": self.asset.r2_object_key,
            "size": 50000,
            "action": "PutObject",
        }
        signature = self._generate_signature(json.dumps(payload).encode("utf-8"))
        timestamp = int(time.time())

        self._post(payload, signature=signature, timestamp=timestamp)
        res2 = self._post(payload, signature=signature, timestamp=timestamp)

        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "READY")

    def test_size_mismatch_quarantine(self):
        payload = {
            "r2_object_key": self.asset.r2_object_key,
            "size": 5000000000,
            "action": "PutObject",
        }
        response = self._post(
            payload,
            signature=self._generate_signature(json.dumps(payload).encode("utf-8")),
            timestamp=int(time.time()),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "QUARANTINED")

    def test_ignore_non_put_actions(self):
        payload = {
            "r2_object_key": self.asset.r2_object_key,
            "size": 50000,
            "action": "DeleteObject",
        }
        response = self._post(
            payload,
            signature=self._generate_signature(json.dumps(payload).encode("utf-8")),
            timestamp=int(time.time()),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "PENDING")

    def test_unknown_object_key(self):
        payload = {
            "r2_object_key": "admin/manual_upload.png",
            "size": 50000,
            "action": "PutObject",
        }
        response = self._post(
            payload,
            signature=self._generate_signature(json.dumps(payload).encode("utf-8")),
            timestamp=int(time.time()),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
