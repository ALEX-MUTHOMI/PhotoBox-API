"""
gallery/tasks.py — Async Processing Pipeline: The Unified Vault Pattern

ARCHITECTURE CONTRACT:
  The Django web thread does ZERO I/O-bound work.
  All binary data flows: Browser → R2 directly (Heavy Lane)
                      OR Django local disk → R2 via this Celery task (Fast Lane).

  Cloudinary is demoted to a CDN Fetch Proxy. It receives NO SDK uploads.
  The Photo.delivery_url property constructs the Cloudinary Fetch URL from the R2 key.
"""
import logging
from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.conf import settings
from django.db import transaction
from django.db.models import F

from gallery.storage import get_r2_client, infer_content_type

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,  # 30s between retries — gives R2 time to recover from transient errors
    name='gallery.tasks.process_fast_lane_asset',
)
def process_fast_lane_asset(self, photo_id: str):
    """
    PILLAR 2: Unified Vault Asset Processor (Fast Lane).

    Called by PhotoFastLaneViewSet.perform_create() immediately AFTER the web thread
    has returned 202 Accepted. The photographer's HTTP connection is already closed.

    Execution contract:
      1. Fetch Photo from DB (with select_related to avoid N+1).
      2. Stream bytes from local Django storage → Cloudflare R2.
      3. Persist the R2 object key to photo.r2_object_key.
      4. Atomically flip is_processed=True + status='READY'.
      5. On MaxRetriesExceededError: mark FAILED, atomically refund quota.
    """
    from gallery.models import Photo
    from core.models import Workspace

    logger.info(f"[FAST LANE] Starting R2 vault for photo {photo_id}")

    try:
        photo = Photo.objects.select_related(
            'scene__event__workspace'
        ).get(id=photo_id)
    except Photo.DoesNotExist:
        # Deleted between accept and task execution — safe to discard
        logger.warning(f"[FAST LANE] Photo {photo_id} does not exist. Task discarded.")
        return

    # IDEMPOTENCY: Guard against duplicate executions (retry races, duplicate broker delivery)
    if photo.is_processed and photo.r2_object_key:
        logger.info(f"[FAST LANE] Photo {photo_id} already vaulted. Idempotent skip.")
        return

    workspace = photo.scene.event.workspace
    bucket = settings.CLOUDFLARE_R2_BUCKET_NAME

    # Tenant-isolated R2 key — enforces storage separation at the object level
    object_key = (
        f"fast-lane/tenant_{workspace.id}/{photo_id}/{photo.original_filename}"
    )

    try:
        if not photo.image_file:
            raise ValueError(
                f"Photo {photo_id} has no image_file on disk. Cannot stream to R2."
            )

        r2 = get_r2_client()

        # Stream local file bytes directly to R2.
        # upload_fileobj uses multipart upload internally for large files.
        # For Fast Lane (≤5MB), it is a single PUT — no overhead.
        with open(photo.image_file.path, 'rb') as file_obj:
            r2.upload_fileobj(
                file_obj,
                bucket,
                object_key,
                ExtraArgs={
                    'ContentType': infer_content_type(photo.original_filename),
                    # Storage-layer metadata for forensic audit and replay detection
                    'Metadata': {
                        'photo-id':         str(photo.id),
                        'workspace-id':     str(workspace.id),
                        'original-filename': photo.original_filename,
                    },
                },
            )

        # ATOMIC DB COMMIT: The photo is in the vault.
        # optimized_url is set to None — delivery_url property derives from r2_object_key.
        with transaction.atomic():
            Photo.objects.filter(id=photo_id).update(
                r2_object_key=object_key,
                is_processed=True,
                status='READY',
                optimized_url=None,
            )

        logger.info(
            f"[FAST LANE] ✅ Photo {photo_id} vaulted to R2. "
            f"Key: {object_key}"
        )

    except MaxRetriesExceededError:
        # PERMANENT FAILURE: All 3 retries exhausted.
        # We MUST refund the quota atomically or the photographer loses storage permanently.
        logger.critical(
            f"[FAST LANE] 🔴 Permanent failure for {photo_id} after {self.max_retries} retries. "
            f"Refunding {photo.file_size_bytes} bytes to workspace {workspace.id}."
        )
        with transaction.atomic():
            Photo.objects.filter(id=photo_id).update(
                status='FAILED',
                is_processed=False,
            )
            Workspace.objects.filter(id=workspace.id).update(
                storage_used_bytes=F('storage_used_bytes') - photo.file_size_bytes
            )
        # Do NOT re-raise — permanently dead; Celery must not retry again.

    except (BotoCoreError, ClientError) as exc:
        # TRANSIENT FAILURE: R2 API error (503, throttle, network blip).
        # autoretry_for is not used here to give explicit control over exception types.
        logger.error(
            f"[FAST LANE] ❌ R2 API error for {photo_id} "
            f"(attempt {self.request.retries + 1}/{self.max_retries + 1}): {exc}"
        )
        raise self.retry(exc=exc)

    except Exception as exc:
        # TRANSIENT FAILURE: Unexpected error (disk read failure, etc.)
        logger.error(
            f"[FAST LANE] ❌ Unexpected error for {photo_id} "
            f"(attempt {self.request.retries + 1}/{self.max_retries + 1}): {exc}"
        )
        raise self.retry(exc=exc)
