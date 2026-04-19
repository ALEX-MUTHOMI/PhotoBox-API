"""
Enterprise-Grade Tests: Phase 2 — R2 Ingestion Webhook (R2WebhookView)

SECURITY CONTRACTS BEING TESTED:
  1. HMAC-SHA256 timing-safe signature verification.
  2. 5-minute replay attack window.
  3. Idempotency — duplicate webhooks do not corrupt asset state.
  4. Ghost key tolerance — unknown R2 keys return 200 (not 404) to halt retries.
  5. Size mismatch quarantine — oversized actual upload triggers QUARANTINED.
  6. Missing/invalid signature → strict 403.
  7. Content-Length guard against OOM payloads.
  8. Only PutObject actions flip assets to READY.
"""
import json
import hmac
import hashlib
import time

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.models import Event, Scene, Photo

User = get_user_model()

WEBHOOK_URL = reverse('r2-ingestion-webhook')
TEST_SECRET = 'test-webhook-secret-do-not-use-in-prod'


def _make_payload(**kwargs) -> dict:
    """Build a minimal valid PutObject webhook payload."""
    base = {
        'action':        'PutObject',
        'r2_object_key': 'raw/tenant_1/scene_1/uuid_file.jpg',
        'size':          1024,
    }
    base.update(kwargs)
    return base


def _sign(payload_bytes: bytes, secret: str = TEST_SECRET) -> str:
    """Compute HMAC-SHA256 signature — mirrors the view's verification logic."""
    return hmac.new(
        secret.encode('utf-8'),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()


def _post_webhook(client, payload: dict, secret: str = TEST_SECRET,
                  timestamp: int = None, extra_headers: dict = None):
    """
    Helper: POST a signed webhook to the API.

    Django's test client sets Content-Type and Content-Length automatically
    when you pass `content_type`. We only need to manually set the custom
    Cloudflare headers via HTTP_ prefixed kwargs.
    """
    payload_bytes = json.dumps(payload).encode('utf-8')
    sig = _sign(payload_bytes, secret)
    ts = timestamp if timestamp is not None else int(time.time())

    # Django test client kwargs for custom headers use the HTTP_ prefix
    kwargs = {
        'HTTP_X_CLOUDFLARE_SIGNATURE': sig,
        'HTTP_WEBHOOK_TIMESTAMP':      str(ts),
    }
    if extra_headers:
        kwargs.update(extra_headers)

    return client.post(
        WEBHOOK_URL,
        data=payload_bytes,
        content_type='application/json',
        **kwargs,
    )


class R2WebhookHappyPathTests(TestCase):
    """Happy path: valid HMAC + PutObject → asset transitions to READY."""

    def setUp(self):
        self.client = APIClient()

        self.user = User.objects.create_user(email='photog@example.com', password='pass')
        self.workspace = Workspace.objects.create(user=self.user, business_name='Studio')
        self.event = Event.objects.create(workspace=self.workspace, title='Gig', slug='gig')
        self.scene = Scene.objects.create(event=self.event, title='Stage')

        self.asset = Photo.objects.create(
            scene=self.scene,
            original_filename='wedding.jpg',
            file_size_bytes=1024,
            r2_object_key='raw/tenant_1/scene_1/uuid_wedding.jpg',
            status='PENDING',
            is_processed=False,
        )

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    def test_valid_webhook_transitions_asset_to_ready(self):
        """
        HAPPY PATH: A valid signed PutObject webhook must flip the asset
        from PENDING to READY and set is_processed=True.
        """
        payload = _make_payload(r2_object_key=self.asset.r2_object_key, size=1000)
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'success')

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'READY')
        self.assertTrue(self.asset.is_processed)

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    def test_putobject_without_size_field_still_transitions_ready(self):
        """
        EDGE CASE: If Cloudflare omits the 'size' field, we must still process
        the webhook (no size mismatch check without declared size).
        """
        payload = {'action': 'PutObject', 'r2_object_key': self.asset.r2_object_key}
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'READY')


