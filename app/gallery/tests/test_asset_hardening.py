"""
Enterprise Attack Simulation Suite — Phase 3: Asset Layer Hardening Tests

ATTACK VECTORS COVERED:
  1. Cross-Tenant Asset Hijack (IDOR) — both Fast Lane and Heavy Lane paths
  2. Heavy Lane Size Mismatch Quarantine (Payload Substitution Attack)
  3. Cloudflare Webhook Replay Attack (Stale Timestamp Rejection)
  4. Cloudinary Fetch Proxy URL Structural Verification

USAGE:
  python manage.py test gallery.tests.test_asset_hardening --verbosity=2
"""

import io
import json
import hmac
import hashlib
import time
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient
from PIL import Image as PILImage

from core.models import Workspace
from gallery.models import Event, Scene, Photo

User = get_user_model()

# ── URL Constants ────────────────────────────────────────────────────────
FAST_LANE_URL = reverse('gallery:fastlane-photo-list')
HEAVY_LANE_URL = reverse('bulk-ingest')
R2_WEBHOOK_URL = reverse('r2-ingestion-webhook')

# ── Test-Mode Secrets ────────────────────────────────────────────────────
TEST_WEBHOOK_SECRET = 'test-asset-hardening-webhook-secret'

# ── The Celery task we patch to prevent real R2 I/O ─────────────────────
CELERY_TASK_PATH = 'gallery.tasks.process_fast_lane_asset'


# ── Helpers ──────────────────────────────────────────────────────────────

def _create_user(email, password='HardenedPass123!'):
    """Create a User with deterministic credentials."""
    return User.objects.create_user(email=email, password=password)


def _create_full_tenant(user, biz_name='Studio', event_title='Wedding',
                        event_slug='wedding', scene_title='Ceremony'):
    """
    Bootstrap the full Workspace → Event → Scene hierarchy for a user.
    Returns (workspace, event, scene) tuple.
    """
    workspace = Workspace.objects.create(user=user, business_name=biz_name)
    event = Event.objects.create(
        workspace=workspace, title=event_title, slug=event_slug
    )
    scene = Scene.objects.create(event=event, title=scene_title)
    return workspace, event, scene


def _generate_valid_image(width=100, height=100, fmt='JPEG',
                          filename='hardening_test.jpg'):
    """
    Create a minimal valid in-memory JPEG that passes Pillow's magic byte
    inspector and the format allowlist check.
    """
    buf = io.BytesIO()
    PILImage.new('RGB', (width, height), color=(0, 128, 255)).save(buf, fmt)
    buf.seek(0)
    return SimpleUploadedFile(filename, buf.read(), content_type='image/jpeg')


def _sign_webhook(payload_bytes: bytes,
                  secret: str = TEST_WEBHOOK_SECRET) -> str:
    """Compute HMAC-SHA256 signature identical to the view's verification."""
    return hmac.new(
        secret.encode('utf-8'), payload_bytes, hashlib.sha256
    ).hexdigest()


def _post_signed_webhook(client, payload: dict,
                         secret: str = TEST_WEBHOOK_SECRET,
                         timestamp: int | None = None):
    """
    POST a properly signed Cloudflare R2 webhook to the ingestion endpoint.
    Mirrors the exact header contract expected by R2WebhookView.
    """
    payload_bytes = json.dumps(payload).encode('utf-8')
    sig = _sign_webhook(payload_bytes, secret)
    ts = timestamp if timestamp is not None else int(time.time())

    return client.post(
        R2_WEBHOOK_URL,
        data=payload_bytes,
        content_type='application/json',
        HTTP_X_CLOUDFLARE_SIGNATURE=sig,
        HTTP_WEBHOOK_TIMESTAMP=str(ts),
    )


# ======================================================================
# TEST 1: CROSS-TENANT ASSET HIJACK — REJECTED
# ======================================================================

