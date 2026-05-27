"""
Automated Threat Model Security Test Suite for PhotoBox-API.
"""
import pytest
from unittest.mock import patch, MagicMock
from core.turnstile import verify_turnstile_token
from core.security import verify_webhook_signature, verify_webhook_timestamp


class TestTurnstileBotDefense:
    def test_valid_turnstile_token_passes(self):
        with patch("core.turnstile.httpx.Client") as mock_client:
            mock_res = MagicMock()
            mock_res.json.return_value = {"success": True}
            mock_client.return_value.__enter__.return_value.post.return_value = mock_res
            with patch("core.turnstile.settings") as mock_settings:
                mock_settings.CLOUDFLARE_TURNSTILE_SECRET_KEY = "test-secret"
                assert verify_turnstile_token("valid-cf-token") is True

    def test_empty_token_fails_validation(self):
        with patch("core.turnstile.settings") as mock_settings:
            mock_settings.CLOUDFLARE_TURNSTILE_SECRET_KEY = "test-secret"
            assert verify_turnstile_token("") is False
            assert verify_turnstile_token(None) is False


class TestWebhookReplayProtection:
    def test_stale_timestamp_is_rejected(self):
        # Timestamp older than 300s
        old_ts = "1600000000"
        valid, reason, _ = verify_webhook_timestamp(old_ts, max_age_seconds=300)
        assert valid is False
        assert "exceeds" in reason
