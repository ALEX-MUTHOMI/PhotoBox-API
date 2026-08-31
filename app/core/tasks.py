"""Core Celery tasks shared across apps (for example GDPR asset purge)."""

import logging
from typing import Iterable

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

from gallery.storage import delete_r2_objects

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="core.tasks.purge_deleted_photographer_assets",
    acks_late=True,
    reject_on_worker_lost=True,
)
def purge_deleted_photographer_assets(self, user_id: str, object_keys: Iterable[str]):
    keys = sorted({key for key in object_keys if key})
    if not keys:
        return {"status": "nothing_to_delete", "user_id": user_id, "deleted": 0}

    deleted = delete_r2_objects(keys)
    if deleted:
        logger.info("[GDPR] Deleted %d R2 object(s) for photographer %s.", len(keys), user_id)
        return {"status": "deleted", "user_id": user_id, "deleted": len(keys)}

    try:
        raise self.retry(exc=RuntimeError("R2 delete_objects failed."))
    except MaxRetriesExceededError:
        logger.error("[GDPR] Max retries exceeded while deleting assets for %s.", user_id)
        return {"status": "error", "user_id": user_id, "deleted": 0}