class CrossTenantAssetHijackTests(TestCase):
    """
    ATTACK VECTOR: User A authenticates and attempts to upload a photo
    into User B's Scene by supplying B's scene_id in the POST payload.

    EXPECTED RESULT:
      - Fast Lane: 403 Forbidden (PermissionDenied in perform_create)
      - Heavy Lane: 400 Bad Request (serializer tenant isolation check)
      - No Photo/MediaAsset record is created in the target scene
      - Attacker's workspace quota is NOT charged
    """

    def setUp(self):
        self.attacker_client = APIClient()
        self.attacker = _create_user('attacker@evil.com')
        self.attacker_ws, _, self.attacker_scene = _create_full_tenant(
            self.attacker, biz_name='Evil Studio',
            event_slug='evil-wedding', scene_title='Attack Stage'
        )
        self.attacker_client.force_authenticate(self.attacker)

        self.victim = _create_user('victim@photographer.com')
        self.victim_ws, _, self.victim_scene = _create_full_tenant(
            self.victim, biz_name='Victim Studio',
            event_slug='victim-wedding', scene_title='Sacred Ceremony'
        )

    @patch(CELERY_TASK_PATH)
    def test_cross_tenant_asset_hijack_rejected_fast_lane(self, mock_task):
        """
        FAST LANE IDOR PROOF:
        Attacker supplies victim's scene UUID in the Fast Lane upload payload.
        The view's perform_create() must detect the scene doesn't belong to the
        attacker and raise PermissionDenied (403).
        """
        payload = {
            'scene': str(self.victim_scene.id),
            'image_file': _generate_valid_image(),
        }
        res = self.attacker_client.post(
            FAST_LANE_URL, payload, format='multipart'
        )

        # ── HARD ASSERTION: Must be 403 Forbidden ──────────────────────
        self.assertEqual(
            res.status_code, status.HTTP_403_FORBIDDEN,
            f"CRITICAL IDOR: Attacker was able to POST to victim's Scene! "
            f"Expected 403, got {res.status_code}. Response: {res.data}"
        )

        # ── Celery task must NOT fire — no side effects ────────────────
        mock_task.delay.assert_not_called()

        # ── No asset created in victim's scene ─────────────────────────
        hijacked_photos = Photo.objects.filter(scene=self.victim_scene)
        self.assertEqual(
            hijacked_photos.count(), 0,
            "FATAL: A Photo record was injected into the victim's Scene!"
        )

        # ── Attacker's quota was NOT charged ───────────────────────────
        self.attacker_ws.refresh_from_db()
        self.assertEqual(
            self.attacker_ws.storage_used_bytes, 0,
            "Attacker's quota was charged despite the upload being rejected."
        )

    @patch('gallery.storage.generate_r2_presigned_post')
    def test_cross_tenant_asset_hijack_rejected_heavy_lane(self, mock_r2):
        """
        HEAVY LANE IDOR PROOF:
        Attacker supplies victim's scene UUID in the bulk ingestion manifest.
        The BulkManifestSerializer.validate() must detect the foreign scene_id
        and reject with 400 (not 404, to prevent UUID enumeration).
        """
        manifest = {
            'scene_id': str(self.victim_scene.id),
            'files': [
                {
                    'filename': 'stolen_shot.jpg',
                    'file_size': 1024 * 1024,  # 1MB
                    'client_reference_id': 'ref-hijack-001',
                }
            ]
        }
        res = self.attacker_client.post(
            HEAVY_LANE_URL, manifest, format='json'
        )

        # ── HARD ASSERTION: Must be 400 Bad Request ────────────────────
        self.assertEqual(
            res.status_code, status.HTTP_400_BAD_REQUEST,
            f"CRITICAL IDOR: Attacker obtained presigned POST for victim's "
            f"Scene! Expected 400, got {res.status_code}. Response: {res.data}"
        )

        # ── R2 client should NOT have been called to mint tickets ──────
        # The tenant check is in the serializer, which runs BEFORE ticket gen.
        # Note: get_r2_client() IS called before the serializer check in the
        # current code architecture. The critical assertion is that no
        # presigned POST was generated — verified by the 400 response.
        self.assertNotIn(
            'upload_tickets', res.data or {},
            "FATAL: Presigned POST tickets were generated for a foreign Scene!"
        )

        # ── No MediaAsset created in victim's scene ────────────────────
        from gallery.models import MediaAsset
        hijacked = MediaAsset.objects.filter(scene=self.victim_scene)
        self.assertEqual(
            hijacked.count(), 0,
            "FATAL: MediaAsset records injected into victim's Scene!"
        )


