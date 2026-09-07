"""Async R2 + Cloudinary fetch purge for deleted photo assets."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from celery import shared_task
from django.db import transaction

from core.celery_retry import retry_or_return
from gallery.cloudinary_delivery import build_r2_public_url
from gallery.storage import delete_r2_objects

logger = logging.getLogger(__name__)


def invalidate_cloudinary_fetch(r2_public_url: Optional[str]) -> bool:
    """Best-effort CDN invalidation for Cloudinary type=fetch derivatives.

    PhotoBox tiles are Cloudinary *fetch* of the R2 public URL. destroy() must
    use type=\"fetch\" and that source URL — default type=upload silently no-ops.
    Fail-open: CDN misses are logged; callers still delete R2.
    """
    if not r2_public_url:
        return True
    try:
        import cloudinary.uploader  # noqa: PLC0415

        cloudinary.uploader.destroy(
            r2_public_url,
            type="fetch",
            invalidate=True,
            resource_type="image",
        )
        return True
    except Exception as exc:  # pragma: no cover - network / SDK
        logger.warning(
            "[ASSET PURGE] Cloudinary fetch invalidate failed for %s: %s",
            r2_public_url,
            exc,
        )
        return False


def purge_photo_asset_keys(
    r2_object_key: Optional[str] = None,
    web_r2_object_key: Optional[str] = None,
    r2_public_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Delete R2 objects (fail-closed) and invalidate Cloudinary fetch (fail-open)."""
    keys = [key for key in (r2_object_key, web_r2_object_key) if key]
    fetch_url = r2_public_url
    if not fetch_url and web_r2_object_key:
        fetch_url = build_r2_public_url(web_r2_object_key)

    r2_ok = delete_r2_objects(keys) if keys else True
    invalidate_cloudinary_fetch(fetch_url)

    if not r2_ok:
        raise RuntimeError("R2 delete_objects failed during asset purge.")

    return {
        "status": "purged",
        "r2_keys": keys,
        "r2_public_url": fetch_url,
    }


def enqueue_purge_photo_assets(
    r2_object_key: Optional[str] = None,
    web_r2_object_key: Optional[str] = None,
    r2_public_url: Optional[str] = None,
) -> None:
    """Schedule purge after the surrounding DB transaction commits."""
    transaction.on_commit(
        lambda k=r2_object_key, w=web_r2_object_key, u=r2_public_url: (
            purge_photo_assets_task.delay(k, w, u)
        )
    )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="gallery.tasks.purge_photo_assets",
    queue="default",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def purge_photo_assets_task(
    self,
    r2_object_key: Optional[str] = None,
    web_r2_object_key: Optional[str] = None,
    r2_public_url: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        return purge_photo_asset_keys(
            r2_object_key=r2_object_key,
            web_r2_object_key=web_r2_object_key,
            r2_public_url=r2_public_url,
        )
    except Exception as exc:
        return retry_or_return(
            self,
            exc,
            fallback={
                "status": "error",
                "r2_object_key": r2_object_key,
                "web_r2_object_key": web_r2_object_key,
            },
        )


# Alias matching plan / call-site naming.
purge_photo_assets = purge_photo_assets_task
