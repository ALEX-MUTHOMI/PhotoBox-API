import json
import hmac
import hashlib
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.conf import settings
from django.contrib.auth import get_user_model
from gallery.models import Workspace, Event, Scene, MediaAsset

User = get_user_model()

class CloudflareWebhookSecurityTests(APITestCase):
    def setUp(self):
        # 1. Database Setup (The Anchor)
        self.user = User.objects.create_user(email="photographer@test.com", password="password")
        self.workspace = Workspace.objects.create(user=self.user, business_name="PhotoBox")
        self.event = Event.objects.create(workspace=self.workspace, title="Wedding", slug="wedding")
        self.scene = Scene.objects.create(event=self.event, title="Ceremony")

        # The pending asset authorized for exactly 50,000 bytes
        self.asset = MediaAsset.objects.create(
            scene=self.scene,
            original_filename="image_001.jpg",
            file_size_bytes=50000,
            status="PENDING",
            r2_object_key=f"events/{self.workspace.id}/image_001.jpg"
        )

        self.url = reverse('r2-webhook-ingress')
        self.secret = getattr(settings, 'CLOUDFLARE_SECRET_ACCESS_KEY', 'test-secret-key').encode('utf-8')

        self.valid_payload = {
            "r2_object_key": self.asset.r2_object_key,
            "size": 50000,
            "action": "PutObject"
        }
        self.payload_bytes = json.dumps(self.valid_payload).encode('utf-8')

    def _generate_signature(self, payload_bytes):
        """Helper: Generates a mathematically perfect HMAC-SHA256 signature."""
        return hmac.new(self.secret, payload_bytes, hashlib.sha256).hexdigest()

    # --- 1. CORE FUNCTIONALITY & CRYPTOGRAPHY ---

    def test_valid_cloudflare_signature_success(self):
        """THE VIP: Valid signature marks asset as UPLOADED."""
        signature = self._generate_signature(self.payload_bytes)
        self.client.credentials(HTTP_X_CLOUDFLARE_SIGNATURE=signature)

        response = self.client.post(self.url, self.payload_bytes, content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "UPLOADED")

    def test_missing_signature_rejection(self):
        """THE PROBE: Missing signature drops the request entirely."""
        response = self.client.post(self.url, self.payload_bytes, content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_signature_rejection(self):
        """THE SCRIPT KIDDIE: Wrong signature drops the request."""
        self.client.credentials(HTTP_X_CLOUDFLARE_SIGNATURE="fake_hacker_signature_123")
        response = self.client.post(self.url, self.payload_bytes, content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_payload_tampering_rejection(self):
        """THE MITM: Tampered bytes fail HMAC validation."""
        valid_signature = self._generate_signature(self.payload_bytes)

        tampered_payload = self.valid_payload.copy()
        tampered_payload['size'] = 999999
        tampered_bytes = json.dumps(tampered_payload).encode('utf-8')

        self.client.credentials(HTTP_X_CLOUDFLARE_SIGNATURE=valid_signature)
        response = self.client.post(self.url, tampered_bytes, content_type='application/json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_replay_attack_idempotency(self):
        """THE DDoS: 500 identical webhooks shouldn't crash the system."""
        signature = self._generate_signature(self.payload_bytes)
        self.client.credentials(HTTP_X_CLOUDFLARE_SIGNATURE=signature)

        # Request 1
        self.client.post(self.url, self.payload_bytes, content_type='application/json')
        # Request 2 (The Replay)
        res2 = self.client.post(self.url, self.payload_bytes, content_type='application/json')

        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "UPLOADED")

    # --- 2. BUSINESS LOGIC & STRUCTURAL INTEGRITY ---

    def test_size_mismatch_quarantine(self):
        """THE QUOTA THIEF: Cloudflare size differs from Ingestion ticket size."""
        # User uploads a larger file to R2 than they requested a ticket for.
        thief_payload = self.valid_payload.copy()
        thief_payload['size'] = 5000000000 # 5GB actual upload
        thief_bytes = json.dumps(thief_payload).encode('utf-8')

        # The signature is valid because Cloudflare genuinely generated it
        signature = self._generate_signature(thief_bytes)
        self.client.credentials(HTTP_X_CLOUDFLARE_SIGNATURE=signature)

        response = self.client.post(self.url, thief_bytes, content_type='application/json')

        self.assertEqual(response.status_code, status.HTTP_200_OK) # Tell Cloudflare OK
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "QUARANTINED") # But quarantine the file internally!

    def test_ignore_non_put_actions(self):
        """THE DELETION TRAP: Ignore Cloudflare Delete events."""
        delete_payload = self.valid_payload.copy()
        delete_payload['action'] = "DeleteObject"
        delete_bytes = json.dumps(delete_payload).encode('utf-8')

        signature = self._generate_signature(delete_bytes)
        self.client.credentials(HTTP_X_CLOUDFLARE_SIGNATURE=signature)

        response = self.client.post(self.url, delete_bytes, content_type='application/json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "PENDING") # State must not change to UPLOADED

    def test_unknown_object_key(self):
        """THE GHOST FILE: Unknown R2 keys do not cause 500 crashes."""
        ghost_payload = self.valid_payload.copy()
        ghost_payload['r2_object_key'] = "admin/manual_upload.png"
        ghost_bytes = json.dumps(ghost_payload).encode('utf-8')

        signature = self._generate_signature(ghost_bytes)
        self.client.credentials(HTTP_X_CLOUDFLARE_SIGNATURE=signature)

        response = self.client.post(self.url, ghost_bytes, content_type='application/json')

        # Must return 200 so Cloudflare doesn't think the server is down
        self.assertEqual(response.status_code, status.HTTP_200_OK)