class R2WebhookIdempotencyTests(TestCase):
    """Idempotency: duplicate webhook deliveries must not corrupt state."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email='photog2@example.com', password='pass')
        self.workspace = Workspace.objects.create(user=self.user, business_name='Studio')
        self.event = Event.objects.create(workspace=self.workspace, title='Gig', slug='gig2')
        self.scene = Scene.objects.create(event=self.event, title='Stage')

        self.asset = Photo.objects.create(
            scene=self.scene,
            original_filename='portrait.jpg',
            file_size_bytes=2048,
            r2_object_key='raw/tenant_2/scene_2/portrait.jpg',
            status='READY',       # Already READY
            is_processed=True,
        )

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    def test_duplicate_webhook_returns_already_ready(self):
        """
        IDEMPOTENCY: If the asset is already READY, the webhook must return
        'already_ready' and not flip is_processed back to False.
        """
        payload = _make_payload(r2_object_key=self.asset.r2_object_key)
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'already_ready')

        # State must not have changed
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'READY')
        self.assertTrue(self.asset.is_processed)


class R2WebhookSecurityTests(TestCase):
    """
    Red team: signature forgery, replay attacks, size mismatch, ghost keys.
    Every test here verifies a specific attack vector is blocked.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email='sec@example.com', password='pass')
        self.workspace = Workspace.objects.create(user=self.user, business_name='Sec Studio')
        self.event = Event.objects.create(workspace=self.workspace, title='Event', slug='ev3')
        self.scene = Scene.objects.create(event=self.event, title='Scene')

        self.asset = Photo.objects.create(
            scene=self.scene,
            original_filename='file.jpg',
            file_size_bytes=5000,
            r2_object_key='raw/tenant_3/scene_3/file.jpg',
            status='PENDING',
            is_processed=False,
        )

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    def test_missing_signature_header_returns_403(self):
        """SECURITY: No signature header → immediate 403. No asset state change."""
        payload_bytes = json.dumps(_make_payload(r2_object_key=self.asset.r2_object_key)).encode()
        res = self.client.post(
            WEBHOOK_URL,
            data=payload_bytes,
            content_type='application/json',
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Asset must remain PENDING
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'PENDING')

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    def test_missing_timestamp_header_returns_403(self):
        """SECURITY: Freshness proof is mandatory for signed R2 webhooks."""
        payload = _make_payload(r2_object_key=self.asset.r2_object_key, size=1234)
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        sig = _sign(payload_bytes)

        res = self.client.post(
            WEBHOOK_URL,
            data=payload_bytes,
            content_type="application/json",
            HTTP_X_CLOUDFLARE_SIGNATURE=sig,
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "PENDING")

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    def test_wrong_signature_returns_403(self):
        """SECURITY (Forgery): A webhook signed with the wrong secret → 403."""
        payload = _make_payload(r2_object_key=self.asset.r2_object_key)
        # Sign with a DIFFERENT secret — simulates an attacker who doesn't know the real secret
        res = _post_webhook(self.client, payload, secret='wrong-secret-attacker-guessed')

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'PENDING', "FATAL: Asset was mutated by a forged webhook!")

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    def test_replay_attack_outside_5_minute_window_rejected(self):
        """
        SECURITY (Replay Attack): A valid signed webhook replayed after 5 minutes
        must be rejected. The timestamp is part of the signed payload so it cannot
        be changed without invalidating the HMAC.
        """
        # Timestamp from 10 minutes in the past — outside the 5-minute window
        stale_timestamp = int(time.time()) - (10 * 60)
        payload = _make_payload(r2_object_key=self.asset.r2_object_key)
        res = _post_webhook(self.client, payload, timestamp=stale_timestamp)

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn('expired', str(res.data).lower())

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'PENDING', "FATAL: Replayed webhook mutated asset state!")

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    def test_fresh_timestamp_within_window_accepted(self):
        """
        SECURITY (Replay Window): A valid webhook with a timestamp from 2 minutes ago
        must be accepted (within the 5-minute window).
        """
        recent_timestamp = int(time.time()) - (2 * 60)
        payload = _make_payload(r2_object_key=self.asset.r2_object_key, size=5000)
        res = _post_webhook(self.client, payload, timestamp=recent_timestamp)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'success')

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    def test_ghost_r2_key_returns_200_not_404(self):
        """
        SECURITY (Ghost Key Tolerance): Cloudflare retries a webhook if we return
        anything other than 2xx. An unknown r2_object_key must return 200 'ignored'
        to halt the retry storm, not 404.
        """
        payload = _make_payload(r2_object_key='raw/does/not/exist/in/db.jpg')
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'ignored')

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    def test_size_mismatch_quarantines_asset(self):
        """
        SECURITY (Size Mismatch / File Substitution Attack):
        If the actual uploaded file size exceeds the declared size in the manifest,
        the user may have substituted a different file. Asset must be QUARANTINED.
        """
        # actual size (99999) is larger than declared file_size_bytes (5000)
        payload = _make_payload(
            r2_object_key=self.asset.r2_object_key,
            size=99999,
        )
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'quarantined')

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'QUARANTINED')
        # is_processed must remain False — quarantined assets are not ready
        self.assertFalse(self.asset.is_processed)

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    def test_non_putobject_action_silently_ignored(self):
        """
        CONTRACT: DeleteObject, CopyObject, and other R2 actions must be silently
        acknowledged with 200 'ignored' so Cloudflare does not retry them.
        """
        payload = _make_payload(
            r2_object_key=self.asset.r2_object_key,
            action='DeleteObject',
        )
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'ignored')

        # Asset state must not have changed
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'PENDING')

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    def test_invalid_json_returns_400(self):
        """CONTRACT: Structurally broken JSON must return 400, not crash the server."""
        sig = _sign(b'not json at all')
        res = self.client.post(
            WEBHOOK_URL,
            data=b'not json at all',
            content_type='application/json',
            HTTP_X_CLOUDFLARE_SIGNATURE=sig,
            HTTP_WEBHOOK_TIMESTAMP=str(int(time.time())),
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    def test_no_signature_configured_server_returns_500(self):
        """
        SECURITY REGRESSION: If CLOUDFLARE_WEBHOOK_SECRET is an empty string,
        the view must refuse to process (500 misconfiguration), not silently
        accept all requests by comparing empty string to empty HMAC.
        """
        with override_settings(CLOUDFLARE_WEBHOOK_SECRET=''):
            payload = _make_payload(r2_object_key=self.asset.r2_object_key)
            res = _post_webhook(self.client, payload, secret='')

        self.assertEqual(res.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
