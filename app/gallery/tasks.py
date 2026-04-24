# gallery/tasks.py
"""
Async Processing Pipeline — Fast Lane Asset Monitor.

Architecture: Presigned URL Flow
  1. Client requests upload ticket (POST /fast-lane/photos/)
  2. View creates Photo(status=PENDING), returns presigned POST URL
  3. Client uploads directly to R2
  4. Cloudflare fires R2 webhook → Photo transitions to READY
  5. THIS TASK: wakes 15 min later as a safety net
     - If READY/QUARANTINED: webhook succeeded, exit cleanly
     - If still PENDING + file in R2: webhook dropped, self-heal
     - If still PENDING + no file: abandoned upload, refund quota

Security invariants enforced here:
  - Quota refund is atomic with photo deletion (single transaction)
  - Refund is clamped to >= 0 (no negative storage attacks)
  - Refund only fires once (select_for_update + status check inside tx)
  - Key reconstruction sanitises filename before use
  - MaxRetriesExceededError triggers cleanup, not silent drop
"""
import logging
import re
import uuid
from typing import Any, Dict, Optional

from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.core.exceptions import ValidationError as DjangoValidationError
from django.conf import settings
from django.db import OperationalError, transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest
from gallery.models import Photo
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — never hardcode policy values inline
# ---------------------------------------------------------------------------

# How long (seconds) before we probe for an abandoned upload.
# Must be > client upload timeout. Default: 15 minutes.
FAST_LANE_PROBE_DELAY: int = getattr(settings, "FAST_LANE_MONITOR_DELAY_SECONDS", 900)

# Maximum filename length we accept when reconstructing R2 keys.
_MAX_FILENAME_LEN = 255

# Only allow safe characters in filenames used inside R2 keys.
# Rejects path traversal, null bytes, shell metacharacters.
_SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_\-. ]+$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sanitise_filename(raw: Optional[str]) -> Optional[str]:
    """
    Validate and sanitise a filename before embedding it in an R2 key.

    Rejects:
      - Path traversal sequences (../, ..\\)
      - Null bytes
      - Shell metacharacters
      - Filenames exceeding OS path limits

    Returns None if the filename is unsafe — caller must treat as missing.
    """
    if not raw:
        return None

    # Null byte injection guard
    if "\x00" in raw:
        logger.warning("[FAST LANE] Null byte in filename — rejecting.")
        return None

    # Strip leading/trailing whitespace
    cleaned = raw.strip()

    # Length guard
    if len(cleaned) > _MAX_FILENAME_LEN:
        logger.warning("[FAST LANE] Filename exceeds max length — rejecting.")
        return None

    # Path traversal guard — reject before any regex
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        logger.warning("[FAST LANE] Path traversal attempt in filename: %s", cleaned)
        return None

    # Allowlist: only safe characters
    if not _SAFE_FILENAME_RE.match(cleaned):
        logger.warning("[FAST LANE] Unsafe characters in filename: %s", cleaned)
        return None

    return cleaned


def _build_expected_r2_key(
    workspace_id: Any,
    photo_id: str,
    original_filename: Optional[str],
) -> Optional[str]:
    """
    Reconstruct the expected R2 object key for a fast-lane photo.

    Returns None if the filename is unsafe — the caller must treat
    a None key as "cannot verify" and abort rather than probe.

    Key format: fast-lane/tenant_{workspace_id}/{photo_id}/{sanitised_filename}
    """
    safe_name = _sanitise_filename(original_filename)
    if not safe_name:
        return None
    return f"fast-lane/tenant_{workspace_id}/{photo_id}/{safe_name}"


