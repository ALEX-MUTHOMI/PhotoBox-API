"""
Enterprise Attack Simulation Suite — Phase 2: Billing Hardening Tests

ATTACK VECTORS COVERED:
  1. Out-of-Order Webhook Delivery (subscription_cancelled before subscription_updated)
  2. Concurrent Checkout Cache Lock (10 simultaneous button mashes on slow 3G)
  3. Atomic Quota Deduction Under Load (5 workers × 1GB vs 2GB limit)

USAGE:
  python manage.py test billing.tests.test_billing_hardening --verbosity=2
"""

import time
from threading import Thread, Barrier, Event
from unittest.mock import patch, MagicMock

from django.test import TransactionTestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITransactionTestCase, APIClient
from rest_framework import status

from billing.models import (
    Subscription, ProcessedWebhook, DeadLetterQueue,
)
from billing.tasks import process_lemon_squeezy_webhook
from checkout.models import PricingPlan, CheckoutSession
from checkout.views import GenerateCheckoutLinkView

User = get_user_model()

# ── Constants ────────────────────────────────────────────────────────────
ONE_GB = 1 * 1024 * 1024 * 1024   # 1,073,741,824 bytes
TWO_GB = 2 * 1024 * 1024 * 1024
FIFTY_GB = 50 * 1024 * 1024 * 1024


# ── Payload Factory ──────────────────────────────────────────────────────
def _make_ls_payload(event_name, user_id, session_token, sub_id='sub_100',
                     sub_status='active', variant_id='var_pro_50gb'):
    """Builds a realistic Lemon Squeezy webhook payload."""
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
                'status': sub_status,
                'variant_id': variant_id,
            }
        }
    }


# ======================================================================
# TEST 1: OUT-OF-ORDER WEBHOOK DELIVERY
# ======================================================================

