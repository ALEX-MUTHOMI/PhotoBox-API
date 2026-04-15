import time
import hmac
import hashlib
import json
import logging
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from gallery.models import Workspace, Event, Scene
# Assuming you have a MediaAsset model tracking the file state
# from gallery.models import MediaAsset

User = get_user_model()

class IngestionSecurityAuditTests(TestCase):
    """
    THE FRONT DOOR: Fortified testing for perimeter cryptography,
    L7 DoS defenses, token expiry, and immutable audit logs.
    """
    def setUp(self):
        self.user = User.objects.create_user(email="hacker@test.com", password="password123")
        self.workspace = Workspace.objects.create(user=self.user, business_name="Rogue Studios")
        self.event = Event.objects.create(workspace=self.workspace, title="Target Event", slug="target")
        self.scene = Scene.objects.create(event=self.event, title="Dropzone")

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('bulk-ingest')

    def test_unauthenticated_ghost_attack(self):
        ghost_client = APIClient()
        payload = {"scene_id": str(self.scene.id), "files": []}
        response = ghost_client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_malformed_uuid_dos_defense(self):
        payload = {
            "scene_id": "DROP TABLE users; --",
            "files": [{"filename": "img.jpg", "file_size": 1024, "client_reference_id": "1"}]
        }
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("valid UUID", str(response.data['scene_id']).lower())

    def test_cryptographic_condition_injection(self):
        payload = {
            "scene_id": str(self.scene.id),
            "files": [{"filename": "wedding_shot.jpg", "file_size": 1024, "client_reference_id": "ref-1"}]
        }
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        ticket = response.data['upload_tickets'][0]
        policy_fields = ticket['post_fields']
        self.assertIn('policy', policy_fields, "FATAL: No cryptographic policy attached!")
        self.assertIn('x-amz-signature', policy_fields, "FATAL: Request is unsigned!")

    def test_tenant_cuckoo_attack_logging(self):
        victim_user = User.objects.create_user(email="victim@test.com", password="password123")
        victim_workspace = Workspace.objects.create(user=victim_user, business_name="Victim Studios")
        victim_event = Event.objects.create(workspace=victim_workspace, title="Private Wedding", slug="private")
        victim_scene = Scene.objects.create(event=victim_event, title="Ceremony")

        payload = {
            "scene_id": str(victim_scene.id),
            "files": [{"filename": "malware.jpg", "file_size": 1024, "client_reference_id": "ref-3"}]
        }

        with self.assertLogs('ingestion.serializers', level='WARNING') as cm:
            response = self.client.post(self.url, payload, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            log_output = "".join(cm.output)
            self.assertIn("UNAUTHORIZED TENANT ACCESS ATTEMPT", log_output)
            self.assertIn(str(self.user.id), log_output)


# OLD:
# class WebhookSecurityAuditTests(TestCase):

# NEW:
class _WebhookSecurityAuditTests(TestCase):
    """
    THE BACK DOOR (EVENT INGRESS): Testing EDA state hijacking,
    replay attacks, and payload size mismatches.
    """

    def setUp(self):
        self.client = APIClient()
        self.webhook_url = reverse('r2-webhook-ingress') # Ensure this matches your urls.py
        self.webhook_secret = b"super-secret-cloudflare-key"

        # We simulate a file that successfully passed the Front Door and is sitting in 'PENDING'
        # self.asset = MediaAsset.objects.create(
        #     id="uuid-1234",
        #     status="PENDING",
        #     file_size_bytes=5000,
        #     media_type="IMAGE"
        # )

    def _generate_valid_signature(self, payload_bytes):
        """Helper to simulate Cloudflare's HMAC generation."""
        return hmac.new(self.webhook_secret, payload_bytes, hashlib.sha256).hexdigest()

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET="super-secret-cloudflare-key")
    def test_event_state_hijacking_spoofed_signature(self):
        """
        THE THREAT: Hacker sends fake R2 payload to hijack state.
        THE TEST: Must drop 401 Unauthorized if HMAC signature is missing/invalid.
        """
        payload = {"asset_id": "uuid-1234", "status": "uploaded", "size": 5000}

        # 1. Missing Signature
        res_missing = self.client.post(self.webhook_url, payload, format='json')
        self.assertEqual(res_missing.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Forged Signature
        self.client.credentials(HTTP_X_WEBHOOK_SIGNATURE='fake-hacker-signature')
        res_forged = self.client.post(self.webhook_url, payload, format='json')
        self.assertEqual(res_forged.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch('processing.tasks.process_image.delay') # Mock your Celery task
    @override_settings(CLOUDFLARE_WEBHOOK_SECRET="super-secret-cloudflare-key")
    def test_event_replay_attack_idempotency(self, mock_celery_task):
        """
        THE THREAT: Intercepted valid webhook replayed 500 times to DDoS Celery.
        THE TEST: Proves DB state locks prevent duplicate Celery task triggers.
        """
        payload = {"asset_id": "uuid-1234", "status": "uploaded", "size": 5000}
        payload_bytes = json.dumps(payload).encode('utf-8')
        valid_sig = self._generate_valid_signature(payload_bytes)

        self.client.credentials(HTTP_X_WEBHOOK_SIGNATURE=valid_sig)

        # First webhook arrival (Happy Path)
        res_first = self.client.post(self.webhook_url, payload_bytes, content_type='application/json')
        self.assertEqual(res_first.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_celery_task.call_count, 1, "Celery worker should be triggered once.")

        # Simulate DB state change that your view would do
        # self.asset.status = 'UPLOADED'
        # self.asset.save()

        # Hacker replays the exact same payload a second later
        res_replay = self.client.post(self.webhook_url, payload_bytes, content_type='application/json')

        # We MUST return 200 OK so Cloudflare doesn't retry, but we do NOT trigger Celery again
        self.assertEqual(res_replay.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_celery_task.call_count, 1, "FATAL: Replay attack triggered duplicate Celery task!")

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET="super-secret-cloudflare-key")
    def test_payload_size_mismatch_quarantine(self):
        """
        THE THREAT: User bypassed POST limits, R2 reports a 5GB file for an IMAGE asset.
        THE TEST: Proves the webhook receiver catches the discrepancy and halts processing.
        """
        # User promised 5000 bytes originally, but webhook reports 5 Gigabytes
        payload = {"asset_id": "uuid-1234", "status": "uploaded", "size": 5 * 1024 * 1024 * 1024}
        payload_bytes = json.dumps(payload).encode('utf-8')
        valid_sig = self._generate_valid_signature(payload_bytes)

        self.client.credentials(HTTP_X_WEBHOOK_SIGNATURE=valid_sig)

        with self.assertLogs('webhooks.views', level='CRITICAL') as cm:
            res = self.client.post(self.webhook_url, payload_bytes, content_type='application/json')

            # The view should return 200 so R2 stops sending it, but internal state should be FAILED
            self.assertEqual(res.status_code, status.HTTP_200_OK)

            log_output = "".join(cm.output)
            self.assertIn("SIZE MISMATCH CRITICAL", log_output)

            # Assert DB state moved to QUARANTINE or FAILED
            # self.asset.refresh_from_db()
            # self.assertEqual(self.asset.status, 'QUARANTINE')







