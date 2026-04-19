"""
Enterprise-Grade Tests for the Fast Lane Photo API.

ARCHITECTURE CONTRACT BEING TESTED:
  - The web thread returns 202 Accepted immediately (< 100ms).
  - is_processed is False at upload time — the Celery task flips it later.
  - All I/O (R2 vault) happens in a background Celery worker, NEVER on the web thread.
  - The Celery task is dispatched once with the correct photo ID.
  - All perimeter security checks execute before any DB write.
"""
import io
from unittest.mock import patch, MagicMock
from PIL import Image as PILImage

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.models import Event, Scene, Photo

# The router registers this viewset under 'fast-lane/photos', which Django resolves
# to the basename 'photo' because the queryset model is Photo.
FAST_LANE_URL = reverse('gallery:fastlane-photo-list')

# The exact Celery task path that the view fires. We patch it in every test that
# hits the upload endpoint so no real Celery broker or R2 connection is needed.
# RENAMED from upload_fast_lane_to_cloudinary → process_fast_lane_asset (Phase 1: Unified Vault)
CELERY_TASK_PATH = 'gallery.tasks.process_fast_lane_asset'


def create_user(**params):
    return get_user_model().objects.create_user(**params)


def generate_test_image(width=100, height=100, fmt='JPEG', filename='test_image.jpg'):
    """
    Generates a minimal valid binary image in RAM.
    Produces approximately 2KB of data — well within the 5MB Fast Lane gate.
    """
    file_obj = io.BytesIO()
    image = PILImage.new('RGB', size=(width, height), color=(255, 0, 0))
    image.save(file_obj, fmt)
    file_obj.seek(0)
    return SimpleUploadedFile(filename, file_obj.read(), content_type='image/jpeg')


