"""
Enterprise-Grade Tests: Phase 2 — R2 Ingestion Webhook (R2WebhookView)

SECURITY CONTRACTS BEING TESTED:
  1. HMAC-SHA256 timing-safe signature verification.
  2. 5-minute replay attack window.
  3. Idempotency — duplicate webhooks do not corrupt asset state.
  4. Ghost key tolerance — unknown R2 keys return 200 (not 404) to halt retries.
  5. Size mismatch quarantine — oversized R2 object triggers QUARANTINED.
  6. Missing/invalid signature → strict 403.
  7. Content-Length guard against OOM payloads.
  8. Only PutObject actions flip assets to READY.
  9. Authoritative size from R2 head_object — payload size is telemetry only.
"""
import json
import hmac
import hashlib
import time
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.models import Event, Scene, Photo

User = get_user_model()

WEBHOOK_URL = reverse('r2-ingestion-webhook')
ALIAS_WEBHOOK_URL = reverse('r2-webhook-ingress')
TEST_SECRET = 'test-webhook-secret-do-not-use-in-prod'
HEAD_PATCH = 'ingestion.views.r2_object_size'

# Eager Celery would otherwise run compute_photo_phash against placeholder R2.
_PHASH_ENQUEUE_PATCHER = None


def setUpModule():
    global _PHASH_ENQUEUE_PATCHER
    _PHASH_ENQUEUE_PATCHER = patch("ingestion.views.compute_photo_phash.apply_async")
    _PHASH_ENQUEUE_PATCHER.start()


def tearDownModule():
    if _PHASH_ENQUEUE_PATCHER is not None:
        _PHASH_ENQUEUE_PATCHER.stop()


def _make_payload(**kwargs) -> dict:
    """Build a minimal valid PutObject webhook payload."""
    base = {
        'action':        'PutObject',
        'r2_object_key': 'raw/tenant_1/scene_1/uuid_file.jpg',
        'size':          1024,
    }
    base.update(kwargs)
    return base


def _sign(timestamp: int, payload_bytes: bytes, secret: str = TEST_SECRET) -> str:
    """Compute HMAC-SHA256 over '<timestamp>.<raw_body>'."""
    return hmac.new(
        secret.encode('utf-8'),
        f"{timestamp}.".encode("ascii") + payload_bytes,
        hashlib.sha256,
    ).hexdigest()


def _post_webhook(client, payload: dict, secret: str = TEST_SECRET,
                  timestamp: int = None, signed_timestamp: int = None,
                  extra_headers: dict = None):
    """
    Helper: POST a signed webhook to the API.

    Django's test client sets Content-Type and Content-Length automatically
    when you pass `content_type`. We only need to manually set the custom
    Cloudflare headers via HTTP_ prefixed kwargs.
    """
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode('utf-8')
    transmitted_ts = int(time.time()) if timestamp is None else int(timestamp)
    signed_ts = (
        transmitted_ts if signed_timestamp is None else int(signed_timestamp)
    )
    sig = _sign(signed_ts, payload_bytes, secret)

    # Django test client kwargs for custom headers use the HTTP_ prefix
    kwargs = {
        'HTTP_X_CLOUDFLARE_SIGNATURE': sig,
        'HTTP_WEBHOOK_TIMESTAMP':      str(transmitted_ts),
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
    @patch(HEAD_PATCH, return_value=1000)
    def test_valid_webhook_transitions_asset_to_ready(self, _mock_head):
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
    @patch(HEAD_PATCH, return_value=1000)
    def test_putobject_without_size_field_requires_r2_head(self, mock_head):
        """
        When Cloudflare omits payload size, READY still requires a successful
        R2 head_object reconciliation.
        """
        payload = {'action': 'PutObject', 'r2_object_key': self.asset.r2_object_key}
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mock_head.assert_called_once_with(self.asset.r2_object_key)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'READY')

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    @patch(HEAD_PATCH, return_value=1024)
    def test_smaller_r2_object_still_transitions_to_ready(self, _mock_head):
        """Authoritative size comes from R2 HEAD — a smaller object must not block READY."""
        self.asset.file_size_bytes = 5000
        self.asset.save(update_fields=['file_size_bytes'])
        payload = _make_payload(r2_object_key=self.asset.r2_object_key, size=1024)
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'success')
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'READY')
        self.assertTrue(self.asset.is_processed)

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    @patch("ingestion.views.compute_photo_phash.apply_async")
    @patch("ingestion.views.generate_photo_web_derivative.apply_async")
    @patch(HEAD_PATCH, return_value=1024)
    def test_putobject_enqueues_derivative_task(self, _mock_head, mock_delay, mock_phash):
        payload = _make_payload(r2_object_key=self.asset.r2_object_key, size=1024)
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mock_delay.assert_called_once_with(args=[str(self.asset.id)], throw=False)
        mock_phash.assert_called_once_with(args=[str(self.asset.id)], throw=False)


