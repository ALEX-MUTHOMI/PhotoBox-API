from django.test import TestCase
from django.contrib.auth import get_user_model
from billing.models import Subscription, ProcessedWebhook, DeadLetterQueue

User = get_user_model()

class BillingModelPhysicsTests(TestCase):
    """Tests the fundamental database structure, signals, and default states."""

    def setUp(self):
        self.user = User.objects.create_user(email="test@photobox.com", password="password123")

    def test_subscription_auto_creation_signal(self):
        """PHYSICS: Ensure the User creation signal builds the Quota Vault automatically."""
        self.assertTrue(hasattr(self.user, 'subscription'))
        self.assertIsInstance(self.user.subscription, Subscription)

    def test_free_tier_defaults(self):
        """PHYSICS: Ensure the math is strictly set to the 1GB free tier upon creation."""
        sub = self.user.subscription
        self.assertFalse(sub.is_pro)
        self.assertEqual(sub.storage_limit_bytes, 1073741824) # Exactly 1GB in bytes
        self.assertEqual(sub.storage_used_bytes, 0)

    def test_processed_webhook_creation(self):
        """PHYSICS: Ensure the webhook ledger correctly records event IDs."""
        webhook = ProcessedWebhook.objects.create(event_id="evt_ledger_test_123")
        self.assertEqual(webhook.event_id, "evt_ledger_test_123")
        self.assertIsNotNone(webhook.processed_at)

    def test_dead_letter_queue_creation(self):
        """PHYSICS: Ensure the Failsafe DLQ properly accepts JSON payloads."""
        dlq = DeadLetterQueue.objects.create(
            event_id="evt_fail_999",
            payload={"error": "database_locked", "user_id": 1},
            error_message="Max retries exceeded."
        )
        self.assertEqual(dlq.event_id, "evt_fail_999")
        self.assertEqual(dlq.payload["error"], "database_locked")
        self.assertIsNotNone(dlq.created_at)
