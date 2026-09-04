"""PIN gate: Redis checks before expensive password hashing (R2.4)."""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache


@dataclass(frozen=True)
class PinGateDecision:
    allowed: bool
    status: str  # ok | locked | rate_limited | redis_unavailable
    retry_after: int = 0


def _gallery_lock_key(gallery_id) -> str:
    return f"gallery_pin_failures:{gallery_id}"


def _ip_rate_key(gallery_id, ip: str) -> str:
    return f"gallery_pin_ip:{gallery_id}:{ip or 'unknown'}"


def pin_gate_precheck(gallery_id, client_ip: str) -> PinGateDecision:
    """Return before check_password. Fail-closed if cache/Redis is broken."""
    max_failures = int(getattr(settings, "GALLERY_PIN_MAX_FAILED_ATTEMPTS", 10))
    lockout = int(getattr(settings, "GALLERY_PIN_LOCKOUT_SECONDS", 900))
    ip_limit = int(getattr(settings, "GALLERY_PIN_IP_ATTEMPTS_PER_MINUTE", 5))
    ip_window = int(getattr(settings, "GALLERY_PIN_IP_WINDOW_SECONDS", 60))

    try:
        failures = cache.get(_gallery_lock_key(gallery_id), 0) or 0
        if int(failures) >= max_failures:
            return PinGateDecision(False, "locked", retry_after=lockout)

        ip_key = _ip_rate_key(gallery_id, client_ip)
        ip_count = cache.get(ip_key, 0) or 0
        if int(ip_count) >= ip_limit:
            return PinGateDecision(False, "rate_limited", retry_after=ip_window)
    except Exception:
        return PinGateDecision(False, "redis_unavailable", retry_after=lockout)

    return PinGateDecision(True, "ok")


def record_pin_failure(gallery_id, client_ip: str) -> int:
    lockout = int(getattr(settings, "GALLERY_PIN_LOCKOUT_SECONDS", 900))
    ip_window = int(getattr(settings, "GALLERY_PIN_IP_WINDOW_SECONDS", 60))
    gallery_key = _gallery_lock_key(gallery_id)
    ip_key = _ip_rate_key(gallery_id, client_ip)

    try:
        try:
            gallery_count = cache.incr(gallery_key)
        except ValueError:
            cache.set(gallery_key, 1, timeout=lockout)
            gallery_count = 1

        try:
            cache.incr(ip_key)
        except ValueError:
            cache.set(ip_key, 1, timeout=ip_window)

        return int(gallery_count)
    except Exception:
        # Fail closed: treat as locked so we never hash under cache failure mid-write
        return int(getattr(settings, "GALLERY_PIN_MAX_FAILED_ATTEMPTS", 10))


def clear_pin_failures(gallery_id, client_ip: str | None = None) -> None:
    try:
        cache.delete(_gallery_lock_key(gallery_id))
        if client_ip is not None:
            cache.delete(_ip_rate_key(gallery_id, client_ip))
    except Exception:
        pass
