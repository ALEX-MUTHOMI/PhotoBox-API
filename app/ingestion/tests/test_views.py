from unittest.mock import patch
from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from django.db import IntegrityError
from gallery.models import Workspace, Event, Scene, MediaAsset

User = get_user_model()

class IngestionDatabaseIntegrityTests(TestCase):
    """
    THE ATOMIC VAULT: Ensuring database consistency, O(1) query scalability,
    and strict JSON contracts under heavy load and failure states.
    """

    def setUp(self):
        self.user = User.objects.create_user(email="dev@photobox.com", password="pass")
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="PhotoBox",
            storage_limit_bytes=10*1024*1024*1024
        )
        self.event = Event.objects.create(workspace=self.workspace, title="Event", slug="event")
        self.scene = Scene.objects.create(event=self.event, title="Scene")

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('bulk-ingest')

    @patch('ingestion.views.get_r2_client')
    def test_happy_path_bulk_insert_and_contract(self, mock_boto):
        """
        THE LOGIC: Proves valid payloads return the exact JSON contract,
        and enforces strict Tenant Path isolation in the Cloudflare R2 bucket.
        """
        mock_boto.return_value.generate_presigned_post.return_value = {
            "url": "https://r2.cloudflare.com/bucket",
            "fields": {"policy": "base64", "x-amz-signature": "hash"}
        }

        files_payload = [{"filename": "photo.jpg", "file_size": 1000, "client_reference_id": "ref_1"}]
        payload = {"scene_id": str(self.scene.id), "files": files_payload}

        response = self.client.post(self.url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(MediaAsset.objects.count(), 1)

        # 1. Assert the Frontend Contract
        data = response.data
        self.assertIn("upload_tickets", data)
        self.assertEqual(data['upload_tickets'][0]['post_url'], "https://r2.cloudflare.com/bucket")

        # 2. THE SECURITY FIX: Assert Tenant Path Isolation
        boto_call_kwargs = mock_boto.return_value.generate_presigned_post.call_args[1]
        r2_object_key = boto_call_kwargs['Key']
        self.assertIn(f"tenant_{self.user.id}", r2_object_key, "FATAL: R2 Path is not scoped to the tenant!")
        self.assertIn(f"scene_{self.scene.id}", r2_object_key, "FATAL: R2 Path is not scoped to the scene!")

    @patch('ingestion.views.get_r2_client')
    def test_o_1_query_count_performance_lock(self, mock_boto):
        """
        THE ENGINEER'S DEFENSE: Enforces O(1) Scalability.
        50 files MUST take the exact same number of database queries as 5 files.
        """
        mock_boto.return_value.generate_presigned_post.return_value = {"url": "test", "fields": {}}

        payload_5 = {"scene_id": str(self.scene.id), "files": [{"filename": f"{i}.jpg", "file_size": 100, "client_reference_id": str(i)} for i in range(5)]}
        payload_50 = {"scene_id": str(self.scene.id), "files": [{"filename": f"{i}.jpg", "file_size": 100, "client_reference_id": str(i)} for i in range(50)]}

        # Step 1: Capture the baseline query count for a small payload
        with CaptureQueriesContext(connection) as baseline_ctx:
            self.client.post(self.url, payload_5, format='json')
        baseline_queries = len(baseline_ctx.captured_queries)

        # Step 2: Assert that a massive payload uses the EXACT same number of queries
        with self.assertNumQueries(baseline_queries):
            self.client.post(self.url, payload_50, format='json')

        self.assertEqual(MediaAsset.objects.count(), 55) # 5 from baseline + 50 from massive payload

    @patch('ingestion.views.MediaAsset.objects.bulk_create')
    @patch('ingestion.views.get_r2_client')
    def test_atomic_rollback_on_db_failure(self, mock_boto, mock_bulk_create):
        """
        THE THREAT: The DB crashes right as we are saving the asset records.
        THE TEST: Proves the view catches the crash, returns a clean JSON 500, and rolls back the DB.
        """
        mock_boto.return_value.generate_presigned_post.return_value = {"url": "test", "fields": {}}
        mock_bulk_create.side_effect = IntegrityError("Database connection lost.")

        payload = {
            "scene_id": str(self.scene.id),
            "files": [{"filename": "1.jpg", "file_size": 1000, "client_reference_id": "1"}]
        }

        # We removed the try/except block. If this view throws a raw 500 Python stack trace,
        # this test WILL fail, and that is exactly what we want.
        response = self.client.post(self.url, payload, format='json')

        # The view MUST catch the IntegrityError and return a clean JSON 500
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

        # The ultimate proof: The transaction rolled back. Zero records created.
        self.assertEqual(MediaAsset.objects.count(), 0, "FATAL: Partial database save detected!")
