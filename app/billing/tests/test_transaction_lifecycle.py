"""
Enterprise End-to-End Billing & Checkout Tests

TRANSACTION LIFECYCLE COVERED:
  1. Free user → Checkout → Lemon Squeezy webhook → PRO upgrade → Storage sync
  2. PRO user → Cancellation webhook → FREE downgrade → Storage cap enforced
  3. Payment failure → Grace period → No immediate downgrade
  4. Double-billing defense (idempotency)
  5. Unpaid subscription exploit
  6. DLQ failsafe on DB crash
  7. Cache lock release verification
  8. Empty webhook secret bypass
"""
import json
import hmac
import hashlib
from unittest.mock import patch
from rest_framework.test import APITransactionTestCase, APIClient

from django.test import TransactionTestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.cache import cache

from billing.models import (
    Subscription, ProcessedWebhook, BillingAuditLog, DeadLetterQueue
)
from billing.tasks import process_lemon_squeezy_webhook
from checkout.models import PricingPlan, CheckoutSession

User = get_user_model()

# Realistic Lemon Squeezy webhook payload factory
def _make_ls_payload(
    event_name,
    user_id,
    session_token,
    sub_id='sub_100',
    status='active',
    variant_id='var_pro_50gb',
):
    return {
        'meta': {
            'event_name': event_name,
            'custom_data': {
                'user_id': str(user_id),
                'session_token': str(session_token),
            }
        },
        'data': {
            'id': sub_id,
            'attributes': {
                'status': status,
                'variant_id': variant_id,
            }
        }
    }


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY='test-billing-secret-primary',
)
class FullTransactionLifecycleTests(TransactionTestCase):
    """
    Tests the complete financial lifecycle:
    FREE → Checkout → Webhook → PRO → Cancel → FREE
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email='photographer@studio.com', password='SecurePass123!'
        )
        self.plan = PricingPlan.objects.create(
            name='Pro 50GB',
            lemon_squeezy_variant_id='var_pro_50gb',
            price_usd=19.99,
            bandwidth_limit_bytes=50 * 1024 * 1024 * 1024,  # 50GB
            gallery_expiry_days=365,
            is_active=True,
        )
        self.session = CheckoutSession.objects.create(
            user=self.user, plan=self.plan
        )
        self.sub = Subscription.objects.get(user=self.user)

    # ─────────────────────────────────────────
    # 1. HAPPY PATH: Full Upgrade Lifecycle
    # ─────────────────────────────────────────

    def test_subscription_created_webhook_upgrades_to_pro(self):
        """
        LIFECYCLE: Lemon Squeezy fires subscription_created after payment.
        User MUST transition to PRO with correct storage limit.
        """
        payload = _make_ls_payload(
            'subscription_created',
            self.user.id,
            self.session.session_token,
        )
        result = process_lemon_squeezy_webhook(payload, 'evt_upgrade_001')

        self.assertIn('processed successfully', result)

        # Verify billing vault
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.is_pro)
        self.assertEqual(self.sub.storage_limit_bytes, 50 * 1024 * 1024 * 1024)
        self.assertEqual(self.sub.lemon_squeezy_subscription_id, 'sub_100')

        # Verify core user sync
        self.user.refresh_from_db()
        self.assertEqual(self.user.subscription_tier, 'PRO')
        self.assertEqual(self.user.storage_limit_gb, 50)

        # Verify checkout session closed
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'COMPLETED')

        # Verify audit trail
        log = BillingAuditLog.objects.get(webhook_event_id='evt_upgrade_001')
        self.assertEqual(log.old_state, 'FREE')
        self.assertEqual(log.new_state, 'PRO')

    def test_subscription_cancelled_downgrades_to_free(self):
        """
        LIFECYCLE: After PRO user cancels, webhook fires subscription_cancelled.
        User MUST revert to FREE tier with 1GB cap.
        """
        # First, make user PRO
        self.sub.is_pro = True
        self.sub.lemon_squeezy_subscription_id = 'sub_100'
        self.sub.storage_limit_bytes = 50 * 1024 * 1024 * 1024
        self.sub.save()

        payload = _make_ls_payload(
            'subscription_cancelled', self.user.id, self.session.session_token,
            status='cancelled',
        )
        result = process_lemon_squeezy_webhook(payload, 'evt_cancel_001')

        self.assertIn('downgraded', result)

        self.sub.refresh_from_db()
        self.assertFalse(self.sub.is_pro)
        self.assertEqual(self.sub.storage_limit_bytes, 1073741824)  # 1GB

        self.user.refresh_from_db()
        self.assertEqual(self.user.subscription_tier, 'FREE')

        log = BillingAuditLog.objects.get(webhook_event_id='evt_cancel_001')
        self.assertEqual(log.old_state, 'PRO')
        self.assertEqual(log.new_state, 'FREE')

    # ─────────────────────────────────────────
    # 2. SECURITY: Double-Billing & Replay
    # ─────────────────────────────────────────

    def test_duplicate_webhook_blocked_by_idempotency_ledger(self):
        """
        SECURITY: Network glitch sends same webhook twice.
        Second invocation MUST be silently ignored. Storage MUST NOT double.
        """
        payload = _make_ls_payload(
            'subscription_created', self.user.id, self.session.session_token,
        )

        result1 = process_lemon_squeezy_webhook(payload, 'evt_dupe_001')
        result2 = process_lemon_squeezy_webhook(payload, 'evt_dupe_001')

        self.assertIn('processed successfully', result1)
        self.assertIn('Duplicate', result2)
        self.assertEqual(ProcessedWebhook.objects.count(), 1)

    def test_replayed_payload_with_new_header_event_id_is_deduplicated(self):
        """
        SECURITY: Replaying the exact same signed payload with a forged new X-Event-ID
        must not bypass idempotency.
        """
        payload = _make_ls_payload(
            'subscription_created', self.user.id, self.session.session_token,
        )
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
        ).hexdigest()

        result1 = process_lemon_squeezy_webhook(payload, 'evt_replay_001', payload_hash)
        result2 = process_lemon_squeezy_webhook(payload, 'evt_replay_999', payload_hash)

        self.assertIn('processed successfully', result1)
        self.assertIn('Duplicate', result2)
        self.assertEqual(ProcessedWebhook.objects.count(), 1)

    # ─────────────────────────────────────────
    # 3. SECURITY: Unpaid Subscription Exploit
    # ─────────────────────────────────────────

    def test_unpaid_subscription_does_not_grant_pro(self):
        """
        HACKER: Starts checkout, generates webhook with status='unpaid'.
        System MUST reject — subscription is NOT entitled.
        """
        payload = _make_ls_payload(
            'subscription_created', self.user.id, self.session.session_token,
            status='unpaid',
        )
        result = process_lemon_squeezy_webhook(payload, 'evt_unpaid_001')

        self.assertIn('unpaid', result.lower())

        self.sub.refresh_from_db()
        self.assertFalse(self.sub.is_pro)  # Must remain FREE

    def test_subscription_created_rejects_user_session_mismatch(self):
        """
        SECURITY: custom_data user_id and session_token must anchor the same
        checkout session owner. A mismatched token must not upgrade another user.
        """
        attacker = User.objects.create_user(
            email="attacker@example.com",
            password="SecurePass123!",
        )
        attacker_session = CheckoutSession.objects.create(
            user=attacker,
            plan=self.plan,
        )

        payload = _make_ls_payload(
            'subscription_created',
            self.user.id,
            attacker_session.session_token,
        )
        process_lemon_squeezy_webhook(payload, 'evt_mismatch_001')

        self.sub.refresh_from_db()
        attacker.subscription.refresh_from_db()
        self.assertFalse(self.sub.is_pro)
        self.assertFalse(attacker.subscription.is_pro)
        self.assertTrue(
            DeadLetterQueue.objects.filter(event_id='evt_mismatch_001').exists()
        )

    # ─────────────────────────────────────────
    # 4. SECURITY: Payment Failure Grace Period
    # ─────────────────────────────────────────

    def test_payment_failure_logs_but_does_not_downgrade(self):
        """
        BUSINESS LOGIC: Payment fails but Lemon Squeezy grants grace period.
        System MUST NOT immediately downgrade — just log the warning.
        """
        self.sub.is_pro = True
        self.sub.lemon_squeezy_subscription_id = 'sub_100'
        self.sub.save()

        payload = _make_ls_payload(
            'subscription_payment_failed', self.user.id, self.session.session_token,
        )
        result = process_lemon_squeezy_webhook(payload, 'evt_fail_001')

        self.assertIn('Payment failure logged', result)

        # PRO status must be preserved during grace period
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.is_pro)

    # ─────────────────────────────────────────
    # 5. FAILSAFE: DLQ on User Not Found
    # ─────────────────────────────────────────

    def test_missing_user_routes_to_dead_letter_queue(self):
        """
        FAILSAFE: Webhook references a deleted/non-existent user.
        Payload MUST be preserved in DLQ for manual recovery.
        """
        payload = _make_ls_payload(
            'subscription_created', 'nonexistent-uuid-12345',
            self.session.session_token,
        )
        # This will hit a User.DoesNotExist or CheckoutSession lookup failure
        process_lemon_squeezy_webhook(payload, 'evt_orphan_001')

        dlq = DeadLetterQueue.objects.filter(event_id='evt_orphan_001')
        self.assertTrue(dlq.exists(), "Payload was lost — DLQ did not capture it!")

    # ─────────────────────────────────────────
    # 6. SECURITY: Irrelevant Events Ignored
    # ─────────────────────────────────────────

    def test_non_subscription_events_silently_ignored(self):
        """
        CONTRACT: order_created, invoice_paid, etc. must not trigger billing logic.
        """
        payload = _make_ls_payload(
            'order_created', self.user.id, self.session.session_token,
        )
        result = process_lemon_squeezy_webhook(payload, 'evt_order_001')

        self.assertIn('Ignored', result)
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.is_pro)


@override_settings(
    LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY='test-billing-secret-primary',
)
class WebhookReceiverHTTPTests(TransactionTestCase):
    """
    Tests the HTTP layer of the billing webhook endpoint.
    Verifies HMAC, Content-Length guard, and empty secret defense.
    """

    def setUp(self):
        cache.clear()
        self.url = '/api/billing/webhook/'
        self.secret = b'test-billing-secret-primary'
        self.user = User.objects.create_user(email='http@test.com', password='pass')
        self.plan = PricingPlan.objects.create(
            name='Pro', lemon_squeezy_variant_id='var_1',
            price_usd=10, bandwidth_limit_bytes=100,
            gallery_expiry_days=30, is_active=True,
        )
        self.session = CheckoutSession.objects.create(user=self.user, plan=self.plan)
        self.payload = json.dumps({
            'meta': {'event_name': 'subscription_created',
                     'custom_data': {'user_id': str(self.user.id),
                                     'session_token': str(self.session.session_token)}},
            'data': {'id': 'sub_1', 'attributes': {'status': 'active'}}
        }, separators=(',', ':')).encode()

    def _sign(self, payload_bytes, secret=None):
        s = secret or self.secret
        return hmac.new(s, payload_bytes, hashlib.sha256).hexdigest()

    @patch('billing.views.process_lemon_squeezy_webhook.delay')
    def test_valid_webhook_returns_202(self, mock_delay):
        sig = self._sign(self.payload)
        resp = self.client.post(
            self.url, data=self.payload, content_type='application/json',
            HTTP_X_SIGNATURE=sig, HTTP_X_EVENT_ID='evt_http_001',
        )
        self.assertEqual(resp.status_code, 202)
        self.assertTrue(mock_delay.called)

    def test_missing_signature_returns_401(self):
        resp = self.client.post(
            self.url, data=self.payload, content_type='application/json',
            HTTP_X_EVENT_ID='evt_http_002',
        )
        self.assertEqual(resp.status_code, 401)

    def test_forged_signature_returns_401(self):
        resp = self.client.post(
            self.url, data=self.payload, content_type='application/json',
            HTTP_X_SIGNATURE='forged_abc123', HTTP_X_EVENT_ID='evt_http_003',
        )
        self.assertEqual(resp.status_code, 401)

    def test_rejected_webhook_response_body_is_generic(self):
        resp = self.client.post(
            self.url, data=self.payload, content_type='application/json',
            HTTP_X_SIGNATURE='forged_abc123', HTTP_X_EVENT_ID='evt_http_generic',
        )

        rendered = resp.content.decode("utf-8").lower()
        self.assertEqual(resp.status_code, 401)
        self.assertNotIn("signature", rendered)
        self.assertNotIn("headers", rendered)
        self.assertNotIn("json", rendered)

    def test_malformed_webhook_response_body_is_generic(self):
        malformed_payload = b'{"meta":'
        sig = self._sign(malformed_payload)
        resp = self.client.post(
            self.url, data=malformed_payload, content_type='application/json',
            HTTP_X_SIGNATURE=sig, HTTP_X_EVENT_ID='evt_http_malformed',
        )

        rendered = resp.content.decode("utf-8").lower()
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn("signature", rendered)
        self.assertNotIn("headers", rendered)
        self.assertNotIn("json", rendered)

    @override_settings(
        LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY='',
        LEMON_SQUEEZY_WEBHOOK_SECRET_SECONDARY='',
    )
    def test_empty_secrets_returns_500_not_bypass(self):
        """
        SECURITY: If both webhook secrets are empty, the server MUST refuse
        all webhooks (500), not silently accept via empty-HMAC match.
        """
        sig = self._sign(self.payload, secret=b'')
        resp = self.client.post(
            self.url, data=self.payload, content_type='application/json',
            HTTP_X_SIGNATURE=sig, HTTP_X_EVENT_ID='evt_http_004',
        )
        self.assertEqual(resp.status_code, 500)

    def test_oversized_payload_rejected(self):
        """
        OOM DEFENSE: A 2MB payload must be rejected before parsing.
        """
        huge = b'x' * (2 * 1024 * 1024)
        resp = self.client.post(
            self.url, data=huge, content_type='application/json',
            HTTP_X_SIGNATURE='irrelevant', HTTP_X_EVENT_ID='evt_oom',
            CONTENT_LENGTH=str(len(huge)),
        )
        self.assertEqual(resp.status_code, 400)


@override_settings(LEMON_SQUEEZY_API_KEY='test-ls-api-key', LEMON_SQUEEZY_STORE_ID='1')
class CheckoutCacheLockTests(APITransactionTestCase):
    """
    Verifies the cache lock in GenerateCheckoutLinkView is properly released.
    Uses APITransactionTestCase for proper cache + DB isolation.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(email='lock@test.com', password='pass')
        self.plan = PricingPlan.objects.create(
            name='Pro', lemon_squeezy_variant_id='var_lock_1',
            price_usd=10, bandwidth_limit_bytes=100,
            gallery_expiry_days=30, is_active=True,
        )
        self.url = '/api/checkout/generate/'
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.user)

    @patch('requests.post')
    def test_cache_lock_released_after_success(self, mock_post):
        """
        REGRESSION: The finally block previously had `pass` — lock was never released.
        After a successful checkout, a second request MUST NOT get 409.
        """
        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {
            'data': {'attributes': {'url': 'https://checkout.test/ok'}}
        }

        resp1 = self.api_client.post(self.url, {'plan_id': self.plan.id})
        self.assertEqual(resp1.status_code, 200)

        # Second request — should NOT be 409 if lock was properly released
        resp2 = self.api_client.post(self.url, {'plan_id': self.plan.id})
        self.assertNotEqual(
            resp2.status_code, 409,
            "FATAL: Cache lock was NOT released — user is permanently locked out!"
        )

    @patch('requests.post')
    def test_cache_lock_released_after_failure(self, mock_post):
        """
        REGRESSION: Even if Lemon Squeezy returns 500, the lock MUST be released.
        """
        mock_post.return_value.status_code = 500
        mock_post.return_value.text = 'Internal Server Error'

        resp1 = self.api_client.post(self.url, {'plan_id': self.plan.id})
        self.assertEqual(resp1.status_code, 502)

        # Lock must be released — second attempt should not be 409
        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {
            'data': {'attributes': {'url': 'https://checkout.test/ok'}}
        }
        resp2 = self.api_client.post(self.url, {'plan_id': self.plan.id})
        self.assertNotEqual(resp2.status_code, 409)
