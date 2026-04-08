"""
Enterprise Tests for the Billing & Quota Vault API.
"""
import json
import hmac
import hashlib
import time
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from rest_framework import status

from billing.models import Subscription, ProcessedWebhook, BillingAuditLog

User = get_user_model()

# ==========================================
# 1. WEBHOOK CRYPTOGRAPHY & GATEWAY TESTS
# ==========================================
class WebhookSecurityTests(TransactionTestCase):
    def setUp(self):
        self.client = Client()
        self.webhook_url = '/api/billing/webhook/' # Adjust if your routing differs
        self.secret = b"super_secret_test_key_123"

        self.raw_payload = json.dumps({
            "meta": {
                "event_name": "subscription_created",
                "custom_data": {"user_id": 1}
            },
            "data": {"id": "sub_123", "attributes": {"status": "active"}}
        }, separators=(',', ':')).encode('utf-8')

    def generate_signature(self, secret_key, payload_bytes):
        return hmac.new(secret_key, payload_bytes, hashlib.sha256).hexdigest()

    def test_rejects_missing_signature(self):
        response = self.client.post(self.webhook_url, data=self.raw_payload, content_type='application/json')
        self.assertEqual(response.status_code, 401)

    def test_rejects_forged_signature(self):
        headers = {'HTTP_X_SIGNATURE': 'fake_hacker_signature_8923y4'}
        response = self.client.post(self.webhook_url, data=self.raw_payload, content_type='application/json', **headers)
        self.assertEqual(response.status_code, 401)

    @override_settings(LEMON_SQUEEZY_WEBHOOK_SECRET="super_secret_test_key_123")
    def test_accepts_valid_signature_constant_time(self):
        valid_sig = self.generate_signature(self.secret, self.raw_payload)
        headers = {'HTTP_X_SIGNATURE': valid_sig, 'HTTP_X_EVENT_ID': 'evt_001'}
        response = self.client.post(self.webhook_url, data=self.raw_payload, content_type='application/json', **headers)
        self.assertIn(response.status_code, [200, 202])

    @override_settings(LEMON_SQUEEZY_WEBHOOK_SECRET="super_secret_test_key_123")
    def test_rejects_replay_attack(self):
        old_timestamp = int(time.time()) - 600 # 10 minutes ago
        stale_payload = json.dumps({"meta": {"created_at": old_timestamp}}, separators=(',', ':')).encode('utf-8')
        valid_sig = self.generate_signature(self.secret, stale_payload)

        headers = {'HTTP_X_SIGNATURE': valid_sig, 'HTTP_X_EVENT_ID': 'evt_002'}
        response = self.client.post(self.webhook_url, data=stale_payload, content_type='application/json', **headers)
        self.assertEqual(response.status_code, 401)

    @override_settings(LEMON_SQUEEZY_WEBHOOK_SECRET="super_secret_test_key_123")
    def test_idempotency_double_billing_defense(self):
        valid_sig = self.generate_signature(self.secret, self.raw_payload)
        headers = {'HTTP_X_SIGNATURE': valid_sig, 'HTTP_X_EVENT_ID': 'evt_999'}

        resp1 = self.client.post(self.webhook_url, data=self.raw_payload, content_type='application/json', **headers)
        self.assertIn(resp1.status_code, [200, 202])

        resp2 = self.client.post(self.webhook_url, data=self.raw_payload, content_type='application/json', **headers)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(ProcessedWebhook.objects.filter(event_id='evt_999').count(), 1)

    @override_settings(LEMON_SQUEEZY_WEBHOOK_SECRET="super_secret_test_key_123")
    @patch('billing.views.process_lemon_squeezy_webhook.delay')
    def test_webhook_accepts_valid_signature_and_queues_task(self, mock_celery_task):
        """SCALE: Ensure valid payments are handed to Celery, not processed synchronously."""
        payload = {
            "meta": {
                "event_name": "subscription_created",
                "created_at": int(time.time()),
                "custom_data": {"user_id": 1}
            }
        }
        raw_payload = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        valid_sig = self.generate_signature(self.secret, raw_payload)
        headers = {'HTTP_X_SIGNATURE': valid_sig, 'HTTP_X_EVENT_ID': 'real-uuid-123'}

        res = self.client.post(self.webhook_url, data=raw_payload, content_type='application/json', **headers)
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        mock_celery_task.assert_called_once_with(payload, 'real-uuid-123')


# ==========================================
# 2. THE QUOTA VAULT (Physics & Race Conditions)
# ==========================================
class SubscriptionQuotaTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="photog@test.com", password="password")
        self.subscription = self.user.subscription
        self.subscription.storage_used_bytes = 1000000000 # ~953MB used
        self.subscription.save()

        self.client = Client()
        self.client.force_login(self.user)
        # Adjust URL to match your routing if needed, typically '/api/billing/gallery/upload/'
        self.upload_url = '/api/billing/gallery/upload/'

    def test_race_condition_upload_defense(self):
        upload_size = 50000000 # 50MB

        def fire_request(_):
            return self.client.post(self.upload_url, data={'file_size': upload_size}, content_type='application/json')

        with ThreadPoolExecutor(max_workers=10) as executor:
            responses = list(executor.map(fire_request, range(10)))

        successes = sum(1 for r in responses if r.status_code == status.HTTP_201_CREATED)
        blocks = sum(1 for r in responses if r.status_code == status.HTTP_402_PAYMENT_REQUIRED)

        self.assertEqual(successes, 1)
        self.assertEqual(blocks, 9)

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.storage_used_bytes, 1050000000)


# ==========================================
# 3. INSIDER THREAT & MODEL PHYSICS
# ==========================================
class BillingModelPhysicsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="test@photobox.com", password="password123")

    def test_subscription_auto_creation_signal(self):
        self.assertTrue(hasattr(self.user, 'subscription'))
        self.assertIsInstance(self.user.subscription, Subscription)

    def test_free_tier_defaults(self):
        sub = self.user.subscription
        self.assertFalse(sub.is_pro)
        self.assertEqual(sub.storage_limit_bytes, 1073741824)
        self.assertEqual(sub.storage_used_bytes, 0)

    def test_audit_log_absolute_immutability(self):
        log = BillingAuditLog.objects.create(user=self.user, old_state="FREE", new_state="PRO")
        self.assertEqual(BillingAuditLog.objects.count(), 1)

        log.new_state = "HACKED"
        with self.assertRaises(PermissionDenied):
            log.save()

        with self.assertRaises(PermissionDenied):
            BillingAuditLog.objects.all().update(new_state="HACKED")

        with self.assertRaises(PermissionDenied):
            log.delete()

        with self.assertRaises(PermissionDenied):
            BillingAuditLog.objects.all().delete()

        log.refresh_from_db()
        self.assertEqual(log.new_state, "PRO")


class InsiderThreatSecurityTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="client@test.com", password="password")
        self.subscription = self.user.subscription

    def test_immutable_audit_log_creation(self):
        self.subscription.is_pro = True
        self.subscription.save()

        log = BillingAuditLog.objects.filter(user=self.user).last()
        self.assertIsNotNone(log)
        self.assertEqual(log.old_state, "FREE")
        self.assertEqual(log.new_state, "PRO")

    def test_audit_logs_block_all_deletion_vectors(self):
        self.subscription.is_pro = True
        self.subscription.save()
        log = BillingAuditLog.objects.first()

        with self.assertRaises(Exception):
            log.delete()

        with self.assertRaises(Exception):
            BillingAuditLog.objects.all().delete()

        self.assertEqual(BillingAuditLog.objects.count(), 1)
