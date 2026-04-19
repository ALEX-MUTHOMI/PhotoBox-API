from unittest.mock import patch, MagicMock
from botocore.exceptions import ClientError
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

from gallery.models import Workspace, Event, Scene, Photo
from gallery.tasks import process_fast_lane_asset

User = get_user_model()

R2_SETTINGS = dict(
    CLOUDFLARE_R2_ENDPOINT='https://test.r2.cloudflarestorage.com',
    CLOUDFLARE_R2_BUCKET_NAME='test-bucket',
    CLOUDFLARE_ACCESS_KEY_ID='test-key',
    CLOUDFLARE_SECRET_ACCESS_KEY='test-secret',
)

@override_settings(**R2_SETTINGS)
class FastLaneMonitorTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='worker@test.com', password='password123')
        # Give the workspace some used storage to test refunds
        self.workspace = Workspace.objects.create(user=self.user, storage_used_bytes=1000000)
        self.event = Event.objects.create(workspace=self.workspace, title='Worker Event', slug='worker-event')
        self.scene = Scene.objects.create(event=self.event, title='Scene 1')
        
        self.file_size = 512000
        self.photo = Photo.objects.create(
            scene=self.scene, 
            r2_object_key='raw/tenant/test.jpg', 
            file_size_bytes=self.file_size, 
            status='PENDING'
        )
        self.initial_quota = self.workspace.storage_used_bytes

    @patch('gallery.storage.get_r2_client')
    def test_dropped_webhook_self_heals_to_ready(self, mock_get_r2):
        """
        SCENARIO: File is in R2, but webhook dropped.
        EXPECTED: Task forces status to READY (Self-Heal).
        """
        mock_client = MagicMock()
        # head_object succeeds, meaning file is in R2
        mock_client.head_object.return_value = {'ContentLength': self.file_size}
        mock_get_r2.return_value = mock_client

        process_fast_lane_asset(photo_id=str(self.photo.pk))

        self.photo.refresh_from_db()
        self.assertEqual(self.photo.status, 'READY')
        self.assertTrue(self.photo.is_processed)

    @patch('gallery.storage.get_r2_client')
    def test_abandoned_upload_refunds_quota_and_deletes_photo(self, mock_get_r2):
        """
        SCENARIO: File is NOT in R2 (client abandoned upload).
        EXPECTED: Task deletes the PENDING photo and refunds quota safely.
        """
        mock_client = MagicMock()
        # head_object throws 404, meaning file never arrived
        mock_client.head_object.side_effect = ClientError(
            {'Error': {'Code': '404', 'Message': 'Not Found'}}, 'HeadObject'
        )
        mock_get_r2.return_value = mock_client

        process_fast_lane_asset(photo_id=str(self.photo.pk))

        # Photo should be deleted
        with self.assertRaises(Photo.DoesNotExist):
            self.photo.refresh_from_db()

        # Quota should be refunded
        self.workspace.refresh_from_db()
        self.assertEqual(
            self.workspace.storage_used_bytes, 
            self.initial_quota - self.file_size
        )

    @patch('gallery.storage.get_r2_client')
    def test_task_idempotency_on_replay(self, mock_get_r2):
        """
        SCENARIO: Task runs twice (e.g. Celery network glitch).
        EXPECTED: Second run does nothing, data remains stable.
        """
        mock_client = MagicMock()
        mock_client.head_object.return_value = {'ContentLength': self.file_size}
        mock_get_r2.return_value = mock_client

        # First run (Heals)
        process_fast_lane_asset(photo_id=str(self.photo.pk))
        self.photo.refresh_from_db()
        self.assertEqual(self.photo.status, 'READY')

        # Second run (Idempotent skip)
        result = process_fast_lane_asset(photo_id=str(self.photo.pk))
        self.assertEqual(result.get('status'), 'already_processed')

    @patch('gallery.storage.get_r2_client')
    def test_upload_task_with_deleted_photo_does_not_crash(self, mock_get_r2):
        """
        SCENARIO: User deletes the Event/Photo before the 15-minute monitor wakes up.
        EXPECTED: Task exits cleanly without crashing or double-refunding.
        """
        mock_get_r2.return_value = MagicMock()
        deleted_id = str(self.photo.pk)
        self.photo.delete()

        self.workspace.refresh_from_db()
        quota_before = self.workspace.storage_used_bytes

        try:
            result = process_fast_lane_asset(photo_id=deleted_id)
            self.assertEqual(result.get('status'), 'skipped')
        except Exception as e:
            self.fail(f'Task raised exception for deleted photo: {e}')

        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, quota_before)








# from unittest.mock import patch, MagicMock
# from botocore.exceptions import ClientError
# from django.test import TestCase, override_settings
# from django.contrib.auth import get_user_model
# from django.db.models import F

