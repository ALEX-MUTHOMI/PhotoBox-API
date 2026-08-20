"""
Cloudflare Turnstile Bot Detection Verification Helper.

Validates the client-side `cf-turnstile-response` token against Cloudflare's
siteverify API endpoint.
"""
from __future__ import annotations

import logging
import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify_turnstile_token(token: str | None, remote_ip: str | None = None) -> bool:
    """
    Verify a Cloudflare Turnstile token.

    Returns True if valid or if Turnstile is disabled in development.
    """
    secret_key = getattr(settings, "CLOUDFLARE_TURNSTILE_SECRET_KEY", "")
    if not secret_key:
        # Disabled or in local dev/testing
        return True

    if not token:
        return False

    payload = {
        "secret": secret_key,
        "response": token,
    }
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        with httpx.Client(timeout=5.0) as client:
            res = client.post(TURNSTILE_VERIFY_URL, data=payload)
            data = res.json()
            return bool(data.get("success", False))
    except Exception as exc:
        logger.warning("Turnstile verification connection failed", extra={"error": str(exc)})
        # Fail-closed in production, fail-open if configured
        return getattr(settings, "TURNSTILE_FAIL_OPEN", False)