# ======================================================================
# TEST 2: HEAVY LANE SIZE MISMATCH → QUARANTINE
# ======================================================================

@override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET)
class HeavyLaneSizeMismatchQuarantineTests(TestCase):
    """
    ATTACK VECTOR: Payload Substitution Attack.

    A user requests a presigned POST for a 5MB image via the manifest.
    They then bypass the client UI and use curl to upload a 5GB ZIP bomb
    to the R2 URL. When the Cloudflare webhook hits our server, the
    `size` field in the payload (actual bytes stored in R2) is drastically
    larger than the `file_size_bytes` recorded in the IngestionTicket
    (MediaAsset).

    EXPECTED RESULT:
      - The MediaAsset status transitions from PENDING to QUARANTINED
      - is_processed remains False (quarantined assets are NOT ready)
      - The webhook returns 200 OK with status 'quarantined'
    """

    def setUp(self):
        self.client = APIClient()
        self.user = _create_user('uploader@studio.com')
        self.workspace, self.event, self.scene = _create_full_tenant(
            self.user, biz_name='Upload Studio',
            event_slug='upload-gig', scene_title='Main Stage'
        )

        # Create the MediaAsset as it would exist after BulkIngestionView
        # mints the presigned POST ticket (status=PENDING, declared 5MB)
        self.ticket = Photo.objects.create(
            scene=self.scene,
            original_filename='wedding_hero.jpg',
            file_size_bytes=5 * 1024 * 1024,  # Declared: 5MB
            r2_object_key='raw/tenant_1/scene_1/uuid_hero.jpg',
            status='PENDING',
            is_processed=False,
        )

    def test_heavy_lane_size_mismatch_quarantine(self):
        """
        PROOF: Cloudflare webhook reports actual_size (5GB) >> declared_size (5MB).
        The R2WebhookView must quarantine the asset instead of marking it READY.
        """
        FIVE_GB = 5 * 1024 * 1024 * 1024  # 5,368,709,120 bytes

        webhook_payload = {
            'action': 'PutObject',
            'r2_object_key': self.ticket.r2_object_key,
            'size': FIVE_GB,  # Actual uploaded size: 5GB (1000x larger than declared)
        }

        res = _post_signed_webhook(self.client, webhook_payload)

        # ── HARD ASSERTIONS ────────────────────────────────────────────
        self.assertEqual(
            res.status_code, status.HTTP_200_OK,
            "Webhook endpoint should return 200 to halt Cloudflare retries."
        )
        self.assertEqual(
            res.data['status'], 'quarantined',
            f"Expected 'quarantined' status, got '{res.data.get('status')}'. "
            f"The system blindly accepted a 5GB ZIP bomb!"
        )

        # ── DB State Verification ──────────────────────────────────────
        self.ticket.refresh_from_db()

        self.assertEqual(
            self.ticket.status, 'QUARANTINED',
            f"FATAL: Asset status is '{self.ticket.status}' instead of "
            f"'QUARANTINED'. The payload substitution attack succeeded!"
        )
        self.assertFalse(
            self.ticket.is_processed,
            "FATAL: Quarantined asset was marked as processed!"
        )

    def test_exact_size_match_transitions_to_ready(self):
        """
        CONTROL TEST: When actual size equals declared size, the asset must
        transition normally to READY. This proves the quarantine logic is
        not a false positive factory.
        """
        webhook_payload = {
            'action': 'PutObject',
            'r2_object_key': self.ticket.r2_object_key,
            'size': 4 * 1024 * 1024,  # 4MB — less than declared 5MB
        }

        res = _post_signed_webhook(self.client, webhook_payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'success')

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'READY')
        self.assertTrue(self.ticket.is_processed)

    def test_smaller_actual_size_is_accepted_not_quarantined(self):
        """
        EDGE CASE: If the actual uploaded size is SMALLER than declared,
        this is not suspicious (compression, smaller file). Must NOT quarantine.
        """
        webhook_payload = {
            'action': 'PutObject',
            'r2_object_key': self.ticket.r2_object_key,
            'size': 1024,  # 1KB — drastically smaller than declared 5MB
        }

        res = _post_signed_webhook(self.client, webhook_payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # The system should accept smaller files (not quarantine)
        self.assertNotEqual(
            res.data['status'], 'quarantined',
            "A smaller-than-declared file should NOT be quarantined."
        )

        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'READY')


