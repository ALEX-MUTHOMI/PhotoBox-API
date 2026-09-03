"""Single source for gallery lifetime in days and Event.expires_at stamps."""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone


def gallery_ttl_days_for(workspace) -> int:
    """
    Resolve how long a newly published gallery should live.

    1. Latest completed checkout's PricingPlan.gallery_expiry_days.
    2. Else GALLERY_TTL_DAYS for PRO/FREE from the owner's Subscription.
    3. Values <= 0 mean unlimited (sweep skips a null expires_at).
    """
    from checkout.models import CheckoutSession

    session = (
        CheckoutSession.objects.filter(user_id=workspace.user_id, status="COMPLETED")
        .select_related("plan")
        .order_by("-updated_at", "-created_at")
        .first()
    )
    if session is not None:
        return int(session.plan.gallery_expiry_days)

    ttl_map = getattr(settings, "GALLERY_TTL_DAYS", {"FREE": 30, "PRO": 365, "ENTERPRISE": 0})
    subscription = getattr(workspace.user, "subscription", None)
    is_pro = bool(getattr(subscription, "is_pro", False))
    tier = "PRO" if is_pro else "FREE"
    return int(ttl_map.get(tier, 30))


def stamp_event_expiry_on_publish(event) -> None:
    """Write expires_at once on the False→True publish transition."""
    if event.expires_at is not None:
        return

    ttl_days = gallery_ttl_days_for(event.workspace)
    if ttl_days <= 0:
        return

    event.expires_at = timezone.now() + timedelta(days=ttl_days)
    event.save(update_fields=["expires_at"])