# from gallery.models import Workspace, Event, Scene, Photo
# from gallery.tasks import process_fast_lane_asset

# User = get_user_model()

# R2_SETTINGS = dict(
#     CLOUDFLARE_R2_ENDPOINT='https://test.r2.cloudflarestorage.com',
#     CLOUDFLARE_R2_BUCKET_NAME='test-bucket',
#     CLOUDFLARE_ACCESS_KEY_ID='test-key',
#     CLOUDFLARE_SECRET_ACCESS_KEY='test-secret',
# )

# @override_settings(**R2_SETTINGS)
# class FastLaneMonitorTaskTests(TestCase):
#     def setUp(self):
#         self.user = User.objects.create_user(email='worker@test.com', password='password123')
#         # Give the workspace some used storage to test refunds
#         self.workspace = Workspace.objects.create(user=self.user, storage_used_bytes=1000000)
#         self.event = Event.objects.create(workspace=self.workspace, title='Worker Event', slug='worker-event')
#         self.scene = Scene.objects.create(event=self.event, title='Scene 1')
        
#         self.file_size = 512000
#         self.photo = Photo.objects.create(
#             scene=self.scene, 
#             r2_object_key='raw/tenant/test.jpg', 
#             file_size_bytes=self.file_size, 
#             status='PENDING'
#         )
#         self.initial_quota = self.workspace.storage_used_bytes

#     @patch('gallery.storage.get_r2_client')
#     def test_dropped_webhook_self_heals_to_ready(self, mock_get_r2):
#         """
#         SCENARIO: File is in R2, but webhook dropped.
#         EXPECTED: Task forces status to READY (Self-Heal).
#         """
#         mock_client = MagicMock()
#         # head_object succeeds, meaning file is in R2
#         mock_client.head_object.return_value = {'ContentLength': self.file_size}
#         mock_get_r2.return_value = mock_client

#         process_fast_lane_asset(photo_id=str(self.photo.pk))

#         self.photo.refresh_from_db()
#         self.assertEqual(self.photo.status, 'READY')
#         self.assertTrue(self.photo.is_processed)

#     @patch('gallery.storage.get_r2_client')
#     def test_abandoned_upload_refunds_quota_and_deletes_photo(self, mock_get_r2):
#         """
#         SCENARIO: File is NOT in R2 (client abandoned upload).
#         EXPECTED: Task deletes the PENDING photo and refunds quota safely.
#         """
#         mock_client = MagicMock()
#         # head_object throws 404, meaning file never arrived
#         mock_client.head_object.side_effect = ClientError(
#             {'Error': {'Code': '404', 'Message': 'Not Found'}}, 'HeadObject'
#         )
#         mock_get_r2.return_value = mock_client

#         process_fast_lane_asset(photo_id=str(self.photo.pk))

#         # Photo should be deleted
#         with self.assertRaises(Photo.DoesNotExist):
#             self.photo.refresh_from_db()

#         # Quota should be refunded
#         self.workspace.refresh_from_db()
#         self.assertEqual(
#             self.workspace.storage_used_bytes, 
#             self.initial_quota - self.file_size
#         )

#     @patch('gallery.storage.get_r2_client')
#     def test_task_idempotency_on_replay(self, mock_get_r2):
#         """
#         SCENARIO: Task runs twice (e.g. Celery network glitch).
#         EXPECTED: Second run does nothing, data remains stable.
#         """
#         mock_client = MagicMock()
#         mock_client.head_object.return_value = {'ContentLength': self.file_size}
#         mock_get_r2.return_value = mock_client

#         # First run (Heals)
#         process_fast_lane_asset(photo_id=str(self.photo.pk))
#         self.photo.refresh_from_db()
#         self.assertEqual(self.photo.status, 'READY')

#         # Second run (Idempotent skip)
#         result = process_fast_lane_asset(photo_id=str(self.photo.pk))
#         self.assertEqual(result.get('status'), 'already_processed')

#     @patch('gallery.storage.get_r2_client')
#     def test_upload_task_with_deleted_photo_does_not_crash(self, mock_get_r2):
#         """
#         SCENARIO: User deletes the Event/Photo before the 15-minute monitor wakes up.
#         EXPECTED: Task exits cleanly without crashing or double-refunding.
#         """
#         mock_get_r2.return_value = MagicMock()
#         deleted_id = str(self.photo.pk)
#         self.photo.delete()

#         self.workspace.refresh_from_db()
#         quota_before = self.workspace.storage_used_bytes

#         try:
#             result = process_fast_lane_asset(photo_id=deleted_id)
#             self.assertEqual(result.get('status'), 'skipped')
#         except Exception as e:
#             self.fail(f'Task raised exception for deleted photo: {e}')

#         self.workspace.refresh_from_db()
#         self.assertEqual(self.workspace.storage_used_bytes, quota_before)