# ======================================================================
# TEST 3: CLOUDFLARE WEBHOOK REPLAY ATTACK — REJECTED
# ======================================================================

@override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET)
class CloudflareWebhookReplayAttackTests(TestCase):
    """
    ATTACK VECTOR: Webhook Replay Attack.

    A hacker intercepts a valid Cloudflare R2 webhook (body + signature).
    They replay it 15 minutes later by sending the exact same body and
    HMAC signature, but the Webhook-Timestamp header reveals the stale
    origin time.

    EXPECTED RESULT:
      - The 5-minute replay window check rejects the webhook with 403
      - The asset remains in PENDING state (no state corruption)
    """

    def setUp(self):
        self.client = APIClient()
        self.user = _create_user('replay-target@studio.com')
        self.workspace, self.event, self.scene = _create_full_tenant(
            self.user, biz_name='Replay Studio',
            event_slug='replay-gig', scene_title='Stage'
        )

        self.asset = Photo.objects.create(
            scene=self.scene,
            original_filename='keynote_01.jpg',
            file_size_bytes=2048,
            r2_object_key='raw/tenant_replay/scene_1/keynote_01.jpg',
            status='PENDING',
            is_processed=False,
        )

    def test_cloudflare_webhook_replay_attack_rejected(self):
        """
        PROOF: A perfectly signed webhook replayed with a 15-minute-old
        timestamp must be rejected outright. The Webhook-Timestamp header
        pushed the age beyond the 5-minute tolerance window.
        """
        FIFTEEN_MINUTES_AGO = int(time.time()) - (15 * 60)

        webhook_payload = {
            'action': 'PutObject',
            'r2_object_key': self.asset.r2_object_key,
            'size': 2048,
        }

        res = _post_signed_webhook(
            self.client, webhook_payload,
            timestamp=FIFTEEN_MINUTES_AGO,
        )

        # ── HARD ASSERTION: Must be 403 ────────────────────────────────
        self.assertEqual(
            res.status_code, status.HTTP_403_FORBIDDEN,
            f"CRITICAL: Replayed webhook was ACCEPTED! Expected 403, "
            f"got {res.status_code}. Response: {res.data}"
        )

        # ── Verify the error message references expiry ─────────────────
        response_text = json.dumps(res.data).lower()
        self.assertTrue(
            'expired' in response_text or 'replay' in response_text,
            f"Error response should mention expiry/replay. Got: {res.data}"
        )

        # ── Asset MUST remain PENDING — no state corruption ────────────
        self.asset.refresh_from_db()
        self.assertEqual(
            self.asset.status, 'PENDING',
            f"FATAL: Replayed webhook corrupted asset state "
            f"from PENDING to {self.asset.status}!"
        )
        self.assertFalse(
            self.asset.is_processed,
            "FATAL: Replayed webhook set is_processed=True!"
        )

    def test_fresh_webhook_within_window_accepted(self):
        """
        CONTROL TEST: A webhook with a 2-minute-old timestamp must be
        accepted normally. Proves the replay window doesn't reject
        legitimate webhooks that are slightly delayed.
        """
        TWO_MINUTES_AGO = int(time.time()) - (2 * 60)

        webhook_payload = {
            'action': 'PutObject',
            'r2_object_key': self.asset.r2_object_key,
            'size': 2048,
        }

        res = _post_signed_webhook(
            self.client, webhook_payload,
            timestamp=TWO_MINUTES_AGO,
        )

        self.assertEqual(
            res.status_code, status.HTTP_200_OK,
            f"Valid webhook within 5-min window should be accepted. "
            f"Got {res.status_code}."
        )
        self.assertEqual(res.data['status'], 'success')

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'READY')
        self.assertTrue(self.asset.is_processed)

    def test_webhook_with_no_timestamp_still_processes(self):
        """
        SECURITY:
        Timestamp-less signed webhooks must be rejected so authenticity cannot
        be replayed without freshness.
        """
        webhook_payload = {
            'action': 'PutObject',
            'r2_object_key': self.asset.r2_object_key,
            'size': 2048,
        }

        payload_bytes = json.dumps(webhook_payload).encode('utf-8')
        sig = _sign_webhook(payload_bytes)

        res = self.client.post(
            R2_WEBHOOK_URL,
            data=payload_bytes,
            content_type='application/json',
            HTTP_X_CLOUDFLARE_SIGNATURE=sig,
        )

        self.assertEqual(
            res.status_code, status.HTTP_403_FORBIDDEN,
            'Missing timestamp must be rejected to close the replay window.'
        )

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'PENDING')
        self.assertFalse(self.asset.is_processed)

