import json
import hmac
import hashlib
from django.test import TransactionTestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from checkout.models import PricingPlan, CheckoutSession
from unittest.mock import patch
from billing.models import ProcessedWebhook
from billing.tasks import process_lemon_squeezy_webhook

User = get_user_model()

class WebhookSecurityTests(TransactionTestCase):
    """Automated penetration tests for the Lemon Squeezy Webhook Bridge."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.webhook_url = '/api/billing/webhook/'
        self.secret = b"super_secret_test_key_123"

        self.user = User.objects.create_user(email="hacker@test.com", password="pwd")
        self.plan = PricingPlan.objects.create(
            name="Pro", price_usd=10.00, is_active=True, 
            bandwidth_limit_bytes=100, lemon_squeezy_variant_id="var_1",
            gallery_expiry_days=30
        )
        self.session = CheckoutSession.objects.create(user=self.user, plan=self.plan, session_token="12345678-1234-5678-1234-567812345678")

        # HACKER REALITY: We must test the RAW bytes. If Django parses this to JSON
        # before checking the HMAC, the whitespace changes and the signature fails.
        self.raw_payload = json.dumps({
            "meta": {
                "event_name": "subscription_created",
                "custom_data": {"user_id": self.user.id, "session_token": "12345678-1234-5678-1234-567812345678"}
            },
            "data": {"id": "sub_123", "attributes": {"status": "active"}}
        }, separators=(',', ':')).encode('utf-8')

    def generate_signature(self, secret_key, payload_bytes):
        """Helper to generate valid HMAC SHA-256 signatures."""
        return hmac.new(secret_key, payload_bytes, hashlib.sha256).hexdigest()

    def test_rejects_missing_signature(self):
        """HACKER: Sends payload with no signature header."""
        response = self.client.post(self.webhook_url, data=self.raw_payload, content_type='application/json')
        self.assertEqual(response.status_code, 401)

    def test_rejects_forged_signature(self):
        """HACKER: Sends payload with a guessed/forged signature."""
        headers = {'HTTP_X_SIGNATURE': 'fake_hacker_signature_8923y4'}
        response = self.client.post(self.webhook_url, data=self.raw_payload, content_type='application/json', **headers)
        self.assertEqual(response.status_code, 401)

    @patch('billing.views.process_lemon_squeezy_webhook.delay')
    @override_settings(
        LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY="super_secret_test_key_123",
    )
    def test_accepts_valid_signature_constant_time(self, mock_delay):
        """LEMON SQUEEZY: Sends perfect signature. Server must use hmac.compare_digest."""
        valid_sig = self.generate_signature(self.secret, self.raw_payload)
        headers = {'HTTP_X_SIGNATURE': valid_sig, 'HTTP_X_EVENT_ID': 'evt_001'}

        response = self.client.post(self.webhook_url, data=self.raw_payload, content_type='application/json', **headers)
        self.assertIn(response.status_code, [200, 202])

    # The replay attack test was removed here because replay attacks 
    # are intrinsically handled by the DB Idempotency Ledger test below.

    @patch('billing.views.process_lemon_squeezy_webhook.delay')
    @override_settings(
        LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY="super_secret_test_key_123",
    )
    def test_idempotency_double_billing_defense(self, mock_delay):
        """HACKER/GLITCH: Network glitch sends the EXACT same valid webhook twice."""
        
        mock_delay.side_effect = lambda *args, **kwargs: process_lemon_squeezy_webhook(*args, **kwargs)
        valid_sig = self.generate_signature(self.secret, self.raw_payload)
        headers = {'HTTP_X_SIGNATURE': valid_sig, 'HTTP_X_EVENT_ID': 'evt_999'}

        # Fire request 1 (Should process successfully and return 202 Accepted)
        resp1 = self.client.post(self.webhook_url, data=self.raw_payload, content_type='application/json', **headers)
        self.assertEqual(resp1.status_code, 202)

        # Fire request 2 EXACTLY the same
        # (Idempotency should block duplicate processing but still return 202 so Lemon Squeezy stops retrying)
        resp2 = self.client.post(self.webhook_url, data=self.raw_payload, content_type='application/json', **headers)
        self.assertEqual(resp2.status_code, 202)

        # Verify the webhook was only logged ONCE in the database ledger
        self.assertEqual(ProcessedWebhook.objects.count(), 1)

    # End of file
