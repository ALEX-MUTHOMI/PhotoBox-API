"""Celery maintenance tasks for abandoned heavy-lane uploads."""

import logging
from datetime import timedelta
from typing import Any, Dict

from celery import shared_task
from django.db import DatabaseError, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="ingestion.tasks.reap_abandoned_uploads", max_retries=5)
def reap_abandoned_uploads(self) -> Dict[str, Any]:
    """
    THE FIX: The Reaper Task with Phantom Upload Defense.
    Finds PENDING assets older than 24 hours.
    CRITICALLY: Verifies via Cloudflare R2 if the file actually exists
    before refunding the workspace quota. Uses exponential backoff on outages.
    """
    # Local imports prevent circular dependencies at Django boot time
    from gallery.models import Photo
    from gallery.storage import r2_object_exists

    # 1. Look for abandoned tickets
    abandoned_assets = Photo.objects.filter(
        status='PENDING',
        uploaded_at__lt=timezone.now() - timedelta(hours=24),
        r2_object_key__isnull=False,
    ).exclude(r2_object_key="").select_related('scene__event__workspace')
    
    if not abandoned_assets.exists():
        return {"status": "clean", "message": "No abandoned assets to reap."}

    reaped_count = 0
    phantom_count = 0

    for asset in abandoned_assets:
        workspace = asset.scene.event.workspace
        
        # 2. THE PHANTOM EXORCISM: Head check R2 physically
        try:
            # Uses the centralized, thread-safe, path-traversal-protected storage API
            file_physically_exists = r2_object_exists(asset.r2_object_key)
        except Exception as exc:
            # FAIL CLOSED: if R2 cannot be trusted, do not mutate asset state or quota.
            # In a real worker we retry with backoff; in direct/eager execution
            # (used by tests and local debugging) we stop quietly instead of
            # propagating Celery's Retry exception into the caller.
            logger.warning("[REAPER] R2 unreachable for Asset %s: %s. Backing off.", asset.id, exc)
            from core.celery_retry import retry_or_return  # noqa: PLC0415

            return retry_or_return(
                self,
                exc,
                countdown=60 * (2 ** self.request.retries),
                fallback={
                    "status": "deferred",
                    "message": "R2 unavailable; reaper aborted without mutating asset state.",
                    "reaped_count": reaped_count,
                    "phantom_count": phantom_count,
                },
            )

        with transaction.atomic():
            if file_physically_exists:
                # PHANTOM UPLOAD DETECTED!
                # Hacker uploaded the file but blocked the webhook to keep it 'PENDING'.
                logger.critical(f"PHANTOM UPLOAD DETECTED: Asset {asset.id} exists in R2 but webhook was suppressed!")
                Photo.objects.filter(id=asset.id, status='PENDING').update(status='QUARANTINED')
                phantom_count += 1
            else:
                # LEGITIMATE ABANDONMENT
                try:
                    # Lock ONLY this specific asset row to prevent worker collisions
                    locked_asset = Photo.objects.select_for_update(nowait=True).get(
                        id=asset.id,
                        status='PENDING',
                    )
                except (Photo.DoesNotExist, DatabaseError):
                    continue  # Another worker grabbed this or it was deleted

                refund = locked_asset.file_size_bytes or 0
                if refund > 0:
                    from core.quota import release_workspace_bytes
                    release_workspace_bytes(workspace.id, refund)

                # Preserve the row for forensics and make the refund idempotent:
                # only PENDING assets are ever reaped, so once marked FAILED the
                # quota cannot be refunded twice on a later pass.
                locked_asset.status = 'FAILED'
                locked_asset.save(update_fields=['status'])
                reaped_count += 1
                logger.info(f"[REAPER] Cleaned stale asset {asset.id}, refunded {refund} bytes.")

    return {
        "status": "complete", 
        "reaped_count": reaped_count, 
        "phantom_count": phantom_count
    }
