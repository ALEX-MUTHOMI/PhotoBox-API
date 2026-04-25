"""
Shared security helpers for logging, observability hygiene, and webhook auth.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
from datetime import datetime, timezone as dt_timezone
from typing import Any

from django.conf import settings


_EMAIL_RE = re.compile(r"([A-Z0-9._%+\-]+)@([A-Z0-9.\-]+\.[A-Z]{2,})", re.IGNORECASE)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)
_WEBHOOK_SIGNATURE_RE = re.compile(r"^[A-Fa-f0-9]{64}$")
_WEBHOOK_TIMESTAMP_RE = re.compile(r"^[0-9]{1,20}$")

_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "refresh",
    "access",
    "id_token",
    "api_key",
    "email",
    "client_email",
    "email_host_password",
}


def _hash_value(prefix: str, value: str) -> str:
    salt = getattr(settings, "LOG_SCRUBBER_SALT", getattr(settings, "SECRET_KEY", "photobox"))
    digest = hashlib.sha256(f"{prefix}:{salt}:{value}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def scrub_email(value: str | None) -> str:
    if not value:
        return "email_unknown"
    return _hash_value("email", value.strip().lower())


def scrub_ip(value: str | None) -> str:
    if not value:
        return "ip_unknown"
    candidate = value.split(",")[0].strip()
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return "ip_invalid"
    return _hash_value("ip", candidate)


def scrub_text(value: str) -> str:
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    redacted = _BEARER_RE.sub(r"\1[REDACTED]", redacted)
    return redacted


def scrub_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if key.lower() in _SENSITIVE_KEYS else scrub_value(inner))
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [scrub_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_value(item) for item in value)
    if isinstance(value, str):
        return scrub_text(value)
    return value


def sentry_before_send(event: dict, hint: dict) -> dict:
    return scrub_value(event)


def sentry_before_breadcrumb(crumb: dict, hint: dict) -> dict:
    return scrub_value(crumb)


def build_webhook_signature_input(
    timestamp: str,
    payload_bytes: bytes | bytearray | memoryview,
) -> bytes:
    """
    Build the canonical message covered by the webhook HMAC.

    Contract:
      HMAC-SHA256(secret, "<unix_timestamp>.<raw_request_body>")
    """
    if isinstance(payload_bytes, memoryview):
        payload_bytes = payload_bytes.tobytes()
    else:
        payload_bytes = bytes(payload_bytes)

    return timestamp.encode("ascii") + b"." + payload_bytes


def compute_webhook_signature(
    secret: bytes,
    timestamp: str,
    payload_bytes: bytes | bytearray | memoryview,
) -> str:
    """Compute the canonical lowercase hex HMAC digest for a webhook message."""
    return hmac.new(
        secret,
        build_webhook_signature_input(timestamp, payload_bytes),
        hashlib.sha256,
    ).hexdigest()


def verify_webhook_timestamp(
    timestamp_header: str | None,
    *,
    max_age_seconds: int = 300,
    max_future_skew_seconds: int = 60,
    now: datetime | None = None,
) -> tuple[bool, str, str | None]:
    """
    Validate and canonicalise a webhook timestamp header.

    Returns:
      (True, "", canonical_timestamp) on success
      (False, reason, None) on failure
    """
    if timestamp_header is None:
        return False, "timestamp_missing", None
    if not isinstance(timestamp_header, str):
        return False, "timestamp_unparseable", None
    if timestamp_header != timestamp_header.strip():
        return False, "timestamp_unparseable", None
    if not _WEBHOOK_TIMESTAMP_RE.fullmatch(timestamp_header):
        return False, "timestamp_unparseable", None

    try:
        ts = int(timestamp_header)
        canonical = str(ts)
        if canonical != timestamp_header:
            return False, "timestamp_noncanonical", None

        webhook_dt = datetime.fromtimestamp(ts, tz=dt_timezone.utc)
        current_dt = now or datetime.now(tz=dt_timezone.utc)
        age_seconds = (current_dt - webhook_dt).total_seconds()

        if age_seconds > max_age_seconds:
            return False, f"age:{age_seconds:.0f}s_exceeds:{max_age_seconds}s", None
        if age_seconds < -max_future_skew_seconds:
            return False, f"future_dated:{abs(age_seconds):.0f}s_ahead", None
    except (ValueError, TypeError, OSError, OverflowError):
        return False, "timestamp_unparseable", None

    return True, "", canonical


def verify_webhook_signature(
    payload_bytes: bytes | bytearray | memoryview,
    timestamp: str,
    signature_header: str | None,
    *,
    secret_setting: str = "CLOUDFLARE_WEBHOOK_SECRET",
) -> tuple[bool, str]:
    """
    Verify a webhook HMAC against the configured environment secret.

    Returns:
      (True, "") on success
      (False, reason) on failure
    """
    raw_secret = getattr(settings, secret_setting, "")
    if not raw_secret:
        return False, "secret_not_configured"

    try:
        secret_bytes = raw_secret.encode("utf-8", errors="strict")
    except (UnicodeEncodeError, AttributeError):
        return False, "secret_encoding_error"

    if not signature_header:
        return False, "signature_missing"
    if not isinstance(signature_header, str):
        return False, "signature_format_invalid"
    if signature_header != signature_header.strip():
        return False, "signature_format_invalid"
    if not _WEBHOOK_SIGNATURE_RE.fullmatch(signature_header):
        return False, "signature_format_invalid"

    candidate = signature_header.lower()
    expected = compute_webhook_signature(secret_bytes, timestamp, payload_bytes)

    if not hmac.compare_digest(expected, candidate):
        return False, "signature_mismatch"

    return True, ""
