"""Retention for append-only billing ledgers. Registered in settings.RETENTION_BEAT_SCHEDULE."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

PROCESSED_WEBHOOK_RETENTION_DAYS = 90
DEAD_LETTER_RETENTION_DAYS = 365
REGISTRATION_LOG_RETENTION_DAYS = 90


@shared_task(name="billing.retention.prune_billing_ledgers")
def prune_billing_ledgers() -> Dict[str, Any]:
    """Prune the three append-only billing ledgers."""
    from billing.models import DeadLetterQueue, ProcessedWebhook, RegistrationLog

    now = timezone.now()

    webhooks_deleted, _ = ProcessedWebhook.objects.filter(
        processed_at__lt=now - timedelta(days=PROCESSED_WEBHOOK_RETENTION_DAYS)
    ).delete()

    dlq_deleted, _ = DeadLetterQueue.objects.filter(
        created_at__lt=now - timedelta(days=DEAD_LETTER_RETENTION_DAYS)
    ).delete()

    registrations_deleted, _ = RegistrationLog.objects.filter(
        created_at__lt=now - timedelta(days=REGISTRATION_LOG_RETENTION_DAYS)
    ).delete()

    logger.info(
        "[RETENTION] Pruned webhooks=%d dlq=%d registrations=%d.",
        webhooks_deleted,
        dlq_deleted,
        registrations_deleted,
    )
    return {
        "status": "complete",
        "processed_webhooks_deleted": webhooks_deleted,
        "dead_letter_entries_deleted": dlq_deleted,
        "registration_logs_deleted": registrations_deleted,
    }
