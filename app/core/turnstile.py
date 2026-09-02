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


def _turnstile_secret() -> str:
    for attr in ("TURNSTILE_SECRET_KEY", "CLOUDFLARE_TURNSTILE_SECRET_KEY"):
        value = getattr(settings, attr, "")
        if isinstance(value, str) and value:
            return value
    return ""


def verify_turnstile_token(token: str | None, remote_ip: str | None = None) -> bool:
    """
    Verify a Cloudflare Turnstile token.

    Fail-closed when the secret is missing outside explicit test overrides.
    """
    secret_key = _turnstile_secret()
    if not secret_key:
        return False

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
        return bool(getattr(settings, "TURNSTILE_FAIL_OPEN", False))
