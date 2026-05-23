from unittest.mock import patch

from django.test import SimpleTestCase

from billing.tasks import safe_create_dlq


class BillingWebhookLogRedactionTests(SimpleTestCase):
    def test_dlq_failure_does_not_log_raw_payload_pii_or_tokens(self):
        payload = {
            "meta": {
                "custom_data": {
                    "user_id": "user-123",
                    "session_token": "session-token-secret",
                }
            },
            "data": {
                "attributes": {
                    "email": "client@example.com",
                    "status": "active",
                }
            },
        }

        with patch(
            "billing.tasks.DeadLetterQueue.objects.create",
            side_effect=RuntimeError("db unavailable"),
        ):
            with self.assertLogs("billing.tasks", level="CRITICAL") as captured:
                safe_create_dlq("evt_redact", payload, "logic failure")

        rendered = "\n".join(captured.output)
        self.assertIn("evt_redact", rendered)
        self.assertNotIn("client@example.com", rendered)
        self.assertNotIn("session-token-secret", rendered)