@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY='test-hardening-secret',
)
class OutOfOrderWebhookTests(TransactionTestCase):
    """
    ATTACK VECTOR: Lemon Squeezy guarantees at-least-once delivery, NOT ordered.

    SCENARIO:
      A subscription_cancelled webhook arrives 50ms BEFORE a delayed
      subscription_updated (renewal) webhook.  The cancellation handler
      clears lemon_squeezy_subscription_id, so the renewal webhook
      cannot find the subscription by that ID.

    EXPECTED RESULT:
      - The renewal is reconciled onto the tenant even if cancellation arrived first.
      - The user returns to PRO with the correct storage limit.
      - No dead-letter entry is emitted for a valid delayed update.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email='ooo-test@photographer.com', password='SecurePass123!'
        )
        self.plan = PricingPlan.objects.create(
            name='Pro 50GB',
            lemon_squeezy_variant_id='var_pro_50gb',
            price_usd=19.99,
            bandwidth_limit_bytes=FIFTY_GB,
            gallery_expiry_days=365,
            is_active=True,
        )
        self.session = CheckoutSession.objects.create(
            user=self.user, plan=self.plan
        )

        # PRE-CONDITION: User is already PRO with an active subscription
        self.sub = Subscription.objects.get(user=self.user)
        self.sub.is_pro = True
        self.sub.lemon_squeezy_subscription_id = 'sub_100'
        self.sub.storage_limit_bytes = FIFTY_GB
        self.sub.save()

        self.user.subscription_tier = 'PRO'
        self.user.storage_limit_gb = 50
        self.user.save(update_fields=['subscription_tier', 'storage_limit_gb'])

    def test_lemon_squeezy_out_of_order_webhooks(self):
        """
        Simulate: subscription_cancelled arrives BEFORE subscription_updated.
        The billing state machine must recover once the delayed active update arrives.
        """
        cancel_payload = _make_ls_payload(
            'subscription_cancelled',
            self.user.id,
            self.session.session_token,
            sub_id='sub_100',
            sub_status='cancelled',
        )
        cancel_result = process_lemon_squeezy_webhook(
            cancel_payload, 'evt_cancel_ooo_01'
        )

        self.assertIn('downgraded', cancel_result)

        self.sub.refresh_from_db()
        self.assertFalse(self.sub.is_pro)
        self.assertEqual(self.sub.storage_limit_bytes, ONE_GB)
        self.assertEqual(self.sub.lemon_squeezy_subscription_id, 'sub_100')

        renew_payload = _make_ls_payload(
            'subscription_updated',
            self.user.id,
            self.session.session_token,
            sub_id='sub_100',
            sub_status='active',
            variant_id='var_pro_50gb',
        )
        renew_result = process_lemon_squeezy_webhook(
            renew_payload, 'evt_renew_ooo_02'
        )

        self.assertIn('processed successfully', renew_result)
        self.assertFalse(
            DeadLetterQueue.objects.filter(event_id='evt_renew_ooo_02').exists(),
            'FATAL: The delayed renewal was dead-lettered instead of reconciled.'
        )
        self.assertEqual(ProcessedWebhook.objects.count(), 2)

        self.sub.refresh_from_db()
        self.assertTrue(self.sub.is_pro)
        self.assertEqual(self.sub.storage_limit_bytes, FIFTY_GB)

        self.user.refresh_from_db()
        self.assertEqual(self.user.subscription_tier, 'PRO')
        self.assertEqual(self.user.storage_limit_gb, 50)

# ======================================================================
# TEST 2: CONCURRENT CHECKOUT CACHE LOCK (10 BUTTON MASHES ON SLOW 3G)
# ======================================================================

@override_settings(
    LEMON_SQUEEZY_API_KEY='test-ls-api-key',
    LEMON_SQUEEZY_STORE_ID='1',
)
class ConcurrentCheckoutLockTests(APITransactionTestCase):
    """
    ATTACK VECTOR: User mashes the 'Upgrade' button 10 times while the
    first request is still in-flight over a slow 3G connection.

    The cache lock (cache.add) must ensure only ONE checkout session is
    created.  The remaining 9 requests MUST receive 409 Conflict.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email='masher@photographer.com', password='SecurePass123!'
        )
        self.plan = PricingPlan.objects.create(
            name='Pro 50GB',
            lemon_squeezy_variant_id='var_cc_50gb',
            price_usd=19.99,
            bandwidth_limit_bytes=FIFTY_GB,
            gallery_expiry_days=365,
            is_active=True,
        )
        self.url = '/api/checkout/generate/'

    @patch.object(GenerateCheckoutLinkView, 'throttle_classes', [])
    def test_concurrent_checkout_cache_lock(self):
        """
        Fire 10 concurrent POST requests.  Exactly ONE gets 200 OK with
        a checkout URL.  The remaining 9 MUST receive 409 Conflict.
        Only ONE CheckoutSession record is created in the database.
        """
        results = [None] * 10

        # Synchronisation primitives:
        #   gateway_entered — set when the lock-holder enters requests.post()
        #   gateway_release — held until all other threads have received 409
        gateway_entered = Event()
        gateway_release = Event()

        def blocking_lemon_squeezy_post(*args, **kwargs):
            """
            Simulates a slow 3G Lemon Squeezy round-trip.
            Blocks the lock-holder inside requests.post() long enough for
            all other threads to hit cache.add() and receive 409.
            """
            gateway_entered.set()
            gateway_release.wait(timeout=15)
            response = MagicMock()
            response.status_code = 201
            response.json.return_value = {
                'data': {
                    'attributes': {
                        'url': 'https://checkout.lemonsqueezy.com/test'
                    }
                }
            }
            response.raise_for_status = MagicMock()
            return response

        # Barrier ensures all 10 threads start simultaneously
        barrier = Barrier(10, timeout=15)

        def fire_request(index):
            client = APIClient()
            client.force_authenticate(user=self.user)
            barrier.wait()  # Synchronise all 10 threads before firing
            resp = client.post(self.url, {'plan_id': self.plan.id}, format='json')
            results[index] = resp.status_code

        with patch('requests.post', side_effect=blocking_lemon_squeezy_post):
            threads = [
                Thread(target=fire_request, args=(i,)) for i in range(10)
            ]
            for t in threads:
                t.start()

            # Wait for the lock-holder to enter the slow gateway
            gateway_entered.wait(timeout=15)

            # Give remaining 9 threads time to hit cache.add() and return 409
            time.sleep(1)

            # Release the lock-holder so it can complete
            gateway_release.set()

            for t in threads:
                t.join(timeout=20)

        # ── ASSERTIONS ──────────────────────────────────────────────────

        # Ensure all threads completed
        self.assertTrue(
            all(r is not None for r in results),
            f"Some requests did not complete: {results}"
        )

        successes = results.count(status.HTTP_200_OK)
        conflicts = results.count(status.HTTP_409_CONFLICT)

        self.assertEqual(
            successes, 1,
            f"Exactly ONE checkout should succeed. Got {successes}. "
            f"Full results: {results}"
        )
        self.assertEqual(
            conflicts, 9,
            f"Remaining 9 should get 409 Conflict. Got {conflicts}. "
            f"Full results: {results}"
        )

        # Only ONE checkout session was created in the database
        session_count = CheckoutSession.objects.filter(user=self.user).count()
        self.assertEqual(
            session_count, 1,
            f"FATAL: {session_count} checkout sessions created — "
            f"double-billing is possible!"
        )


