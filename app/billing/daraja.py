"""Daraja / M-Pesa STK callback helpers — secret_token gate (no HMAC assumption)."""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from billing.models import DarajaCallbackToken, ProcessedWebhook


def hash_daraja_token(raw_token: str) -> str:
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()


def mint_daraja_callback_token(*, user, checkout_request_id: str = "") -> str:
    """Return the raw secret_token (store only the hash)."""
    raw = secrets.token_urlsafe(32)
    ttl = int(getattr(settings, "DARAJA_CALLBACK_TOKEN_TTL_SECONDS", 900))
    DarajaCallbackToken.objects.create(
        token_hash=hash_daraja_token(raw),
        user=user,
        checkout_request_id=checkout_request_id or "",
        expires_at=timezone.now() + timedelta(seconds=ttl),
    )
    return raw


def consume_daraja_callback_token(raw_token: str) -> DarajaCallbackToken | None:
    """
    Validate and single-use consume. Returns None on missing/expired/replay.
    """
    if not raw_token:
        return None
    token_hash = hash_daraja_token(raw_token)
    with transaction.atomic():
        row = (
            DarajaCallbackToken.objects.select_for_update()
            .filter(token_hash=token_hash)
            .first()
        )
        if row is None:
            return None
        if row.used_at is not None:
            return None
        if row.expires_at <= timezone.now():
            return None
        row.used_at = timezone.now()
        row.save(update_fields=["used_at"])
        return row


def record_daraja_webhook_once(provider_event_id: str) -> bool:
    """Return True if this is the first time we see the provider event id."""
    _, created = ProcessedWebhook.objects.get_or_create(event_id=provider_event_id)
    return created
