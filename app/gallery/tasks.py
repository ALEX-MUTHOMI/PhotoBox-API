# gallery/tasks.py
"""
Async processing for gallery assets and gallery archives.
"""
import logging
import os
import re
import tempfile
import uuid
import zipfile
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Any, Dict, Optional

from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.conf import settings
from django.db import OperationalError, transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.utils import timezone
from django.utils.text import slugify

from gallery.models import Event, GalleryArchiveJob, Photo, VisibilityChoices

logger = logging.getLogger(__name__)

FAST_LANE_PROBE_DELAY: int = getattr(settings, "FAST_LANE_MONITOR_DELAY_SECONDS", 900)
ARCHIVE_STREAM_CHUNK_SIZE = 1024 * 1024
ARCHIVE_TTL_HOURS = int(getattr(settings, "GALLERY_ARCHIVE_TTL_HOURS", 24))

_MAX_FILENAME_LEN = 255
_SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_\-. ]+$")


def _sanitise_filename(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None

    if "\x00" in raw:
        logger.warning("[FAST LANE] Null byte in filename, rejecting.")
        return None

    cleaned = raw.strip()
    if len(cleaned) > _MAX_FILENAME_LEN:
        logger.warning("[FAST LANE] Filename exceeds max length, rejecting.")
        return None

    if ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        logger.warning("[FAST LANE] Path traversal attempt in filename: %s", cleaned)
        return None

    if not _SAFE_FILENAME_RE.match(cleaned):
        logger.warning("[FAST LANE] Unsafe characters in filename: %s", cleaned)
        return None

    return cleaned


def _build_expected_r2_key(
    workspace_id: Any,
    photo_id: str,
    original_filename: Optional[str],
) -> Optional[str]:
    safe_name = _sanitise_filename(original_filename)
    if not safe_name:
        return None
    return f"fast-lane/tenant_{workspace_id}/{photo_id}/{safe_name}"


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="gallery.tasks.process_fast_lane_asset",
    acks_late=True,
    reject_on_worker_lost=True,
    queue="image-processing",
)
def process_fast_lane_asset(self, photo_id: str) -> Dict[str, Any]:
    from gallery.models import Photo  # noqa: PLC0415
    from gallery.storage import get_r2_client  # noqa: PLC0415

    logger.info("[FAST LANE MONITOR] Checking vault status for photo_id=%s", photo_id)

    try:
        normalized_photo_id = str(uuid.UUID(str(photo_id)))
    except (TypeError, ValueError, AttributeError):
        logger.warning(
            "[FAST LANE MONITOR] Rejected invalid photo identifier: %s",
            photo_id,
        )
        return {
            "status": "rejected",
            "reason": "invalid_photo_id",
            "photo_id": str(photo_id),
        }

    try:
        photo = (
            Photo.objects
            .select_related("scene__event__workspace")
            .get(id=normalized_photo_id)
        )

        if photo.status in ("READY", "QUARANTINED") or photo.is_processed:
            logger.info(
                "[FAST LANE MONITOR] Photo %s already processed (status=%s).",
                normalized_photo_id,
                photo.status,
            )
            return {"status": "already_processed", "photo_id": normalized_photo_id}

        workspace = photo.scene.event.workspace
        file_size_bytes: int = photo.file_size_bytes or 0
        stored_key: Optional[str] = photo.r2_object_key or None
    except Photo.DoesNotExist:
        logger.warning(
            "[FAST LANE MONITOR] Photo %s not found, already deleted.",
            normalized_photo_id,
        )
        return {"status": "skipped", "reason": "not_found"}

    probe_key = stored_key or _build_expected_r2_key(
        workspace.id,
        normalized_photo_id,
        photo.original_filename,
    )

    if not probe_key:
        logger.error(
            "[FAST LANE MONITOR] Cannot determine R2 key for photo %s. Marking FAILED.",
            normalized_photo_id,
        )
        Photo.objects.filter(id=normalized_photo_id, status="PENDING").update(status="FAILED")
        return {
            "status": "error",
            "reason": "no_safe_r2_key",
            "photo_id": normalized_photo_id,
        }

    try:
        r2 = get_r2_client()
        r2.head_object(
            Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME,
            Key=probe_key,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey", "Not Found"):
            return _handle_abandoned_upload(photo_id, workspace.id, file_size_bytes)

        logger.error(
            "[FAST LANE MONITOR] R2 API error probing photo %s (code=%s): %s",
            normalized_photo_id,
            error_code,
            exc,
        )
        return _retry_or_fail(self, normalized_photo_id, exc)
    except (BotoCoreError, OSError) as exc:
        logger.error(
            "[FAST LANE MONITOR] Network error probing photo %s: %s",
            normalized_photo_id,
            exc,
        )
        return _retry_or_fail(self, normalized_photo_id, exc)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception(
            "[FAST LANE MONITOR] Unexpected error probing photo %s: %s",
            normalized_photo_id,
            exc,
        )
        Photo.objects.filter(id=normalized_photo_id, status="PENDING").update(status="FAILED")
        return {
            "status": "error",
            "reason": "unexpected",
            "photo_id": normalized_photo_id,
        }

    return _handle_self_heal(normalized_photo_id, probe_key)


def _handle_self_heal(photo_id: str, confirmed_key: str) -> Dict[str, Any]:
    logger.warning(
        "[FAST LANE MONITOR] SELF-HEAL for photo %s. File confirmed in R2 at key=%s.",
        photo_id,
        confirmed_key,
    )

    with transaction.atomic():
        updated = Photo.objects.filter(
            id=photo_id,
            status="PENDING",
        ).update(
            r2_object_key=confirmed_key,
            is_processed=True,
            status="READY",
        )

    if updated == 0:
        logger.info(
            "[FAST LANE MONITOR] Self-heal for %s had no effect; webhook likely won the race.",
            photo_id,
        )

    return {"status": "self_healed", "photo_id": photo_id}


def _handle_abandoned_upload(
    photo_id: str,
    workspace_id: Any,
    file_size_bytes: int,
) -> Dict[str, Any]:
    from core.models import Workspace  # noqa: PLC0415
    from gallery.models import Photo  # noqa: PLC0415

    logger.info(
        "[FAST LANE MONITOR] Abandoned upload for photo %s. Refunding %d bytes.",
        photo_id,
        file_size_bytes,
    )

    with transaction.atomic():
        try:
            photo = Photo.objects.select_for_update(nowait=True).get(
                id=photo_id,
                status="PENDING",
            )
        except Photo.DoesNotExist:
            logger.info(
                "[FAST LANE MONITOR] Photo %s no longer pending during refund check.",
                photo_id,
            )
            return {"status": "skipped", "reason": "webhook_arrived"}
        except OperationalError:
            logger.warning(
                "[FAST LANE MONITOR] Lock contention on photo %s; assuming webhook in progress.",
                photo_id,
            )
            return {"status": "skipped", "reason": "lock_contention"}

        refund_bytes: int = photo.file_size_bytes or 0
        photo.delete()

        Workspace.objects.filter(id=workspace_id).update(
            storage_used_bytes=Greatest(
                Value(0),
                F("storage_used_bytes") - refund_bytes,
            )
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
    from gallery.models import Photo  # noqa: PLC0415

    try:
        raise task_instance.retry(exc=exc)
    except MaxRetriesExceededError:
        logger.error(
            "[FAST LANE MONITOR] Max retries exceeded for photo %s. Marking FAILED.",
            photo_id,
        )
        Photo.objects.filter(id=photo_id, status="PENDING").update(status="FAILED")
        return {
            "status": "error",
            "reason": "max_retries_exceeded",
            "photo_id": photo_id,
        }


def _safe_archive_component(value: Optional[str], fallback: str) -> str:
    cleaned = slugify((value or "").strip())[:80]
    return cleaned or fallback


def _build_archive_entry_name(photo: Photo) -> str:
    raw_name = os.path.basename(photo.original_filename or f"{photo.id}.bin")
    safe_filename = _sanitise_filename(raw_name) or f"{photo.id}.bin"
    stem, ext = os.path.splitext(safe_filename)
    scene_name = _safe_archive_component(photo.scene.title, "scene")
    asset_name = f"{_safe_archive_component(stem, 'asset')}-{str(photo.id)[:8]}{ext.lower()}"
    return str(PurePosixPath(scene_name) / asset_name)


def _build_archive_r2_key(job: GalleryArchiveJob) -> str:
    return (
        f"archives/tenant_{job.gallery.workspace_id}/"
        f"gallery_{job.gallery_id}/{job.id}.zip"
    )


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="gallery.tasks.build_gallery_archive",
    acks_late=True,
    reject_on_worker_lost=True,
)
def build_gallery_archive(self, archive_job_id: str) -> Dict[str, Any]:
    from gallery.models import GalleryArchiveJob, Photo  # noqa: PLC0415
    from gallery.storage import get_r2_client, upload_local_file_to_r2  # noqa: PLC0415

    try:
        job = GalleryArchiveJob.objects.select_related("gallery__workspace").get(id=archive_job_id)
    except GalleryArchiveJob.DoesNotExist:
        logger.warning("[ARCHIVE] Job %s does not exist.", archive_job_id)
        return {"status": "missing", "archive_job_id": archive_job_id}

    if job.status == GalleryArchiveJob.Status.COMPLETED and job.r2_zip_key:
        return {
            "status": "already_completed",
            "archive_job_id": archive_job_id,
            "r2_zip_key": job.r2_zip_key,
        }

    if job.status == GalleryArchiveJob.Status.PROCESSING:
        return {"status": "processing", "archive_job_id": archive_job_id}

    temp_zip_path = None
    try:
        GalleryArchiveJob.objects.filter(id=job.id).update(
            status=GalleryArchiveJob.Status.PROCESSING
        )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as temp_zip:
            temp_zip_path = temp_zip.name

        r2_client = get_r2_client()
        with zipfile.ZipFile(
            temp_zip_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as archive_file:
            photos = (
                Photo.objects
                .select_related("scene")
                .filter(
                    scene__event=job.gallery,
                    status="READY",
                    visibility__in=[
                        VisibilityChoices.PUBLIC,
                        VisibilityChoices.CLIENT_ONLY,
                    ],
                )
                .exclude(r2_object_key__isnull=True)
                .exclude(r2_object_key="")
                .order_by("scene__display_order", "uploaded_at")
                .iterator()
            )

            for photo in photos:
                response = r2_client.get_object(
                    Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME,
                    Key=photo.r2_object_key,
                )
                with response["Body"] as source_stream, archive_file.open(
                    _build_archive_entry_name(photo),
                    mode="w",
                    force_zip64=True,
                ) as zip_entry:
                    while True:
                        chunk = source_stream.read(ARCHIVE_STREAM_CHUNK_SIZE)
                        if not chunk:
                            break
                        zip_entry.write(chunk)

        zip_key = _build_archive_r2_key(job)
        uploaded = upload_local_file_to_r2(
            temp_zip_path,
            zip_key,
            content_type="application/zip",
        )
        if not uploaded:
            raise RuntimeError("Archive ZIP upload to R2 failed.")

        expiry = timezone.now() + timedelta(hours=ARCHIVE_TTL_HOURS)
        GalleryArchiveJob.objects.filter(id=job.id).update(
            status=GalleryArchiveJob.Status.COMPLETED,
            r2_zip_key=zip_key,
            expires_at=expiry,
        )
        return {
            "status": "completed",
            "archive_job_id": archive_job_id,
            "r2_zip_key": zip_key,
        }
    except Exception as exc:
        logger.exception("[ARCHIVE] Archive build failed for job %s: %s", archive_job_id, exc)
        GalleryArchiveJob.objects.filter(id=job.id).update(
            status=GalleryArchiveJob.Status.FAILED
        )
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            return {
                "status": "error",
                "reason": "max_retries_exceeded",
                "archive_job_id": archive_job_id,
            }
    finally:
        if temp_zip_path and os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)


@shared_task(
    bind=True,
    max_retries=0,
    name="gallery.tasks.prepare_gallery_bulk_download",
)
def prepare_gallery_bulk_download(
    self,
    event_id: str,
    requested_by_user_id: Optional[str] = None,
    requester_kind: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        gallery = Event.objects.get(id=event_id)
    except Event.DoesNotExist:
        logger.warning(
            "[ARCHIVE] Bulk download requested for missing gallery %s by %s (%s).",
            event_id,
            requested_by_user_id,
            requester_kind,
        )
        return {"status": "missing", "gallery_id": event_id}

    active_job = (
        GalleryArchiveJob.objects
        .filter(gallery=gallery)
        .order_by("-created_at")
        .first()
    )
    if active_job and active_job.status in (
        GalleryArchiveJob.Status.PENDING,
        GalleryArchiveJob.Status.PROCESSING,
    ):
        return {
            "status": active_job.status.lower(),
            "gallery_id": str(gallery.id),
            "archive_job_id": str(active_job.id),
        }

    if (
        active_job
        and active_job.status == GalleryArchiveJob.Status.COMPLETED
        and active_job.r2_zip_key
        and active_job.expires_at
        and active_job.expires_at > timezone.now()
    ):
        return {
            "status": "already_completed",
            "gallery_id": str(gallery.id),
            "archive_job_id": str(active_job.id),
            "r2_zip_key": active_job.r2_zip_key,
        }

    archive_job = GalleryArchiveJob.objects.create(gallery=gallery)
    build_result = build_gallery_archive.delay(str(archive_job.id))
    logger.info(
        "[ARCHIVE] Queued gallery archive job %s for gallery %s by %s (%s).",
        archive_job.id,
        gallery.id,
        requested_by_user_id,
        requester_kind,
    )
    return {
        "status": "queued",
        "gallery_id": str(gallery.id),
        "archive_job_id": str(archive_job.id),
        "build_task_id": getattr(build_result, "id", None),
    }


process_fast_lane_asset.queue = "image-processing"
upload_photo_to_r2 = process_fast_lane_asset
