from django.test import SimpleTestCase

from core.security import scrub_email, scrub_ip, scrub_value


class SecurityHelperTests(SimpleTestCase):
    def test_scrub_email_returns_stable_hash_not_plaintext(self):
        redacted = scrub_email("Photographer@example.com")
        self.assertTrue(redacted.startswith("email_"))
        self.assertNotIn("Photographer@example.com", redacted)

    def test_scrub_ip_returns_stable_hash_not_plaintext(self):
        redacted = scrub_ip("203.0.113.10")
        self.assertTrue(redacted.startswith("ip_"))
        self.assertNotIn("203.0.113.10", redacted)

    def test_scrub_value_redacts_sensitive_keys_and_inline_tokens(self):
        payload = {
            "headers": {
                "Authorization": "Bearer secret-token-value",
                "X-Test": "ok",
            },
            "email": "victim@example.com",
            "message": "Login for victim@example.com with Bearer abc123",
        }

        scrubbed = scrub_value(payload)

        self.assertEqual(scrubbed["headers"]["Authorization"], "[REDACTED]")
        self.assertEqual(scrubbed["email"], "[REDACTED]")
        self.assertNotIn("victim@example.com", scrubbed["message"])
        self.assertNotIn("abc123", scrubbed["message"])
