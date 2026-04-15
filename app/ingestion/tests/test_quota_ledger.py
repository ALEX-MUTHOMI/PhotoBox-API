from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from botocore.exceptions import BotoCoreError
from gallery.models import Workspace, Event, Scene, MediaAsset

User = get_user_model()

class EconomicLedgerSecurityTests(TestCase):
    """
    THE ECONOMIC BOUNDARY: Protecting the SaaS from storage theft,
    testing array sum overflows, and ensuring atomic DB/Quota rollbacks.
    """
    def setUp(self):
        self.user = User.objects.create_user(email="dev@photobox.com", password="pass")

        # The user has a 10MB limit. They have already used 5MB.
        # They have exactly 5MB (5,242,880 bytes) of space left.
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="PhotoBox",
            storage_limit_bytes=10 * 1024 * 1024,
            storage_used_bytes=5 * 1024 * 1024
        )
        self.event = Event.objects.create(workspace=self.workspace, title="Event", slug="event")
        self.scene = Scene.objects.create(event=self.event, title="Scene")

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('bulk-ingest')

    @patch('ingestion.views.get_r2_client')
    def test_exact_byte_boundary_success(self, mock_boto):
        """
        THE LOGIC: If a user has exactly 5MB left, and uploads exactly 5MB, it must pass.
        """
        mock_boto.return_value.generate_presigned_post.return_value = {"url": "test", "fields": {}}

        # Payload is exactly 5MB (5 * 1024 * 1024 = 5242880 bytes)
        payload = {
            "scene_id": str(self.scene.id),
            "files": [{"filename": "exact.jpg", "file_size": 5242880, "client_reference_id": "1"}]
        }

        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Assert the ledger was updated correctly to 10MB total
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 10 * 1024 * 1024)

    @patch('ingestion.views.get_r2_client')
    def test_single_byte_overflow_rejection(self, mock_boto):
        """
        THE THREAT: User tries to squeeze in 1 byte over their paid limit.
        THE TEST: The system must block it with a 402 Payment Required.
        """
        # Payload is 5MB + 1 byte
        payload = {
            "scene_id": str(self.scene.id),
            "files": [{"filename": "overflow.jpg", "file_size": 5242881, "client_reference_id": "1"}]
        }

        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

        # Assert the ledger was NOT modified
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 5 * 1024 * 1024)

    @patch('ingestion.views.get_r2_client')
    def test_array_sum_overflow_rejection(self, mock_boto):
        """
        THE THREAT: Hacker uploads ten 1MB files when they only have 5MB left.
        THE TEST: Proves the ledger calculates the SUM of the array before processing.
        """
        # Create an array of 6 files, each 1MB. Total = 6MB (Exceeds the 5MB limit)
        files_payload = [
            {"filename": f"split_{i}.jpg", "file_size": 1024 * 1024, "client_reference_id": str(i)}
            for i in range(6)
        ]

        payload = {
            "scene_id": str(self.scene.id),
            "files": files_payload
        }

        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

        # Assert the ledger was NOT modified
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 5 * 1024 * 1024)
        # Prove no tickets or records were created in the Vault
        self.assertEqual(MediaAsset.objects.count(), 0)

    @patch('ingestion.views.get_r2_client')
    def test_cloud_outage_auto_refund_and_rollback(self, mock_boto):
        """
        THE THREAT: Cloudflare crashes after the quota is deducted.
        THE TEST: The system must refund the bytes AND roll back the database inserts.
        """
        # Simulate Cloudflare going offline
        mock_boto.return_value.generate_presigned_post.side_effect = BotoCoreError()

        payload = {
            "scene_id": str(self.scene.id),
            "files": [{"filename": "valid.jpg", "file_size": 1000000, "client_reference_id": "1"}]
        }

        response = self.client.post(self.url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

        # 1. The Economic Check: The 1,000,000 bytes MUST be refunded!
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 5 * 1024 * 1024)

        # 2. The Database Check: No ghost records left in PENDING state
        self.assertEqual(MediaAsset.objects.count(), 0)
