import json
import uuid
import threading
from unittest.mock import patch, MagicMock
from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from django.utils import timezone
from datetime import timedelta
from botocore.exceptions import ClientError
from django.db import transaction

from gallery.models import Workspace, Event, Scene, MediaAsset
from ingestion.tasks import reap_abandoned_uploads

User = get_user_model()

class IngestionPerformanceAndScaleTests(TransactionTestCase):
    """
    THE EXTREME SCALE: Validates architecture against high bulk arrays, 
    4K video size thresholds, and L7 Database Lock Starvation.
    Uses TransactionTestCase because we test DB locks across threads.
    """
    
    def setUp(self):
        self.user = User.objects.create_user(email="pro@test.com", password="password123")
        self.workspace = Workspace.objects.create(
            user=self.user, 
            business_name="Pro Studios",
            storage_limit_bytes=5 * 1024 * 1024 * 1024 * 10 # 50 GB
        )
        self.event = Event.objects.create(workspace=self.workspace, title="Huge Event", slug="huge")
        self.scene = Scene.objects.create(event=self.event, title="Day 1")

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('bulk-ingest')

    @patch('ingestion.views.get_r2_client')
    def test_4k_video_bulk_limits(self, mock_get_r2):
        """
        THE THREAT: Integer overflows or mass array OOM vectors.
        THE TEST: Proves the system gracefully handles generating presigned URLs for 
        large arrays of maximal 4K video payloads (4.9GB each) without OOM.
        """
        # Mock Boto3 purely computational presign
        mock_client = MagicMock()
        mock_client.generate_presigned_post.return_value = {
            'url': 'https://r2/', 'fields': {'x-amz-signature': 'abc'}
        }
        mock_get_r2.return_value = mock_client
        
        # 100 x 4.9 GB = 490 GB payload request! (Would trigger payment block initially, 
        # so we'll test with exactly the limit, or we can just bypass limit to test logic)
        self.workspace.storage_limit_bytes = 1000 * 1024**3 # 1 TB
        self.workspace.save()

        files_payload = []
        for i in range(150): # 150 huge 4K files
            files_payload.append({
                "filename": f"4k_wedding_cam{i}.mp4",
                "file_size": 4 * 1024 * 1024 * 1024, # 4GB each
                "client_reference_id": f"ref_4k_{i}"
            })

        payload = {"scene_id": str(self.scene.id), "files": files_payload}
        
        res = self.client.post(self.url, payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res.data['upload_tickets']), 150)
        
        # Check DB states
        self.assertEqual(MediaAsset.objects.filter(scene=self.scene).count(), 150)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 150 * 4 * 1024 * 1024 * 1024)

    def test_db_lock_contention_survival(self):
        """
        THE THREAT: Slow Boto3 / API calls freezing the DB connection pool.
        THE TEST: Proves `nowait=True` returns 409 instead of 500 block.
        """
        # In TransactionTestCase, we can lock the row in a transaction to simulate 
        # another process holding the lock.
        
        payload = {
            "scene_id": str(self.scene.id),
            "files": [{"filename": "img.jpg", "file_size": 1024, "client_reference_id": "ref-1"}]
        }
        
        def hold_lock():
            with transaction.atomic():
                Workspace.objects.select_for_update().get(id=self.workspace.id)
                import time
                time.sleep(2) # Hold lock for 2 seconds

        # Start background thread to hold lock
        t = threading.Thread(target=hold_lock)
        t.start()
        
        import time
        time.sleep(0.5) # Let thread acquire lock
        
        # Now try to upload while lock is held!
        # Because we isolated the HTTP request out of the thread, it will hit the DB and see the lock.
        res = self.client.post(self.url, payload, format='json')
        
        # We MUST get 409 Conflict, NOT a hang or 500.
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("Workspace is currently processing another bulk upload", str(res.data))
        
        t.join() # cleanup


class ReaperSecurityTests(TestCase):
    """
    THE FIX: Defending against Phantom Upload Starvation.
    """
    def setUp(self):
        self.user = User.objects.create_user(email="hacker@test.com", password="password123")
        self.workspace = Workspace.objects.create(
            user=self.user, 
            storage_used_bytes=5 * 1024 * 1024 * 1024 # 5 GB used
        )
        self.event = Event.objects.create(workspace=self.workspace, title="Event", slug="ev")
        self.scene = Scene.objects.create(event=self.event, title="Day 1")
        
        # A PENDING asset that is physically in R2 (HACKER)
        self.phantom_asset = MediaAsset.objects.create(
            scene=self.scene, file_size_bytes=4 * 1024 * 1024 * 1024, # 4GB
            status='PENDING', r2_object_key='hacker/file.mp4'
        )
        self.phantom_asset.uploaded_at = timezone.now() - timedelta(hours=48)
        self.phantom_asset.save()

        # A PENDING asset that genuinely failed to upload (NORMAL USER)
        self.legit_abandon_asset = MediaAsset.objects.create(
            scene=self.scene, file_size_bytes=1024, 
            status='PENDING', r2_object_key='legit/file.jpg'
        )
        self.legit_abandon_asset.uploaded_at = timezone.now() - timedelta(hours=48)
        self.legit_abandon_asset.save()

    @patch('ingestion.tasks.get_r2_client')
    def test_phantom_upload_reaper_defense(self, mock_get_r2):
        """
        THE THREAT: Hacker uploaded 4GB file to R2 but blocked webhook. Reaper might refund quota!
        THE TEST: Proves `head_object` blocks the refund and Quarantines the phantom asset.
        """
        mock_client = MagicMock()
        
        # Mock R2 responses: The hacker's file exists, but legit user's file is 404.
        def mock_head_object(Bucket, Key):
            if Key == 'hacker/file.mp4':
                return {'ContentLength': 4000000} # Succesful physical check
            else:
                error_response = {'Error': {'Code': '404', 'Message': 'Not Found'}}
                raise ClientError(error_response, 'HeadObject')
                
        mock_client.head_object.side_effect = mock_head_object
        mock_get_r2.return_value = mock_client
        
        # Run Reaper
        output = reap_abandoned_uploads()
        
        # Assertions
        self.assertIn("Reaped: 1. Phantoms Caught: 1.", output)
        
        # Hacker's file is QUARANTINED, Quota is NOT refunded (5GB - 1024 bytes)
        self.phantom_asset.refresh_from_db()
        self.assertEqual(self.phantom_asset.status, 'QUARANTINED')
        
        # Normal user's file is FAILED
        self.legit_abandon_asset.refresh_from_db()
        self.assertEqual(self.legit_abandon_asset.status, 'FAILED')
        
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 5 * 1024 * 1024 * 1024 - 1024)
