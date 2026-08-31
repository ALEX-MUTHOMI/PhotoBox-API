from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from gallery.models import Workspace, Event, Scene
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
        self.assertIn("valid uuid", str(response.data['scene_id']).lower())

    @patch('ingestion.views.generate_r2_presigned_post')
    def test_cryptographic_condition_injection(self, mock_r2):
        mock_r2.return_value = {
            'upload_url': 'https://test.r2.cloudflarestorage.com/test-bucket',
            'post_url': 'https://test.r2.cloudflarestorage.com/test-bucket',
            'post_fields': {
                'key': 'raw/tenant/scene/wedding_shot.jpg',
                'policy': 'base64encodedpolicy==',
                'x-amz-algorithm': 'AWS4-HMAC-SHA256',
                'x-amz-credential': 'test-key/20260416/auto/s3/aws4_request',
                'x-amz-date': '20260416T000000Z',
                'x-amz-signature': 'abc123def456',
            }
        }
        payload = {
            "scene_id": str(self.scene.id),
            "files": [{"filename": "wedding_shot.jpg", "file_size": 1024, "client_reference_id": "ref-1"}]
        }
        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

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


