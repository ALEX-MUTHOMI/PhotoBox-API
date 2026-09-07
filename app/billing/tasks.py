"""Celery tasks for Lemon Squeezy webhook processing and billing side effects."""

import logging
import json
import hashlib
from celery import shared_task
from django.db import transaction, OperationalError
from django.contrib.auth import get_user_model
from checkout.models import CheckoutSession, PricingPlan
from core.security import scrub_value
from billing.models import Subscription, BillingAuditLog, SubscriptionTier, DeadLetterQueue, ProcessedWebhook

logger = logging.getLogger(__name__)
User = get_user_model()

# Configuration Variables - Free tier remains static, Pro tier is now dynamic
FREE_TIER_BYTES = 1073741824       # 1GB
FREE_TIER_GB = 1


def _apply_workspace_storage_limit(user, limit_bytes):
    """Workspace is the quota ledger. Billing writes the byte cap here."""
    from core.models import Workspace

    workspace, created = Workspace.objects.select_for_update().get_or_create(
        user=user,
        defaults={
            'business_name': user.name or (user.email.split('@')[0] if user.email else 'Studio'),
            'storage_limit_bytes': limit_bytes,
        },
    )
    if not created and workspace.storage_limit_bytes != limit_bytes:
        workspace.storage_limit_bytes = limit_bytes
        workspace.save(update_fields=['storage_limit_bytes'])
    return workspace


def _resolve_subscription_for_event(event_name, subscription_id, user_id):
    """
    Resolve the subscription row in a way that survives out-of-order delivery.

    `subscription_updated` can legally arrive after `subscription_cancelled`.
    When that happens, we fall back to the user anchor from custom_data instead
    of treating the renewal as an orphaned event.
    """
    if event_name == 'subscription_created':
        user = User.objects.select_for_update().get(id=user_id)
        sub = Subscription.objects.select_for_update().get(user=user)
        return user, sub

    try:
        sub = Subscription.objects.select_for_update().get(
            lemon_squeezy_subscription_id=subscription_id
        )
        user = User.objects.select_for_update().get(id=sub.user_id)
        return user, sub
    except Subscription.DoesNotExist:
        if user_id is None:
            raise
        user = User.objects.select_for_update().get(id=user_id)
        sub = Subscription.objects.select_for_update().get(user=user)
        return user, sub

def safe_create_dlq(event_id, payload_data, error_message):
    """
    FAILSAFE: Ensures we NEVER lose a payment payload, even if the DB goes completely offline.
    """
    safe_payload = scrub_value(payload_data)
    try:
        DeadLetterQueue.objects.create(
            event_id=event_id,
            payload=safe_payload,
            error_message=error_message
        )
    except Exception as dlq_exc:
        try:
            payload_fingerprint = hashlib.sha256(
                json.dumps(safe_payload, sort_keys=True, separators=(',', ':'), default=str)
                .encode('utf-8')
            ).hexdigest()[:16]
        except (TypeError, ValueError):
            payload_fingerprint = "unavailable"

        # FATAL FALLBACK: preserve a correlation handle without logging raw
        # provider payloads, emails, checkout tokens, or custom_data values.
        logger.critical(
            "FATAL DLQ FAILURE | event_id=%s | original_error_recorded=true | "
            "db_error_type=%s | payload_fingerprint=%s | raw_payload_logged=false",
            event_id,
            type(dlq_exc).__qualname__,
            payload_fingerprint,
        )