# ---------------------------------------------------------------------------
# Task aliases — both names resolve to the same implementation so that
# existing Celery beat schedules, test imports, and the view's .apply_async()
# call all work without a redeploy.
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    queue="image-processing",
    # Explicit name survives module renames / refactors
    name="gallery.tasks.process_fast_lane_asset",
    # Re-queue if the worker crashes mid-execution
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_fast_lane_asset(self, photo_id: str) -> Dict[str, Any]:
    """
    Fast Lane Asset Monitor — runs 15 minutes after upload dispatch.

    See module docstring for full flow description.
    """
    # Local imports break circular dependency:
    # gallery.models → gallery.tasks would create an import cycle at startup.
    from gallery.models import Photo          # noqa: PLC0415
    from gallery.storage import get_r2_client # noqa: PLC0415

    logger.info("[FAST LANE MONITOR] Checking vault status for photo_id=%s", photo_id)

    try:
        uuid.UUID(str(photo_id))
    except (ValueError, TypeError, AttributeError):
        logger.warning(
            "[FAST LANE MONITOR] Invalid photo_id received. Rejecting without DB mutation: %r",
            photo_id,
        )
        return {"status": "error", "reason": "invalid_photo_id", "photo_id": str(photo_id)}

    # ------------------------------------------------------------------
    # STEP 1: Fetch the photo record WITHOUT a database lock.
    # We drop the select_for_update() here because locking the row 
    # while we make slow network calls to R2 destroys database concurrency.
    # Downstream helpers will apply locks when mutation is actually needed.
    # ------------------------------------------------------------------
    try:
        photo = (
            Photo.objects
            .select_related("scene__event__workspace")
            .get(id=photo_id)
        )

        # ----------------------------------------------------------
        # STEP 2: Idempotency check 
        # ----------------------------------------------------------
        if photo.status in ("READY", "QUARANTINED") or photo.is_processed:
            logger.info(
                "[FAST LANE MONITOR] Photo %s already vaulted (status=%s). Idempotent skip.",
                photo_id,
                photo.status,
            )
            return {"status": "already_processed", "photo_id": photo_id}

        # Snapshot immutable values before any mutation
        workspace = photo.scene.event.workspace
        file_size_bytes: int = photo.file_size_bytes or 0
        stored_key: Optional[str] = photo.r2_object_key or None

    except (Photo.DoesNotExist, DjangoValidationError):
        # Photo was deleted between dispatch and execution (e.g. user deleted event).
        # Quota was already refunded at delete time by the view's delete handler.
        logger.warning(
            "[FAST LANE MONITOR] Photo %s not found — already deleted. No quota action needed.",
            photo_id,
        )
        return {"status": "skipped", "reason": "not_found"}

    # ------------------------------------------------------------------
    # STEP 3: Determine the R2 key to probe.
    #
    # Priority:
    #   1. The key stored on the photo record (set during Fast Lane upload)
    #   2. Reconstructed key from workspace + photo_id + sanitised filename
    #
    # If neither is available, we cannot safely probe — abort.
    # ------------------------------------------------------------------
    probe_key = stored_key or _build_expected_r2_key(
        workspace.id,
        photo_id,
        photo.original_filename,
    )

    if not probe_key:
        logger.error(
            "[FAST LANE MONITOR] Cannot determine R2 key for photo %s "
            "(no stored key and filename is unsafe). Marking FAILED.",
            photo_id,
        )
        # Mark failed so the record is not invisible in the admin
        Photo.objects.filter(id=photo_id, status="PENDING").update(status="FAILED")
        return {"status": "error", "reason": "no_safe_r2_key", "photo_id": photo_id}

    # ------------------------------------------------------------------
    # STEP 4: Probe R2
    # ------------------------------------------------------------------
    try:
        r2 = get_r2_client()
        r2.head_object(
            Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME,
            Key=probe_key,
        )
        # head_object succeeded → file IS in R2

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")

        if error_code in ("404", "NoSuchKey", "Not Found"):
            # ----------------------------------------------------------
            # SCENARIO B: File never reached R2 — abandoned upload.
            # Atomically delete the photo AND refund the quota in ONE
            # transaction so there is no window for a double-refund.
            # ----------------------------------------------------------
            return _handle_abandoned_upload(photo_id, workspace.id, file_size_bytes)

        # Any other ClientError (403 Forbidden, 5xx, throttle) is transient.
        logger.error(
            "[FAST LANE MONITOR] R2 API error probing photo %s (code=%s): %s. Retrying.",
            photo_id,
            error_code,
            exc,
        )
        return _retry_or_fail(self, photo_id, exc)

    except (BotoCoreError, OSError) as exc:
        # Network-level failure — R2 is unreachable
        logger.error(
            "[FAST LANE MONITOR] Network error probing photo %s: %s. Retrying.",
            photo_id,
            exc,
        )
        return _retry_or_fail(self, photo_id, exc)

    except Exception as exc:
        # Catch-all for genuinely unexpected errors (programming bugs etc.)
        # Log with full traceback so we can debug — do NOT silently retry.
        logger.exception(
            "[FAST LANE MONITOR] Unexpected error probing photo %s: %s",
            photo_id,
            exc,
        )
        Photo.objects.filter(id=photo_id, status="PENDING").update(status="FAILED")
        return {"status": "error", "reason": "unexpected", "photo_id": photo_id}

    # ------------------------------------------------------------------
    # SCENARIO A: File IS in R2 but webhook was dropped / never arrived.
    # Self-heal: transition PENDING → READY inside a single atomic block.
    # ------------------------------------------------------------------
    return _handle_self_heal(photo_id, probe_key)


# ---------------------------------------------------------------------------
# Private helpers — extracted so the main task reads like a flow chart
# ---------------------------------------------------------------------------

def _handle_self_heal(photo_id: str, confirmed_key: str) -> Dict[str, Any]:
    """
    Webhook was dropped but file arrived in R2.
    Atomically mark READY. Never overwrites confirmed data with None.
    """
    logger.warning(
        "[FAST LANE MONITOR] SELF-HEAL: webhook dropped for photo %s. "
        "File confirmed in R2 at key=%s. Forcing READY.",
        photo_id,
        confirmed_key,
    )

    with transaction.atomic():
        updated = Photo.objects.filter(  # noqa: F841 (import at top of file)
            id=photo_id,
            status="PENDING",  # guard: only transition from PENDING
        ).update(
            r2_object_key=confirmed_key,
            is_processed=True,
            status="READY",
            # DO NOT set optimized_url=None — preserve any existing CDN URL
        )

    if updated == 0:
        # Webhook arrived between our probe and our update — that's fine
        logger.info(
            "[FAST LANE MONITOR] Self-heal for %s had no effect — "
            "webhook likely arrived concurrently. No action needed.",
            photo_id,
        )

    return {"status": "self_healed", "photo_id": photo_id}


