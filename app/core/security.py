"""
Shared security helpers for logging and observability hygiene.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
from typing import Any

from django.conf import settings


_EMAIL_RE = re.compile(r"([A-Z0-9._%+\-]+)@([A-Z0-9.\-]+\.[A-Z]{2,})", re.IGNORECASE)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)

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