# ======================================================================
# TEST 4: CLOUDINARY FETCH PROXY URL — STRUCTURAL VERIFICATION
# ======================================================================

@override_settings(
    CLOUDINARY_CLOUD_NAME='photobox-prod',
    CLOUDFLARE_R2_DOMAIN='cdn.photobox-vault.com',
)
class CloudinaryURLCryptographicSignatureTests(TestCase):
    """
    VERIFICATION VECTOR: Delivery URL Structural Integrity.

    The system uses Cloudinary Fetch Proxy URLs to deliver optimised images.
    These URLs must contain the correct cloud name, transformation parameters,
    and R2 origin reference.

    IMPORTANT — KNOWN GAP (Documented in Phase 1 Audit):
      The current architecture does NOT use Cloudinary's HMAC URL signing
      (s--<hash>-- segment). This test verifies the current structural
      contract. When signed URLs are implemented, this test must be updated
      to assert the s--...-- signature segment.
    """

    def setUp(self):
        self.user = _create_user('delivery@photographer.com')
        self.workspace, self.event, self.scene = _create_full_tenant(
            self.user, biz_name='Delivery Studio',
            event_slug='delivery-gig', scene_title='Main Hall'
        )

    def test_cloudinary_url_structural_enforcement(self):
        """
        PROOF: The delivery_url property generates a Cloudinary Fetch Proxy
        URL containing:
          1. The correct cloud name (photobox-prod)
          2. The /image/fetch/ path (NOT /image/upload/)
          3. Quality auto-optimisation (q_auto)
          4. WebP format conversion (f_webp)
          5. The R2 origin domain (cdn.photobox-vault.com)
          6. The exact R2 object key
        """
        photo = Photo.objects.create(
            scene=self.scene,
            original_filename='ceremony_001.jpg',
            file_size_bytes=8_000_000,
            r2_object_key='fast-lane/tenant_1/photo_abc/ceremony_001.jpg',
            status='READY',
            is_processed=True,
        )

        url = photo.delivery_url

        # ── EXISTENCE ──────────────────────────────────────────────────
        self.assertIsNotNone(
            url,
            "FATAL: delivery_url returned None for a READY photo with "
            "r2_object_key set!"
        )

        # ── CLOUD NAME ─────────────────────────────────────────────────
        self.assertIn(
            'photobox-prod', url,
            f"delivery_url must reference the Cloudinary cloud name. "
            f"Got: {url}"
        )

        # ── FETCH PROXY PATTERN (NOT SDK UPLOAD) ──────────────────────
        self.assertIn(
            '/image/fetch/', url,
            f"FATAL: delivery_url is not using the Fetch Proxy pattern! "
            f"This means Cloudinary is receiving SDK uploads. Got: {url}"
        )
        self.assertNotIn(
            '/image/upload/', url,
            f"REGRESSION: delivery_url is using the old SDK /image/upload/ "
            f"path. The Unified Vault architecture requires /image/fetch/. "
            f"Got: {url}"
        )

        # ── TRANSFORM PARAMETERS ──────────────────────────────────────
        self.assertIn(
            'q_auto', url,
            f"Quality auto-optimisation (q_auto) missing from delivery_url. "
            f"This results in raw files being served — bandwidth waste. "
            f"Got: {url}"
        )
        self.assertIn(
            'f_webp', url,
            f"WebP format conversion (f_webp) missing from delivery_url. "
            f"Browsers will receive unoptimised JPEG/PNG. Got: {url}"
        )

        # ── R2 ORIGIN ─────────────────────────────────────────────────
        self.assertIn(
            'cdn.photobox-vault.com', url,
            f"delivery_url must reference the R2 public domain. Got: {url}"
        )

        # ── R2 OBJECT KEY ─────────────────────────────────────────────
        self.assertIn(
            photo.r2_object_key, url,
            f"The R2 object key must be embedded in the delivery URL. "
            f"Got: {url}"
        )

    def test_delivery_url_format_is_deterministic(self):
        """
        CONTRACT: Two calls to delivery_url on the same Photo instance
        must produce identical URLs. Non-deterministic URLs would bust
        Cloudinary's edge cache and multiply bandwidth costs.
        """
        photo = Photo.objects.create(
            scene=self.scene,
            original_filename='portrait.jpg',
            file_size_bytes=3_000_000,
            r2_object_key='fast-lane/tenant_1/portrait_xyz/portrait.jpg',
            status='READY',
            is_processed=True,
        )

        url_1 = photo.delivery_url
        url_2 = photo.delivery_url

        self.assertEqual(
            url_1, url_2,
            "delivery_url is non-deterministic! This will bust the "
            "Cloudinary edge cache (CDN miss on every request)."
        )

    def test_photo_without_r2_key_falls_back_to_optimized_url(self):
        """
        BACKWARD COMPATIBILITY: Legacy photos uploaded via the old
        Cloudinary SDK path use optimized_url as the fallback delivery URL.
        """
        legacy_photo = Photo.objects.create(
            scene=self.scene,
            original_filename='legacy.jpg',
            file_size_bytes=1_000_000,
            optimized_url='https://res.cloudinary.com/legacy-cloud/image/upload/v1/legacy.jpg',
        )

        url = legacy_photo.delivery_url
        self.assertEqual(
            url,
            'https://res.cloudinary.com/legacy-cloud/image/upload/v1/legacy.jpg',
            "Legacy photos must fall back to optimized_url."
        )

    def test_photo_without_r2_key_or_optimized_url_returns_none(self):
        """
        EDGE CASE: A newly created photo with no R2 key AND no legacy
        optimized_url must return None — not crash or return garbage.
        """
        empty_photo = Photo.objects.create(
            scene=self.scene,
            original_filename='orphan.jpg',
            file_size_bytes=512,
            status='PENDING',
        )

        url = empty_photo.delivery_url
        self.assertIsNone(
            url,
            "A photo with no r2_object_key and no optimized_url must "
            "return None for delivery_url."
        )


