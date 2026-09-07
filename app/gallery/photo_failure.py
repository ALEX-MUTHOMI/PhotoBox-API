"""Photo failure helpers that refund quota exactly once and enqueue asset purge."""
from __future__ import annotations

import logging
from typing import Any, Optional

from django.db import transaction

from core.quota import release_workspace_bytes
from gallery.cloudinary_delivery import build_r2_public_url

logger = logging.getLogger(__name__)

_QUOTA_CHARGED_STATUSES = frozenset({"PENDING", "READY"})


def mark_photo_failed_and_release_quota(photo_id: Any) -> bool:
    """Mark PENDING/READY → FAILED, refund storage once, enqueue R2/CDN purge.

    Returns True when a status transition occurred. Idempotent for already-FAILED.
    """
    from gallery.asset_purge import enqueue_purge_photo_assets  # noqa: PLC0415
    from gallery.models import Photo  # noqa: PLC0415

    with transaction.atomic():
        try:
            photo = (
                Photo.objects.select_for_update()
                .select_related("scene__event")
                .get(id=photo_id)
            )
        except Photo.DoesNotExist:
            return False

        if photo.status not in _QUOTA_CHARGED_STATUSES:
            return False

        r2_key = photo.r2_object_key or None
        web_key = photo.web_r2_object_key or None
        r2_public_url = build_r2_public_url(web_key) if web_key else None
        refund = int(photo.file_size_bytes or 0)
        workspace_id = photo.scene.event.workspace_id

        photo.status = "FAILED"
        photo.save(update_fields=["status"])
        release_workspace_bytes(workspace_id, refund)
        enqueue_purge_photo_assets(
            r2_object_key=r2_key,
            web_r2_object_key=web_key,
            r2_public_url=r2_public_url,
        )
        logger.info(
            "[PHOTO FAIL] photo=%s marked FAILED; refunded %s bytes; purge enqueued.",
            photo_id,
            refund,
        )
        return True


def should_refund_on_destroy(status: Optional[str]) -> bool:
    """Refund quota on photographer delete only when still charged."""
    return (status or "") in _QUOTA_CHARGED_STATUSES