def _handle_abandoned_upload(
    photo_id: str,
    workspace_id: Any,
    file_size_bytes: int,
) -> Dict[str, Any]:
    """
    File never reached R2. Delete the photo record and refund quota.

    Security invariants:
      1. Both operations are in ONE atomic transaction — no window for double refund.
      2. Refund is clamped to >= 0 — prevents negative storage_used_bytes.
      3. select_for_update inside the transaction re-checks status —
         prevents a race where the webhook arrives between probe and delete.
    """
    from gallery.models import Photo          # noqa: PLC0415

    logger.info(
        "[FAST LANE MONITOR] Abandoned upload for photo %s. "
        "Refunding %d bytes to workspace %s.",
        photo_id,
        file_size_bytes,
        workspace_id,
    )

    with transaction.atomic():
        # Re-acquire lock inside this transaction
        try:
            photo = Photo.objects.select_for_update(nowait=True).get(
                id=photo_id,
                status="PENDING",  # only delete if still PENDING
            )
        except Photo.DoesNotExist:
            # Webhook arrived between probe and now — photo is READY.
            # Do NOT refund. The upload succeeded.
            logger.info(
                "[FAST LANE MONITOR] Photo %s no longer PENDING during "
                "abandoned-upload handler. Webhook succeeded. No refund.",
                photo_id,
            )
            return {"status": "skipped", "reason": "webhook_arrived"}
        except OperationalError:
            # Lock contention — webhook is processing right now.
            # Treat as webhook success to be safe.
            logger.warning(
                "[FAST LANE MONITOR] Lock contention on photo %s during "
                "abandoned-upload handler. Assuming webhook in progress. No refund.",
                photo_id,
            )
            return {"status": "skipped", "reason": "lock_contention"}

        refund_bytes: int = photo.file_size_bytes or 0
        photo.delete()

        # Clamp: storage_used_bytes must never go below zero.
        # GREATEST(storage_used_bytes - refund, 0)
        from core.models import Workspace  # noqa: PLC0415
        Workspace.objects.filter(id=workspace_id).update(
            storage_used_bytes=Greatest(
                Value(0),
                F("storage_used_bytes") - refund_bytes,
            )
        )

    logger.info(
        "[FAST LANE MONITOR] Refunded %d bytes to workspace %s for abandoned photo %s.",
        refund_bytes,
        workspace_id,
        photo_id,
    )
    return {
        "status": "abandoned_and_refunded",
        "photo_id": photo_id,
        "bytes_refunded": refund_bytes,
    }


def _retry_or_fail(
    task_instance: Any,
    photo_id: str,
    exc: Exception,
) -> Dict[str, Any]:
    """
    Attempt a retry. If max retries exhausted, mark the photo FAILED
    so it is visible in the admin and does not stay PENDING indefinitely.

    A photo stuck in PENDING forever is invisible to operators and
    causes quota to be permanently locked — treat exhausted retries as
    a hard failure requiring manual review.
    """
    from gallery.models import Photo  # noqa: PLC0415

    try:
        raise task_instance.retry(exc=exc)
    except MaxRetriesExceededError:
        logger.error(
            "[FAST LANE MONITOR] Max retries exceeded for photo %s. "
            "Marking FAILED. Manual investigation required. Error: %s",
            photo_id,
            exc,
        )
        # Mark FAILED so the photo is not invisible in the admin
        Photo.objects.filter(id=photo_id, status="PENDING").update(status="FAILED")
        return {
            "status": "error",
            "reason": "max_retries_exceeded",
            "photo_id": photo_id,
        }


# ---------------------------------------------------------------------------
# Alias — test_celery_tasks.py imports upload_photo_to_r2
# Both names point to the same implementation.
# ---------------------------------------------------------------------------
upload_photo_to_r2 = process_fast_lane_asset


@shared_task(
    bind=True,
    name="gallery.tasks.prepare_gallery_bulk_download",
    acks_late=True,
    reject_on_worker_lost=True,
)
def prepare_gallery_bulk_download(
    self,
    event_id: str,
    requested_by_user_id: Optional[str] = None,
    requester_kind: str = "photographer",
) -> Dict[str, Any]:
    """
    Stable Celery entry point for asynchronous gallery archive preparation.

    The actual archive assembly pipeline can evolve behind this task name
    without breaking views, tests, or beat schedules that enqueue bulk
    download work.
    """
    logger.info(
        "[BULK DOWNLOAD] Accepted archive preparation request for event_id=%s requester_kind=%s requested_by_user_id=%s",
        event_id,
        requester_kind,
        requested_by_user_id,
    )
    return {
        "status": "queued",
        "event_id": event_id,
        "requester_kind": requester_kind,
        "requested_by_user_id": requested_by_user_id,
    }
