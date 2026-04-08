import logging
import json
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.db import transaction, OperationalError
from django.contrib.auth import get_user_model
from .models import ProcessedWebhook, Subscription, BillingAuditLog, DeadLetterQueue

logger = logging.getLogger(__name__)
User = get_user_model()

# Configuration Variables - Centralized to avoid "Magic Numbers"
FREE_TIER_BYTES = 1073741824       # 1GB
PRO_TIER_BYTES = 107374182400      # 100GB
FREE_TIER_GB = 1
PRO_TIER_GB = 100

def safe_create_dlq(event_id, payload_data, error_message):
    """
    FAILSAFE: Ensures we NEVER lose a payment payload, even if the DB goes completely offline.
    """
    try:
        DeadLetterQueue.objects.create(
            event_id=event_id,
            payload=payload_data,
            error_message=error_message
        )
    except Exception as dlq_exc:
        # FATAL FALLBACK: Dump the raw JSON to the system logs for manual AWS/Datadog recovery.
        logger.critical(
            f"FATAL DLQ FAILURE | Event: {event_id} | "
            f"Original Error: {error_message} | "
            f"DB Error: {dlq_exc} | "
            f"RAW PAYLOAD: {json.dumps(payload_data)}"
        )

@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def process_lemon_squeezy_webhook(self, payload_data, event_id):
    """
    BACKGROUND WORKER: Fully evaluates the Lemon Squeezy Subscription State Machine.
    Synchronizes the Billing Vault and the Core User identity in a single atomic lock.
    """
    event_name = payload_data.get('meta', {}).get('event_name')
    custom_data = payload_data.get('meta', {}).get('custom_data', {})
    user_id = custom_data.get('user_id')

    # Extract the actual status of the subscription from Lemon Squeezy
    attributes = payload_data.get('data', {}).get('attributes', {})
    subscription_status = attributes.get('status')

    if not user_id:
        logger.warning(f"Webhook {event_id} missing user_id. Discarding.")
        return "Ignored: Missing user_id"

    # We only care about events that dictate subscription access
    if not event_name or not event_name.startswith('subscription_'):
        return "Ignored: Irrelevant event type"

    try:
        with transaction.atomic():
            # 1. Idempotency Lock (Prevents Double-Billing)
            webhook_log, created = ProcessedWebhook.objects.select_for_update().get_or_create(event_id=event_id)
            if not created:
                logger.info(f"Webhook {event_id} already processed.")
                return "Ignored: Duplicate event"

            user = User.objects.get(id=user_id)
            sub = user.subscription

            # 2. STATE MACHINE LOGIC
            # Lemon Squeezy active states: 'active', 'on_trial', 'past_due' (usually granted a grace period)
            is_currently_entitled = subscription_status in ['active', 'on_trial', 'past_due']

            # UPGRADE PATH: If Lemon Squeezy says they should have access, and we currently show them as FREE
            if is_currently_entitled and not sub.is_pro:
                BillingAuditLog.objects.create(
                    user=user, old_state="FREE", new_state="PRO", webhook_event_id=event_id
                )

                # A. Update the Billing Vault Physics
                sub.is_pro = True
                sub.storage_limit_bytes = PRO_TIER_BYTES
                sub.lemon_squeezy_subscription_id = str(payload_data.get('data', {}).get('id'))
                sub.save()

                # B. Sync with the Core User App (Domain-Driven Design)
                user.subscription_tier = 'PRO'
                user.storage_limit_gb = PRO_TIER_GB
                user.save(update_fields=['subscription_tier', 'storage_limit_gb'])

            # DOWNGRADE PATH: If Lemon Squeezy says they LOST access (unpaid, expired, cancelled), and we show them as PRO
            elif not is_currently_entitled and sub.is_pro:
                BillingAuditLog.objects.create(
                    user=user, old_state="PRO", new_state="FREE", webhook_event_id=event_id
                )

                # A. Downgrade the Billing Vault Physics
                sub.is_pro = False
                sub.storage_limit_bytes = FREE_TIER_BYTES
                sub.save()

                # B. Sync with the Core User App
                user.subscription_tier = 'FREE'
                user.storage_limit_gb = FREE_TIER_GB
                user.save(update_fields=['subscription_tier', 'storage_limit_gb'])

        return f"Success: State evaluated for status {subscription_status}."

    except OperationalError as exc:
        # DB is locked by another transaction. Wait and retry safely.
        logger.warning(f"Database locked on {event_id}. Retrying {self.request.retries}/3...")
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            logger.error(f"Max retries exhausted for webhook {event_id}. Routing to DLQ.")
            safe_create_dlq(event_id, payload_data, "Max retries exceeded (Database locked)")

    except User.DoesNotExist:
        logger.error(f"User {user_id} not found for webhook {event_id}.")
        safe_create_dlq(event_id, payload_data, "User Not Found")

    except Exception as exc:
        # Catches all other random logic crashes
        logger.critical(f"Critical logic failure on webhook {event_id}: {str(exc)}")
        safe_create_dlq(event_id, payload_data, f"Logic Exception: {str(exc)}")
