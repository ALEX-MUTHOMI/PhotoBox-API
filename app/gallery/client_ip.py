"""Resolve client IP for throttling — never honor spoofed XFF unless CF trust is on."""
from __future__ import annotations

from django.conf import settings


def get_request_client_ip(request) -> str:
    """
    Identity for PIN / unauthenticated rate limits.

    When TRUST_CLOUDFLARE_CLIENT_IP is false (default), only REMOTE_ADDR is used
    so attackers cannot pick their throttle bucket via X-Forwarded-For.
    """
    if getattr(settings, "TRUST_CLOUDFLARE_CLIENT_IP", False):
        cf_ip = (request.META.get("HTTP_CF_CONNECTING_IP") or "").split(",")[0].strip()
        if cf_ip:
            return cf_ip
    return (request.META.get("REMOTE_ADDR") or "unknown").strip() or "unknown"