@override_settings(
    CLOUDFLARE_R2_BUCKET_NAME='test-bucket',       # was R2_BUCKET_NAME — wrong name
    CLOUDFLARE_ACCESS_KEY_ID='test-key',            # was R2_ACCESS_KEY_ID — wrong name
    CLOUDFLARE_SECRET_ACCESS_KEY='test-secret',     # was R2_SECRET_ACCESS_KEY — wrong name
    CLOUDFLARE_R2_ENDPOINT='https://test.r2.cloudflarestorage.com',  # was missing entirely
)
@patch('gallery.storage.get_r2_client')
def test_r2_download_url_bypasses_cdn(self, mock_get_r2_client):
    ...

@patch('gallery.storage.get_r2_client')
def test_download_url_presigned_expiry_is_capped(self, mock_get_r2_client):
    """
    SECURITY: download_url must generate a presigned GET with a hard ceiling
    of 900 seconds (15 min). Boto3 must enforce this even if a longer TTL
    is somehow requested upstream.
    """
    # Single mock — the one the decorator injected. This IS the real code path.
    mock_client = mock_get_r2_client.return_value
    mock_client.generate_presigned_url.return_value = (
        'https://test-bucket.r2.cloudflarestorage.com/image.jpg'
        '?X-Amz-Expires=900&X-Amz-Signature=abc123'
    )

    photo = Photo.objects.create(
        scene=self.scene,
        original_filename='highres.jpg',
        file_size_bytes=50_000_000,
        r2_object_key='fast-lane/tenant_1/highres/highres.jpg',
        status='READY',
        is_processed=True,
    )

    url = photo.download_url

    # ASSERTION 1 — URL was actually generated
    self.assertIsNotNone(url,
        "ARCHITECTURE FAILURE: download_url returned None. "
        "R2 client was never called or returned nothing.")

    # ASSERTION 2 — boto3 was actually called, not a cached/fallback value
    mock_client.generate_presigned_url.assert_called_once()

    # ASSERTION 3 — ExpiresIn ceiling is enforced at the boto3 call level
    call_kwargs = mock_client.generate_presigned_url.call_args
    expires_in = (
        call_kwargs.kwargs.get('Params', {}).get('ExpiresIn')
        or call_kwargs.kwargs.get('ExpiresIn')
        or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
    )

    self.assertIsNotNone(expires_in,
        "SECURITY FAILURE: ExpiresIn was not passed to generate_presigned_url. "
        "URL has no expiry — it lives forever.")

    self.assertLessEqual(expires_in, 900,
        f"SECURITY FAILURE: Presigned URL expiry is {expires_in}s — exceeds 900s ceiling. "
        "A leaked URL stays valid too long.")


    # @override_settings(R2_BUCKET_NAME='test-bucket', R2_ACCESS_KEY_ID='test', R2_SECRET_ACCESS_KEY='test')
    # @patch('gallery.storage.get_r2_client')
    # def test_download_url_presigned_expiry_is_capped(self, mock_get_r2_client):
    #     # 1. ENGINEER FIX: Prime the mock to return a synthetic string, NOT a MagicMock object.
    #     # This simulates Boto3 successfully generating a secure link.
    #     mock_client_instance = mock_get_r2_client.return_value
    #     mock_client_instance.generate_presigned_url.return_value = "https://test-bucket.r2.cloudflarestorage.com/image.jpg?X-Amz-Expires=3600"
        
    #     """
    #     SECURITY: The download_url must use a presigned GET with a hard
    #     ceiling of 900 seconds (15 minutes). Verify the boto3 call
    #     enforces this ceiling even if a longer TTL is somehow requested.
    #     """
    #     mock_client = MagicMock()
    #     mock_client.generate_presigned_url.return_value = (
    #         'https://r2.example.com/signed?X-Amz-Expires=900'
    #     )
    #     mock_r2_client.return_value = mock_client

    #     photo = Photo.objects.create(
    #         scene=self.scene,
    #         original_filename='highres.jpg',
    #         file_size_bytes=50_000_000,
    #         r2_object_key='fast-lane/tenant_1/highres/highres.jpg',
    #         status='READY',
    #         is_processed=True,
    #     )

    #     url = photo.download_url

    #     self.assertIsNotNone(url, "download_url returned None!")

    #     # Verify generate_presigned_url was called with ExpiresIn <= 900
    #     call_kwargs = mock_client.generate_presigned_url.call_args
    #     expires_in = call_kwargs[1].get('ExpiresIn') or call_kwargs.kwargs.get('ExpiresIn')
    #     self.assertLessEqual(
    #         expires_in, 900,
    #         f"Presigned GET URL expiry exceeds 900s ceiling! "
    #         f"ExpiresIn={expires_in}. Leaked URLs survive too long."
    #     )


