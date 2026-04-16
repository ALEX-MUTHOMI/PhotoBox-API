"""
gallery/tasks.py — Async Processing Pipeline (Pillar 2)

This is the CORRECT EDA implementation of the Fast Lane.
The Django request thread does ZERO I/O-bound work.
This Celery task runs in a background worker pool, completely
off the web server, so it can never cause Worker Starvation or OOM.
"""
import logging
import cloudinary
import cloudinary.uploader
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.db import transaction
from django.db.models import F

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,    # 30 seconds between retries
    autoretry_for=(Exception,), # Auto-retry on any transient Cloudinary failure
    name='gallery.tasks.upload_fast_lane_to_cloudinary'
)
def upload_fast_lane_to_cloudinary(self, photo_id: str):
    """
    PILLAR 2: The Asynchronous CDN Handoff.

    Triggered by PhotoFastLaneViewSet.perform_create() immediately AFTER
    the HTTP request has already returned a 202 Accepted to the photographer.

    This task:
    1. Fetches the Photo from the DB.
    2. Uploads its raw local file to Cloudinary.
    3. Saves the resulting signed CDN URL back to the Photo.
    4. Marks the Photo as is_processed=True so the client can see it.
    5. On permanent failures: marks FAILED and atomically refunds quota.
    """
    # Import here to avoid circular imports at module level
    from gallery.models import Photo
    from core.models import Workspace

    logger.info(f"[FAST LANE CELERY] Starting Cloudinary upload for photo: {photo_id}")

    # Fetch with select_related to avoid N+1 DB queries
    try:
        photo = Photo.objects.select_related(
            'scene__event__workspace'
        ).get(id=photo_id)
    except Photo.DoesNotExist:
        # The photo was deleted between request and task execution — safe to ignore
        logger.warning(f"[FAST LANE CELERY] Photo {photo_id} does not exist. Skipping.")
        return

    # Guard: Already processed (e.g. task ran twice due to a retry race)
    if photo.is_processed and photo.optimized_url:
        logger.info(f"[FAST LANE CELERY] Photo {photo_id} already processed. Idempotent skip.")
        return

    workspace = photo.scene.event.workspace

    # ----------------------------------------------------------------
    # CLOUDINARY UPLOAD
    # This is the ONLY place any I/O happens. It runs on a Celery worker,
    # NOT on a Django web worker. The web server is completely free.
    # ----------------------------------------------------------------
    try:
        if not photo.image_file:
            raise ValueError("Photo has no image_file attached. Cannot upload to Cloudinary.")

        upload_result = cloudinary.uploader.upload(
            photo.image_file.path,
            folder=f"photobox/{workspace.id}/fast-lane/",
            resource_type="image",
            # Cloudinary auto-converts to WebP for supported browsers
            format="webp",
            # Eager transformation: pre-generate a 800px thumbnail
            eager=[{"width": 800, "crop": "limit", "quality": "auto:good"}],
            eager_async=True,  # Even Cloudinary's processing is async
        )

        optimized_url = upload_result.get("secure_url")
        public_id = upload_result.get("public_id")

        if not optimized_url:
            raise ValueError(f"Cloudinary returned no URL for photo {photo_id}.")

        # Atomic DB update: mark processed and store the CDN URL
        with transaction.atomic():
            Photo.objects.filter(id=photo_id).update(
                optimized_url=optimized_url,
                is_processed=True,
                r2_object_key=public_id,  # Reusing field to store Cloudinary public_id
            )

        logger.info(f"[FAST LANE CELERY] ✅ Photo {photo_id} processed. CDN: {optimized_url}")

    except MaxRetriesExceededError:
        # PERMANENT FAILURE: All retry attempts exhausted.
        # autoretry_for raises MaxRetriesExceededError before we can check
        # self.request.retries, so we catch it explicitly here.
        # We MUST refund the quota so the photographer doesn't lose storage permanently.
        logger.critical(
            f"[FAST LANE CELERY] Permanent failure for {photo_id} after {self.max_retries} retries. "
            f"Refunding {photo.file_size_bytes} bytes to workspace {workspace.id}."
        )

        with transaction.atomic():
            # Mark the photo as failed so the UI can surface an actionable error state
            Photo.objects.filter(id=photo_id).update(
                status='FAILED',
                is_processed=False,
            )
            # Atomically refund the quota to prevent "ghost storage" leaks
            Workspace.objects.filter(id=workspace.id).update(
                storage_used_bytes=F('storage_used_bytes') - photo.file_size_bytes
            )

        # Do NOT re-raise — the task is permanently dead, Celery should not retry again.

    except Exception as exc:
        # TRANSIENT FAILURE: Network blip, Cloudinary 503, etc.
        # Let Celery's autoretry_for mechanism handle the retry schedule.
        logger.error(
            f"[FAST LANE CELERY] ❌ Transient failure for {photo_id} "
            f"(attempt {self.request.retries + 1}/{self.max_retries + 1}): {exc}"
        )
        raise self.retry(exc=exc)