class FastLaneApiTests(TestCase):
    """
    Production-ready test suite for the Fast Lane API.

    Every test that triggers the upload endpoint patches the Celery task.
    This isolates the web layer physics (auth, security, DB writes, HTTP status)
    from the async worker layer (Cloudinary I/O), which has its own test suite.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = create_user(email='pro@example.com', password='testpass123')

        # The Workspace stores the byte-level quota ledger.
        # Default storage_limit_bytes = 1GB (from the model default).
        self.workspace = Workspace.objects.create(user=self.user, business_name='Pro Studio')
        self.event = Event.objects.create(workspace=self.workspace, title='Summer Wedding', slug='summer')
        self.scene = Scene.objects.create(event=self.event, title='Highlight')

        self.client.force_authenticate(self.user)

    # ==========================================
    # 1. THE HAPPY PATH — EDA-COMPLIANT BEHAVIOR
    # ==========================================

    @patch(CELERY_TASK_PATH)
    def test_upload_image_fast_lane_returns_202_accepted(self, mock_task):
        """
        ARCHITECTURE: The web thread must return 202, not 201.
        202 = "I received your file and queued it. Processing is NOT done yet."
        201 = "I created the resource and it is ready." — semantically wrong for async pipelines.
        """
        payload = {
            'scene': self.scene.id,
            'image_file': generate_test_image()
        }
        res = self.client.post(FAST_LANE_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)

    @patch(CELERY_TASK_PATH)
    def test_upload_creates_photo_in_pending_state(self, mock_task):
        """
        ARCHITECTURE: After the 202 response, the Photo row exists in the DB
        with is_processed=False and status='PENDING'. The Celery worker has not
        yet run. The client must NOT render this photo until it polls and sees
        is_processed=True.
        """
        payload = {
            'scene': self.scene.id,
            'image_file': generate_test_image()
        }
        res = self.client.post(FAST_LANE_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        photo_id = res.data['photo_id']

        photo = Photo.objects.get(id=photo_id)

        # The honest state: processing has not happened yet
        self.assertFalse(photo.is_processed, "FATAL: is_processed=True at upload time — EDA violation!")
        self.assertEqual(photo.status, 'PENDING')

        # But the byte ledger must already be committed atomically
        self.assertGreater(photo.file_size_bytes, 0)

    @patch(CELERY_TASK_PATH)
    def test_upload_dispatches_celery_task_with_correct_photo_id(self, mock_task):
        """
        ARCHITECTURE: The web thread's ONLY job at upload time is to fire the Celery
        task. Verify it fires exactly once with the correct photo ID.
        If this fails, the Cloudinary upload NEVER happens — photos stay stuck in PENDING forever.
        """
        payload = {
            'scene': self.scene.id,
            'image_file': generate_test_image()
        }
        res = self.client.post(FAST_LANE_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)

        # Celery task must have been fired exactly once
        mock_task.delay.assert_called_once()

        # The argument must be the UUID string of the created photo
        called_photo_id = mock_task.delay.call_args[0][0]
        self.assertEqual(called_photo_id, str(res.data['photo_id']))

    @patch(CELERY_TASK_PATH)
    def test_upload_commits_bytes_atomically_to_quota_ledger(self, mock_task):
        """
        BILLING: After upload, the Workspace quota ledger must be updated atomically
        via F() expression. Verify the bytes are committed immediately on the 202 response,
        not deferred to the Celery task.
        """
        payload = {
            'scene': self.scene.id,
            'image_file': generate_test_image()
        }
        res = self.client.post(FAST_LANE_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)

        photo = Photo.objects.get(id=res.data['photo_id'])
        self.workspace.refresh_from_db()

        # The exact file size must be reflected in the ledger
        self.assertEqual(self.workspace.storage_used_bytes, photo.file_size_bytes)

    @patch(CELERY_TASK_PATH)
    def test_delete_image_refunds_quota_atomically(self, mock_task):
        """
        SECURITY (Billing Integrity): Deleting a photo must atomically refund the
        exact byte count back to the Workspace ledger. Prevents 'ghost storage' —
        where a photographer's quota leaks after deletion.
        """
        payload = {'scene': self.scene.id, 'image_file': generate_test_image()}
        res = self.client.post(FAST_LANE_URL, payload, format='multipart')
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)

        photo_id = res.data['photo_id']
        self.workspace.refresh_from_db()
        used_bytes_before = self.workspace.storage_used_bytes
        self.assertGreater(used_bytes_before, 0)

        del_url = reverse('gallery:fastlane-photo-detail', args=[photo_id])
        del_res = self.client.delete(del_url)
        self.assertEqual(del_res.status_code, status.HTTP_204_NO_CONTENT)

        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 0, "FATAL: Quota not refunded after deletion!")

    @patch(CELERY_TASK_PATH)
    def test_response_body_contains_queued_status_and_photo_id(self, mock_task):
        """
        CONTRACT: The 202 response body must contain 'status': 'queued' and
        a 'photo_id' that the frontend can use to poll for completion.
        """
        payload = {
            'scene': self.scene.id,
            'image_file': generate_test_image()
        }
        res = self.client.post(FAST_LANE_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data['status'], 'queued')
        self.assertIn('photo_id', res.data)

    # ==========================================
    # 2. AUTHENTICATION PERIMETER
    # ==========================================

    def test_unauthenticated_upload_rejected(self):
        """SECURITY: Unauthenticated requests must be rejected before touching any business logic."""
        unauthenticated_client = APIClient()
        payload = {'scene': self.scene.id, 'image_file': generate_test_image()}
        res = unauthenticated_client.post(FAST_LANE_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_cannot_list_photos_from_other_workspaces(self):
        """SECURITY (Tenant Isolation): GET /fast-lane/photos/ must be scoped to the authenticated user."""
        rival = create_user(email='rival@example.com', password='password123')
        rival_workspace = Workspace.objects.create(user=rival, business_name='Rival')
        rival_event = Event.objects.create(workspace=rival_workspace, title='Rival', slug='rival')
        rival_scene = Scene.objects.create(event=rival_event, title='Ceremony')

        # Create a photo in the rival's workspace directly (bypassing the API)
        Photo.objects.create(
            scene=rival_scene,
            original_filename='secret.jpg',
            file_size_bytes=1024,
        )

        res = self.client.get(FAST_LANE_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # Our user has 0 photos — must not see the rival's photo
        self.assertEqual(res.data['count'], 0)
        self.assertEqual(len(res.data['results']), 0)

    # ==========================================
    # 3. RED TEAM SCRIPTS (THE PERIMETER DEFENSES)
    # ==========================================

    @patch(CELERY_TASK_PATH)
    def test_upload_to_unowned_scene_blocked(self, mock_task):
        """
        SECURITY (Cross-Tenant Injection): A malicious photographer must not
        be able to inject photos into a rival's Event by submitting a foreign scene ID.
        """
        rival = create_user(email='hacker@example.com', password='password123')
        rival_workspace = Workspace.objects.create(user=rival, business_name='Rival')
        rival_event = Event.objects.create(workspace=rival_workspace, title='Rival', slug='rival')
        rival_scene = Scene.objects.create(event=rival_event, title='Ceremony')

        payload = {'scene': rival_scene.id, 'image_file': generate_test_image()}
        res = self.client.post(FAST_LANE_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        # Celery task must NOT have been called — no DB write should have happened
        mock_task.delay.assert_not_called()

    def test_malware_magic_byte_spoofing_shield(self):
        """
        SECURITY (Magic Byte Inspection): A disguised executable script renamed to .jpg
        must be rejected by the Pillow magic byte inspector BEFORE any DB write occurs.
        """
        malicious_file = SimpleUploadedFile(
            'shell.jpg',
            b'import os; os.system("rm -rf /")',
            content_type='image/jpeg'
        )

        payload = {'scene': self.scene.id, 'image_file': malicious_file}
        res = self.client.post(FAST_LANE_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Malware Shield', str(res.data))
        # Verify no Photo was created and no quota was consumed
        self.assertEqual(Photo.objects.count(), 0)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 0)

    def test_payload_too_large_fast_lane_shield(self):
        """
        SECURITY (OOM / Worker Starvation Prevention): Files over 5MB must be dropped
        at the Python layer. The primary gate is Nginx (TCP-level), this is Defense Layer 2.

        NOTE: This test simulates the Python-layer check only. The Nginx TCP-level drop
        (client_max_body_size 5m) cannot be tested in Django TestCase — test that in
        a staging environment with a real Nginx process.
        """
        six_mb_file = SimpleUploadedFile(
            'massive.jpg',
            b'0' * (6 * 1024 * 1024),
            content_type='image/jpeg'
        )

        payload = {'scene': self.scene.id, 'image_file': six_mb_file}
        res = self.client.post(FAST_LANE_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Payload exceeds 5MB', str(res.data))
        self.assertEqual(Photo.objects.count(), 0)

    def test_atomic_quota_race_condition_shield(self):
        """
        SECURITY (Billing): Uploads must be rejected when the Workspace quota is full.
        The storage_used_bytes is manually set to the ceiling to simulate an exhausted account.
        """
        one_gb = 1 * 1024 * 1024 * 1024
        # Fill quota to the ceiling (Workspace default limit is 1GB)
        Workspace.objects.filter(id=self.workspace.id).update(storage_used_bytes=one_gb)

        payload = {'scene': self.scene.id, 'image_file': generate_test_image()}
        res = self.client.post(FAST_LANE_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        # Match the EXACT string the view raises
        self.assertIn('Storage quota exceeded', str(res.data))
        self.assertEqual(Photo.objects.count(), 0)

    def test_decompression_bomb_shield(self):
        """
        SECURITY (Decompression Bomb / ZIP Bomb): An adversarial image that claims to be
        valid JPEG but expands to 100M+ pixels must be rejected by the pixel ceiling check.

        TWO-PASS PATTERN: The view calls PILImage.open twice:
          - Call 1: probe.verify() — structural check (destroys the object)
          - Call 2: with PILImage.open() as img — metadata check (width, height, format)
        We mock both calls via side_effect.
        """
        with patch('gallery.views.PILImage.open') as mock_open:
            # Call 1: the verify() probe — just needs to not raise
            mock_probe = MagicMock()
            mock_probe.verify = MagicMock()

            # Call 2: the metadata reader — reports a bomb
            mock_img = MagicMock()
            mock_img.__enter__ = lambda s: mock_img
            mock_img.__exit__ = MagicMock(return_value=False)
            mock_img.format = 'JPEG'
            # 20,000 x 10,001 = 200,020,000 pixels — well over the 100MP limit
            mock_img.width = 20000
            mock_img.height = 10001

            mock_open.side_effect = [mock_probe, mock_img]

            bomb_file = SimpleUploadedFile('bomb.jpg', b'\xff\xd8\xff' + b'0' * 100, content_type='image/jpeg')
            payload = {'scene': self.scene.id, 'image_file': bomb_file}
            res = self.client.post(FAST_LANE_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Decompression Bomb', str(res.data))


    def test_disallowed_file_format_rejected(self):
        """
        SECURITY (Format Allowlist): Non-image binary formats (GIF, TIFF, BMP, SVG, etc.)
        that pass the magic byte check must still be rejected if not in [JPEG, PNG, WEBP].
        """
        with patch('gallery.views.PILImage.open') as mock_open:
            # Call 1: verify() probe — passes cleanly
            mock_probe = MagicMock()
            mock_probe.verify = MagicMock()

            # Call 2: metadata reader — reports GIF format (not in allowlist)
            mock_img = MagicMock()
            mock_img.__enter__ = lambda s: mock_img
            mock_img.__exit__ = MagicMock(return_value=False)
            mock_img.format = 'GIF'   # Valid image structure but not in [JPEG, PNG, WEBP]
            mock_img.width = 100
            mock_img.height = 100

            mock_open.side_effect = [mock_probe, mock_img]

            gif_file = SimpleUploadedFile('animation.gif', b'GIF89a' + b'0' * 100, content_type='image/gif')
            payload = {'scene': self.scene.id, 'image_file': gif_file}
            res = self.client.post(FAST_LANE_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid Magic Bytes', str(res.data))
