"""Core Celery tasks shared across apps (for example GDPR asset purge)."""

import logging
from typing import Iterable

from celery import shared_task

from core.celery_retry import retry_or_return
from gallery.asset_purge import invalidate_cloudinary_fetch
from gallery.cloudinary_delivery import build_r2_public_url
from gallery.storage import delete_r2_objects

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="core.tasks.purge_deleted_photographer_assets",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def purge_deleted_photographer_assets(self, user_id: str, object_keys: Iterable[str]):
    keys = sorted({key for key in object_keys if key})
    if not keys:
        return {"status": "nothing_to_delete", "user_id": user_id, "deleted": 0}

    deleted = delete_r2_objects(keys)
    # Web derivatives may have been fetched via Cloudinary — invalidate each source URL.
    for key in keys:
        if "/web/" in key or key.endswith(".webp"):
            invalidate_cloudinary_fetch(build_r2_public_url(key))

    if deleted:
        logger.info("[GDPR] Deleted %d R2 object(s) for photographer %s.", len(keys), user_id)
        return {"status": "deleted", "user_id": user_id, "deleted": len(keys)}

    return retry_or_return(
        self,
        RuntimeError("R2 delete_objects failed."),
        fallback={"status": "error", "user_id": user_id, "deleted": 0},
    )