class R2WebhookAliasRouteTests(TestCase):
    """Smoke test: legacy ingress URL name still resolves to the same webhook handler."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email='alias@example.com', password='pass')
        self.workspace = Workspace.objects.create(user=self.user, business_name='Alias Studio')
        self.event = Event.objects.create(workspace=self.workspace, title='Gig', slug='alias-gig')
        self.scene = Scene.objects.create(event=self.event, title='Stage')
        self.asset = Photo.objects.create(
            scene=self.scene,
            original_filename='alias.jpg',
            file_size_bytes=50000,
            r2_object_key='raw/tenant_alias/scene/alias.jpg',
            status='PENDING',
            is_processed=False,
        )

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    @patch(HEAD_PATCH, return_value=50000)
    def test_valid_signature_via_ingress_alias(self, _mock_head):
        payload = _make_payload(r2_object_key=self.asset.r2_object_key, size=50000)
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode('utf-8')
        ts = int(time.time())
        sig = _sign(ts, payload_bytes)

        res = self.client.post(
            ALIAS_WEBHOOK_URL,
            data=payload_bytes,
            content_type='application/json',
            HTTP_X_CLOUDFLARE_SIGNATURE=sig,
            HTTP_WEBHOOK_TIMESTAMP=str(ts),
        )

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res['Deprecation'], 'true')
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'READY')
        self.assertTrue(self.asset.is_processed)

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    @patch(HEAD_PATCH, return_value=1024)
    def test_canonical_webhook_does_not_advertise_deprecation(self, _mock_head):
        payload = _make_payload(r2_object_key=self.asset.r2_object_key, size=1024)
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.has_header('Deprecation'))


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
    @patch(HEAD_PATCH, return_value=2048)
    def test_duplicate_webhook_returns_already_ready(self, _mock_head):
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

    @patch("ingestion.views.compute_photo_phash.apply_async")
    @patch("ingestion.views.generate_photo_web_derivative.apply_async")
    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    @patch(HEAD_PATCH, return_value=2048)
    def test_duplicate_ready_webhook_does_not_enqueue_duplicate_derivative(
        self, _mock_head, mock_delay, mock_phash
    ):
        """
        IDEMPOTENCY: Replayed object-created events for an already READY asset
        must not fan out duplicate derivative tasks.
        """
        payload = _make_payload(r2_object_key=self.asset.r2_object_key)
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'already_ready')
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'READY')
        self.assertTrue(self.asset.is_processed)
        mock_delay.assert_not_called()
        mock_phash.assert_not_called()


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
        sig = _sign(int(time.time()), payload_bytes)

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
    def test_timestamp_tampering_invalidates_signature(self):
        """SECURITY: Changing the timestamp header without re-signing must fail closed."""
        original_ts = int(time.time())
        forged_ts = original_ts + 30
        payload = _make_payload(r2_object_key=self.asset.r2_object_key, size=1234)
        res = _post_webhook(
            self.client,
            payload,
            timestamp=forged_ts,
            signed_timestamp=original_ts,
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, "PENDING")

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    def test_payload_tampering_rejection(self):
        """SECURITY: HMAC binds timestamp + raw body — tampering invalidates the signature."""
        original_payload = _make_payload(r2_object_key=self.asset.r2_object_key, size=5000)
        payload_bytes = json.dumps(original_payload, separators=(",", ":")).encode('utf-8')
        ts = int(time.time())
        sig = _sign(ts, payload_bytes)

        tampered_payload = original_payload.copy()
        tampered_payload['size'] = 999999
        tampered_bytes = json.dumps(tampered_payload, separators=(",", ":")).encode('utf-8')

        res = self.client.post(
            WEBHOOK_URL,
            data=tampered_bytes,
            content_type='application/json',
            HTTP_X_CLOUDFLARE_SIGNATURE=sig,
            HTTP_WEBHOOK_TIMESTAMP=str(ts),
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'PENDING')

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
    @patch(HEAD_PATCH, return_value=5000)
    def test_fresh_timestamp_within_window_accepted(self, _mock_head):
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
    @patch(HEAD_PATCH, return_value=99999)
    def test_size_mismatch_quarantines_asset(self, _mock_head):
        """
        SECURITY (Size Mismatch / File Substitution Attack):
        If the R2 object is larger than the declared manifest size, quarantine.
        """
        payload = _make_payload(
            r2_object_key=self.asset.r2_object_key,
            size=99999,
        )
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'quarantined')

        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'QUARANTINED')
        self.assertFalse(self.asset.is_processed)

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    @patch(HEAD_PATCH)
    def test_r2_head_failure_returns_503(self, mock_head):
        """Fail closed when R2 is unreachable — Cloudflare may retry."""
        mock_head.side_effect = ClientError(
            {"Error": {"Code": "503", "Message": "slow down"}},
            "HeadObject",
        )
        payload = _make_payload(r2_object_key=self.asset.r2_object_key)
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'PENDING')

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    @patch(HEAD_PATCH, return_value=None)
    def test_missing_r2_object_quarantines_asset(self, _mock_head):
        """Phantom upload: DB row exists but R2 object does not."""
        payload = _make_payload(r2_object_key=self.asset.r2_object_key)
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'quarantined')
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, 'QUARANTINED')

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
        ts = int(time.time())
        sig = _sign(ts, b'not json at all')
        res = self.client.post(
            WEBHOOK_URL,
            data=b'not json at all',
            content_type='application/json',
            HTTP_X_CLOUDFLARE_SIGNATURE=sig,
            HTTP_WEBHOOK_TIMESTAMP=str(ts),
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


class R2WebhookLostTransitionTests(TestCase):
    """Concurrent transition losers must not enqueue duplicate derivative work."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email='race@example.com', password='pass')
        self.workspace = Workspace.objects.create(user=self.user, business_name='Race Studio')
        self.event = Event.objects.create(workspace=self.workspace, title='Gig', slug='race-gig')
        self.scene = Scene.objects.create(event=self.event, title='Stage')
        self.asset = Photo.objects.create(
            scene=self.scene,
            original_filename='race.jpg',
            file_size_bytes=1024,
            r2_object_key='raw/tenant_race/scene/race.jpg',
            status='PENDING',
            is_processed=False,
            media_type='IMAGE',
        )

    @override_settings(CLOUDFLARE_WEBHOOK_SECRET=TEST_SECRET)
    @patch("ingestion.views.compute_photo_phash.apply_async")
    @patch("ingestion.views.generate_photo_web_derivative.apply_async")
    @patch(HEAD_PATCH, return_value=1024)
    def test_lost_transition_does_not_enqueue_derivative(
        self, _mock_head, mock_delay, mock_phash
    ):
        """Another worker already moved the row out of PENDING."""
        self.asset.status = 'PROCESSING'
        self.asset.save(update_fields=['status'])
        payload = _make_payload(r2_object_key=self.asset.r2_object_key)
        res = _post_webhook(self.client, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], 'processing')
        mock_delay.assert_not_called()
        mock_phash.assert_not_called()