# ======================================================================
# TEST 3: ATOMIC QUOTA DEDUCTION UNDER CONCURRENT LOAD
# ======================================================================

@override_settings(TESTING=True)
class AtomicQuotaDeductionTests(TransactionTestCase):
    """
    ATTACK VECTOR: 5 simultaneous Celery workers try to deduct 1GB
    from a 2GB storage limit at the exact same millisecond.

    EXPECTED RESULT:
      - Exactly 2 succeed (0GB → 1GB → 2GB)
      - Exactly 3 fail with 402 Payment Required
      - Final DB state: storage_used_bytes == 2GB (zero remaining)
      - SQL CHECK constraint prevents negative values at all times
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email='quota-race@photographer.com', password='SecurePass123!'
        )

        # Set the subscription to 2GB limit, 0 bytes used
        self.sub = Subscription.objects.get(user=self.user)
        self.sub.storage_limit_bytes = TWO_GB
        self.sub.storage_used_bytes = 0
        self.sub.save()

        self.upload_url = '/api/billing/gallery/upload/'

    def test_quota_ledger_atomic_deduction_under_load(self):
        """
        5 concurrent workers each try to deduct 1GB from a 2GB quota.
        select_for_update() MUST serialise the transactions so that
        exactly 2 succeed and the ledger remains mathematically consistent.
        """
        results = [None] * 5

        # Barrier ensures all 5 threads fire simultaneously
        barrier = Barrier(5, timeout=15)

        def fire_upload(index):
            client = APIClient()
            client.force_authenticate(user=self.user)
            barrier.wait()  # Synchronise all 5 threads
            resp = client.post(
                self.upload_url,
                data={'file_size': ONE_GB},
                format='json',
            )
            results[index] = resp.status_code

        threads = [Thread(target=fire_upload, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # ── ASSERTIONS ──────────────────────────────────────────────────

        # Ensure all threads completed
        self.assertTrue(
            all(r is not None for r in results),
            f"Some requests did not complete: {results}"
        )

        # No unexpected error responses (only 201 or 402 are valid)
        unexpected = [
            r for r in results
            if r not in (status.HTTP_201_CREATED, status.HTTP_402_PAYMENT_REQUIRED)
        ]
        self.assertEqual(
            len(unexpected), 0,
            f"Unexpected status codes detected: {unexpected}. "
            f"Full results: {results}"
        )

        successes = results.count(status.HTTP_201_CREATED)
        rejections = results.count(status.HTTP_402_PAYMENT_REQUIRED)

        self.assertEqual(
            successes, 2,
            f"Exactly 2 uploads should fit in 2GB. Got {successes}. "
            f"Full results: {results}"
        )
        self.assertEqual(
            rejections, 3,
            f"Exactly 3 should be rejected at quota limit. Got {rejections}. "
            f"Full results: {results}"
        )

        # ── MATHEMATICAL PROOF: Final Ledger State ──────────────────────

        self.sub.refresh_from_db()

        self.assertEqual(
            self.sub.storage_used_bytes, TWO_GB,
            f"Storage used should be exactly 2GB ({TWO_GB} bytes). "
            f"Got {self.sub.storage_used_bytes} bytes."
        )

        remaining = self.sub.storage_limit_bytes - self.sub.storage_used_bytes
        self.assertEqual(
            remaining, 0,
            f"Zero bytes remaining expected. Got {remaining}."
        )

        # NEGATIVE OVERFLOW CHECK:
        # The SQL CHECK constraint (prevent_negative_storage_used) enforces
        # this at the database level, but we verify the application logic
        # also produces a mathematically valid result.
        self.assertGreaterEqual(
            self.sub.storage_used_bytes, 0,
            "CRITICAL: Negative storage_used_bytes detected — "
            "integer overflow exploit succeeded!"
        )
        self.assertLessEqual(
            self.sub.storage_used_bytes,
            self.sub.storage_limit_bytes,
            "CRITICAL: storage_used_bytes exceeds storage_limit_bytes — "
            "quota enforcement bypass detected!"
        )

