"""
Gallery lifecycle and retention tasks.

Registered in settings.GALLERY_LIFECYCLE_BEAT_SCHEDULE and
settings.RETENTION_BEAT_SCHEDULE. Every task here must be idempotent: beat is
at-least-once, and a rolling deploy can briefly run two beat containers.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

MAGIC_LINK_RETENTION_DAYS = 7


@shared_task(name="gallery.retention.expire_due_galleries")
def expire_due_galleries() -> Dict[str, Any]:
    """Soft-expire every published gallery past its expires_at."""
    from gallery.models import Event, Photo

    now = timezone.now()
    due = Event.objects.filter(
        is_published=True,
        expires_at__isnull=False,
        expires_at__lte=now,
    )

    gallery_ids = list(due.values_list("id", flat=True))
    if not gallery_ids:
        return {"status": "clean", "galleries_expired": 0, "photos_expired": 0}

    photos_expired = Photo.objects.filter(
        scene__event_id__in=gallery_ids,
        status__in=("READY", "PROCESSING"),
    ).update(status="EXPIRED")

    logger.info(
        "[EXPIRY] Expired %d photo(s) across %d gallery(ies).",
        photos_expired,
        len(gallery_ids),
    )
    return {
        "status": "complete",
        "galleries_expired": len(gallery_ids),
        "photos_expired": photos_expired,
    }


@shared_task(name="gallery.retention.purge_expired_magic_links")
def purge_expired_magic_links() -> Dict[str, Any]:
    """Delete magic links whose window closed more than MAGIC_LINK_RETENTION_DAYS ago."""
    from gallery.models import GalleryMagicLink

    cutoff = timezone.now() - timedelta(days=MAGIC_LINK_RETENTION_DAYS)
    deleted, _ = GalleryMagicLink.objects.filter(expires_at__lt=cutoff).delete()
    logger.info("[RETENTION] Purged %d expired magic link(s).", deleted)
    return {"status": "complete", "magic_links_deleted": deleted}


@shared_task(name="gallery.retention.purge_expired_archives")
def purge_expired_archives() -> Dict[str, Any]:
    """Delete archive ZIPs from R2 once their TTL passes, then drop the row."""
    from gallery.models import GalleryArchiveJob
    from gallery.storage import delete_r2_objects

    now = timezone.now()
    expired = GalleryArchiveJob.objects.filter(
        expires_at__isnull=False,
        expires_at__lte=now,
    ).exclude(r2_zip_key__isnull=True).exclude(r2_zip_key="")

    purged = 0
    failed = 0
    for job in expired.iterator():
        if delete_r2_objects([job.r2_zip_key]):
            GalleryArchiveJob.objects.filter(id=job.id).delete()
            purged += 1
        else:
            failed += 1
            logger.warning(
                "[RETENTION] R2 delete failed for archive %s (key=%s).",
                job.id,
                job.r2_zip_key,
            )

    return {"status": "complete", "archives_purged": purged, "archives_failed": failed}


@shared_task(name="gallery.retention.hard_delete_expired_galleries")
def hard_delete_expired_galleries() -> Dict[str, Any]:
    """Irreversible step: remove bytes for galleries past the grace period."""
    from core.quota import release_workspace_bytes
    from gallery.models import Event, Photo
    from gallery.storage import delete_r2_objects

    grace_days = int(getattr(settings, "GALLERY_HARD_DELETE_GRACE_DAYS", 30))
    cutoff = timezone.now() - timedelta(days=grace_days)

    due = Event.objects.filter(
        expires_at__isnull=False,
        expires_at__lte=cutoff,
    ).select_related("workspace")

    photos_deleted = 0
    bytes_reclaimed = 0
    galleries_failed = 0

    for event in due.iterator():
        photos = list(
            Photo.objects.filter(scene__event=event, status="EXPIRED")
            .values("id", "file_size_bytes", "r2_object_key", "web_r2_object_key")
        )
        if not photos:
            continue

        keys = [
            key
            for photo in photos
            for key in (photo["r2_object_key"], photo["web_r2_object_key"])
            if key
        ]

        if keys and not delete_r2_objects(keys):
            galleries_failed += 1
            logger.error(
                "[RETENTION] R2 delete failed for gallery %s. Rows and quota retained.",
                event.id,
            )
            continue

        refund = sum(photo["file_size_bytes"] or 0 for photo in photos)
        Photo.objects.filter(id__in=[photo["id"] for photo in photos]).delete()
        release_workspace_bytes(event.workspace_id, refund)

        photos_deleted += len(photos)
        bytes_reclaimed += refund

    logger.info(
        "[RETENTION] Hard-deleted %d photo(s), reclaimed %d byte(s).",
        photos_deleted,
        bytes_reclaimed,
    )
    return {
        "status": "complete",
        "photos_deleted": photos_deleted,
        "bytes_reclaimed": bytes_reclaimed,
        "galleries_failed": galleries_failed,
    }