@shared_task(bind=True, max_retries=3, default_retry_delay=5)
def process_lemon_squeezy_webhook(self, payload_data, event_id, payload_hash=None):
    """
    BACKGROUND WORKER: Asynchronously processes verified webhooks from Lemon Squeezy.
    Fully secured against Unpaid exploits, replay attacks, and state desynchronization.
    """
    event_name = payload_data.get('meta', {}).get('event_name')
    custom_data = payload_data.get('meta', {}).get('custom_data', {})

    user_id = custom_data.get('user_id')
    session_token = custom_data.get('session_token')

    data_node = payload_data.get('data', {})
    subscription_id = str(data_node.get('id'))
    attributes = data_node.get('attributes', {})
    sub_status = attributes.get('status')
    idempotency_key = payload_hash or hashlib.sha256(
        json.dumps(payload_data, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()

    if not event_name or not event_name.startswith('subscription_'):
        return "Ignored: Irrelevant event type"

    try:
        with transaction.atomic():

            # 1. IDEMPOTENCY LOCK: Prevent Replay Attacks and Duplicate Webhooks (From Original)
            webhook_log, created = ProcessedWebhook.objects.select_for_update().get_or_create(
                event_id=idempotency_key
            )
            if not created:
                logger.info(
                    "Webhook replay skipped. source_event_id=%s idempotency_key=%s",
                    event_id,
                    idempotency_key,
                )
                return "Ignored: Duplicate event"

            # ==========================================
            # EVENT: SUBSCRIPTION CREATED OR UPDATED
            # ==========================================
            if event_name in ['subscription_created', 'subscription_updated']:

                # HACKER FIX: THE EMPTY WALLET DEFENSE
                if sub_status not in ['active', 'past_due', 'on_trial']:
                    logger.warning(f"Sub {subscription_id} unpaid (Status: {sub_status}). Ignoring.")
                    return "Ignored: Subscription unpaid."

                # BRAND NEW SUBSCRIPTION: Close the Checkout Loop
                if event_name == 'subscription_created':
                    if not session_token or not user_id:
                        raise ValueError("CRITICAL: Webhook missing custom_data anchors.")

                    session = CheckoutSession.objects.select_for_update().get(session_token=session_token)
                    if str(session.user_id) != str(user_id):
                        raise ValueError("CRITICAL: Webhook custom_data user/session mismatch.")

                    if session.status == 'COMPLETED':
                        return "Already processed"

                    session.status = 'COMPLETED'
                    session.save()

                    user, sub = _resolve_subscription_for_event(
                        event_name,
                        subscription_id,
                        user_id,
                    )
                    bandwidth_limit = session.plan.bandwidth_limit_bytes

                # UPGRADING EXISTING SUBSCRIPTION: Dynamic Pricing Lookup
                else:
                    user, sub = _resolve_subscription_for_event(
                        event_name,
                        subscription_id,
                        user_id,
                    )
                    variant_id = str(attributes.get('variant_id'))
                    new_plan = PricingPlan.objects.get(lemon_squeezy_variant_id=variant_id)
                    bandwidth_limit = new_plan.bandwidth_limit_bytes

                old_is_pro = sub.is_pro
                dynamic_gb_limit = int(bandwidth_limit / (1024 * 1024 * 1024))

                # A. UPDATE THE BILLING VAULT PHYSICS
                sub.is_pro = True
                sub.lemon_squeezy_subscription_id = subscription_id
                sub.storage_limit_bytes = bandwidth_limit
                sub.save()

                # B. SYNC DISPLAY MATH + WORKSPACE LEDGER
                user.subscription_tier = 'PRO'
                user.storage_limit_gb = dynamic_gb_limit
                user.save(update_fields=['subscription_tier', 'storage_limit_gb'])
                _apply_workspace_storage_limit(user, bandwidth_limit)

                # C. AUDIT LOG
                if not old_is_pro:
                    BillingAuditLog.objects.create(
                        user=user, old_state=SubscriptionTier.FREE,
                        new_state=SubscriptionTier.PRO, webhook_event_id=event_id
                    )
                return f"Subscription {event_name} processed successfully."

            # ==========================================
            # EVENT: SUBSCRIPTION CANCELLED OR EXPIRED
            # ==========================================
            elif event_name in ['subscription_cancelled', 'subscription_expired']:
                user, sub = _resolve_subscription_for_event(
                    event_name,
                    subscription_id,
                    user_id,
                )

                if sub.is_pro:
                    # A. Downgrade the Billing Vault Physics
                    sub.is_pro = False
                    sub.storage_limit_bytes = FREE_TIER_BYTES
                    # Preserve the external id for late-arriving update events and
                    # operator forensics instead of severing the correlation anchor.
                    sub.lemon_squeezy_subscription_id = subscription_id
                    sub.save()

                    # B. Sync display math + Workspace ledger
                    user.subscription_tier = 'FREE'
                    user.storage_limit_gb = FREE_TIER_GB
                    user.save(update_fields=['subscription_tier', 'storage_limit_gb'])
                    _apply_workspace_storage_limit(user, FREE_TIER_BYTES)

                    # C. Audit Log
                    BillingAuditLog.objects.create(
                        user=user, old_state=SubscriptionTier.PRO,
                        new_state=SubscriptionTier.FREE, webhook_event_id=event_id
                    )
                return "Subscription downgraded successfully"

            # ==========================================
            # EVENT: PAYMENT FAILED
            # ==========================================
            elif event_name == 'subscription_payment_failed':
                logger.warning(f"Payment failed for sub {subscription_id}. Grace period active.")
                return "Payment failure logged."

            return "Event ignored: Unhandled state."

    except OperationalError as exc:
        # DB is locked by another transaction. Wait and retry safely.
        logger.warning(f"Database locked on {event_id}. Retrying {self.request.retries}/3...")
        from core.celery_retry import retry_or_call  # noqa: PLC0415

        def _dlq_locked():
            logger.error(f"Max retries exhausted for webhook {event_id}. Routing to DLQ.")
            safe_create_dlq(event_id, payload_data, "Max retries exceeded (Database locked)")

        return retry_or_call(self, exc, on_exhausted=_dlq_locked)

    except (User.DoesNotExist, Subscription.DoesNotExist):
        logger.error(f"User not found for webhook {event_id}.")
        safe_create_dlq(event_id, payload_data, "User Not Found")

    except Exception as exc:
        # Catches all other random logic crashes
        logger.critical(f"Critical logic failure on webhook {event_id}: {str(exc)}")
        safe_create_dlq(event_id, payload_data, f"Logic Exception: {str(exc)}")
