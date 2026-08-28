# gallery/tasks.py
"""
Async processing for gallery assets and gallery archives.
"""
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Any, Dict, Optional
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.conf import settings
from django.db import OperationalError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image as PILImage
from PIL import ImageOps
from PIL import UnidentifiedImageError

PILImage.MAX_IMAGE_PIXELS = int(
    getattr(settings, "PHOTO_MAX_IMAGE_PIXELS", 89_478_485)
)

from core.quota import release_workspace_bytes
from gallery.filename_utils import sanitize_gallery_filename
from gallery.models import GalleryArchiveJob, GalleryArchiveType, Photo, VisibilityChoices

logger = logging.getLogger(__name__)

FAST_LANE_PROBE_DELAY: int = getattr(settings, "FAST_LANE_MONITOR_DELAY_SECONDS", 900)
ARCHIVE_STREAM_CHUNK_SIZE = 1024 * 1024
ARCHIVE_TTL_HOURS = int(getattr(settings, "GALLERY_ARCHIVE_TTL_HOURS", 24))
WEB_DERIVATIVE_MAX_DIMENSION = int(getattr(settings, "PHOTO_WEB_MAX_DIMENSION", 2400))
WEB_DERIVATIVE_QUALITY = int(getattr(settings, "PHOTO_WEB_QUALITY", 86))
WATERMARK_SCALE_RATIO = float(getattr(settings, "PHOTO_WATERMARK_SCALE_RATIO", 0.22))

def _sanitise_filename(raw: Optional[str]) -> Optional[str]:
    safe_filename = sanitize_gallery_filename(raw)
    if safe_filename is None and raw:
        logger.warning("[FAST LANE] Unsafe filename rejected: %s", raw)
    return safe_filename


def _normalise_photo_id(photo_id: Any) -> Optional[str]:
    try:
        return str(UUID(str(photo_id)))
    except (AttributeError, TypeError, ValueError):
        return None


def _build_expected_r2_key(
    workspace_id: Any,
    photo_id: str,
    original_filename: Optional[str],
) -> Optional[str]:
    safe_name = _sanitise_filename(original_filename)
    if not safe_name:
        return None
    return f"fast-lane/tenant_{workspace_id}/{photo_id}/{safe_name}"


def _build_web_derivative_r2_key(photo: Photo) -> str:
    return (
        f"web/tenant_{photo.scene.event.workspace_id}/"
        f"gallery_{photo.scene.event_id}/photo_{photo.id}.webp"
    )


def _apply_workspace_watermark(base_image: PILImage.Image, workspace) -> PILImage.Image:
    if not workspace.watermark_logo:
        return base_image.convert("RGB")

    with workspace.watermark_logo.open("rb") as watermark_file:
        try:
            with PILImage.open(watermark_file) as raw_logo:
                if raw_logo.format != "PNG":
                    raise RuntimeError("Watermark logo must be a valid PNG image.")
                watermark_logo = raw_logo.convert("RGBA")
        except (UnidentifiedImageError, OSError) as exc:
            raise RuntimeError("Watermark logo could not be opened as PNG.") from exc

    canvas = base_image.convert("RGBA")
    max_width = max(120, int(canvas.width * WATERMARK_SCALE_RATIO))
    max_height = max(80, int(canvas.height * WATERMARK_SCALE_RATIO))
    watermark_logo.thumbnail((max_width, max_height), PILImage.Resampling.LANCZOS)

    opacity = max(0, min(int(workspace.watermark_opacity or 0), 100))
    if opacity < 100:
        alpha_channel = watermark_logo.getchannel("A")
        alpha_channel = alpha_channel.point(
            lambda pixel: int(pixel * (opacity / 100))
        )
        watermark_logo.putalpha(alpha_channel)

    margin = max(24, canvas.width // 40, canvas.height // 40)
    br_position = (
        max(margin, canvas.width - watermark_logo.width - margin),
        max(margin, canvas.height - watermark_logo.height - margin),
    )
    if getattr(settings, "PHOTO_WATERMARK_SAT_CORNER_SELECTION", False):
        from gallery.watermark_sat import choose_quietest_corner  # noqa: PLC0415

        position = choose_quietest_corner(
            canvas,
            watermark_logo.width,
            watermark_logo.height,
            margin,
            min_pixels=int(
                getattr(settings, "PHOTO_WATERMARK_SAT_MIN_PIXELS", 160_000)
            ),
            max_side=int(getattr(settings, "PHOTO_WATERMARK_SAT_MAX_SIDE", 800)),
            tie_epsilon=float(
                getattr(settings, "PHOTO_WATERMARK_SAT_TIE_EPSILON", 1.0)
            ),
        )
    else:
        position = br_position
    canvas.alpha_composite(watermark_logo, dest=position)
    return canvas.convert("RGB")


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

    normalized_photo_id = _normalise_photo_id(photo_id)
    if normalized_photo_id is None:
        logger.warning(
            "[FAST LANE MONITOR] Rejecting invalid photo_id before ORM lookup: %s",
            photo_id,
        )
        return {
            "status": "skipped",
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
        return {"status": "error", "reason": "no_safe_r2_key", "photo_id": normalized_photo_id}

    try:
        r2 = get_r2_client()
        r2.head_object(
            Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME,
            Key=probe_key,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey", "Not Found"):
            return _handle_abandoned_upload(normalized_photo_id, workspace.id, file_size_bytes)

        logger.error(
            "[FAST LANE MONITOR] R2 API error probing photo %s (code=%s): %s",
            photo_id,
            error_code,
            exc,
        )
        return _retry_or_fail(self, photo_id, exc)
    except (BotoCoreError, OSError) as exc:
        logger.error("[FAST LANE MONITOR] Network error probing photo %s: %s", photo_id, exc)
        return _retry_or_fail(self, photo_id, exc)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception(
            "[FAST LANE MONITOR] Unexpected error probing photo %s: %s",
            normalized_photo_id,
            exc,
        )
        Photo.objects.filter(id=normalized_photo_id, status="PENDING").update(status="FAILED")
        return {"status": "error", "reason": "unexpected", "photo_id": normalized_photo_id}

    return _handle_self_heal(normalized_photo_id, probe_key)


process_fast_lane_asset.queue = "image-processing"


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

    generate_photo_web_derivative.apply_async(args=[photo_id], throw=False)
    compute_photo_phash.apply_async(args=[photo_id], throw=False)
    return {"status": "self_healed", "photo_id": photo_id}


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    name="gallery.tasks.generate_photo_web_derivative",
    acks_late=True,
    reject_on_worker_lost=True,
)
def generate_photo_web_derivative(self, photo_id: str) -> Dict[str, Any]:
    from gallery.models import Photo  # noqa: PLC0415
    from gallery.storage import get_r2_client, upload_local_file_to_r2  # noqa: PLC0415

    try:
        photo = (
            Photo.objects
            .select_related("scene__event__workspace")
            .get(id=photo_id)
        )
    except Photo.DoesNotExist:
        return {"status": "missing", "photo_id": photo_id}

    if photo.media_type != "IMAGE":
        return {"status": "skipped_non_image", "photo_id": photo_id}

    workspace = photo.scene.event.workspace
    if not workspace.watermark_logo:
        return {"status": "skipped_no_watermark", "photo_id": photo_id}

    if not photo.r2_object_key:
        return {"status": "skipped_no_origin", "photo_id": photo_id}

    web_key = _build_web_derivative_r2_key(photo)
    if photo.web_r2_object_key == web_key:
        return {"status": "already_generated", "photo_id": photo_id, "web_r2_object_key": web_key}

    temp_source_path = None
    temp_output_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".source") as temp_source:
            temp_source_path = temp_source.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".webp") as temp_output:
            temp_output_path = temp_output.name

        r2_client = get_r2_client()
        response = r2_client.get_object(
            Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME,
            Key=photo.r2_object_key,
        )
        with response["Body"] as source_stream, open(temp_source_path, "wb") as temp_source_file:
            while True:
                chunk = source_stream.read(ARCHIVE_STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                temp_source_file.write(chunk)

        with PILImage.open(temp_source_path) as raw_image:
            raw_image.draft(
                "RGB",
                (WEB_DERIVATIVE_MAX_DIMENSION, WEB_DERIVATIVE_MAX_DIMENSION),
            )
            prepared = ImageOps.exif_transpose(raw_image, in_place=True) or raw_image
            width, height = prepared.size
            prepared.thumbnail(
                (WEB_DERIVATIVE_MAX_DIMENSION, WEB_DERIVATIVE_MAX_DIMENSION),
                PILImage.Resampling.LANCZOS,
            )
            watermarked = _apply_workspace_watermark(prepared, workspace)
            watermarked.save(
                temp_output_path,
                format="WEBP",
                quality=WEB_DERIVATIVE_QUALITY,
                method=6,
            )

        uploaded = upload_local_file_to_r2(
            temp_output_path,
            web_key,
            content_type="image/webp",
        )
        if not uploaded:
            raise RuntimeError("Web derivative upload failed.")

        Photo.objects.filter(id=photo.id).update(
            web_r2_object_key=web_key,
            width=width,
            height=height,
        )
        return {"status": "completed", "photo_id": photo_id, "web_r2_object_key": web_key}
    except Exception as exc:
        logger.exception("[WEB DERIVATIVE] Failed for photo %s: %s", photo_id, exc)
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            return {"status": "error", "photo_id": photo_id, "reason": "max_retries_exceeded"}
    finally:
        for temp_path in (temp_source_path, temp_output_path):
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)


def _schedule_scene_burst_cluster(scene_id: str) -> None:
    """Debounce per-scene reclustering after phash writes."""
    from django.core.cache import cache  # noqa: PLC0415

    debounce = int(getattr(settings, "PHOTO_CLUSTER_DEBOUNCE_SECONDS", 30))
    lock_key = f"photobox:burst:cluster:pending:{scene_id}"
    # cache.add is SET NX — first caller schedules; later ones within TTL are no-ops.
    if cache.add(lock_key, "1", timeout=max(debounce, 1)):
        cluster_scene_bursts.apply_async(
            args=[str(scene_id)],
            countdown=debounce,
        )


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    name="gallery.tasks.compute_photo_phash",
    acks_late=True,
    reject_on_worker_lost=True,
    queue="image-processing",
)
def compute_photo_phash(self, photo_id: str) -> Dict[str, Any]:
    """Compute 64-bit pHash from the original R2 object after READY."""
    from gallery.models import Photo  # noqa: PLC0415
    from gallery.phash import compute_phash_bytes  # noqa: PLC0415
    from gallery.storage import get_r2_client  # noqa: PLC0415

    normalized_photo_id = _normalise_photo_id(photo_id)
    if not normalized_photo_id:
        return {"status": "invalid_id", "photo_id": photo_id}

    version = int(getattr(settings, "PHOTO_PHASH_VERSION", 1))
    try:
        photo = Photo.objects.select_related("scene").get(id=normalized_photo_id)
    except Photo.DoesNotExist:
        return {"status": "missing", "photo_id": normalized_photo_id}

    if photo.status != "READY" or photo.media_type != "IMAGE":
        return {"status": "skipped_not_ready", "photo_id": normalized_photo_id}
    if not photo.r2_object_key:
        return {"status": "skipped_no_origin", "photo_id": normalized_photo_id}
    if photo.phash and photo.phash_version == version:
        return {"status": "already_hashed", "photo_id": normalized_photo_id}

    temp_source_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".source") as temp_source:
            temp_source_path = temp_source.name

        r2_client = get_r2_client()
        response = r2_client.get_object(
            Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME,
            Key=photo.r2_object_key,
        )
        with response["Body"] as source_stream, open(temp_source_path, "wb") as out:
            while True:
                chunk = source_stream.read(ARCHIVE_STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)

        with PILImage.open(temp_source_path) as raw_image:
            prepared = ImageOps.exif_transpose(raw_image) or raw_image
            prepared.thumbnail((256, 256), PILImage.Resampling.LANCZOS)
            digest = compute_phash_bytes(prepared)

        Photo.objects.filter(id=photo.id).update(
            phash=digest,
            phash_version=version,
        )
        _schedule_scene_burst_cluster(str(photo.scene_id))
        return {
            "status": "completed",
            "photo_id": normalized_photo_id,
            "scene_id": str(photo.scene_id),
        }
    except Exception as exc:
        logger.exception("[PHASH] Failed for photo %s: %s", normalized_photo_id, exc)
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            return {
                "status": "error",
                "photo_id": normalized_photo_id,
                "reason": "max_retries_exceeded",
            }
    finally:
        if temp_source_path and os.path.exists(temp_source_path):
            os.remove(temp_source_path)


@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=30,
    name="gallery.tasks.cluster_scene_bursts",
    acks_late=True,
    reject_on_worker_lost=True,
    queue="image-processing",
)
def cluster_scene_bursts(self, scene_id: str) -> Dict[str, Any]:
    """Recompute per-scene burst clusters from stored phashes (offline)."""
    from gallery.burst_cluster import PhashRow, cluster_phash_rows  # noqa: PLC0415
    from gallery.models import Photo, PhotoBurstCluster, Scene  # noqa: PLC0415

    try:
        scene_uuid = UUID(str(scene_id))
    except (AttributeError, TypeError, ValueError):
        return {"status": "invalid_scene", "scene_id": scene_id}

    hamming_threshold = int(getattr(settings, "PHOTO_PHASH_HAMMING_THRESHOLD", 8))
    time_window = int(getattr(settings, "PHOTO_BURST_TIME_WINDOW_SECONDS", 90))
    bands = int(getattr(settings, "PHOTO_PHASH_LSH_BANDS", 8))
    rows_per_band = int(getattr(settings, "PHOTO_PHASH_LSH_ROWS", 8))
    version = int(getattr(settings, "PHOTO_PHASH_VERSION", 1))

    try:
        with transaction.atomic():
            try:
                Scene.objects.select_for_update().get(id=scene_uuid)
            except Scene.DoesNotExist:
                return {"status": "missing_scene", "scene_id": str(scene_uuid)}

            photos = list(
                Photo.objects.filter(
                    scene_id=scene_uuid,
                    status="READY",
                    media_type="IMAGE",
                    phash__isnull=False,
                ).values_list("id", "phash", "uploaded_at")
            )
            rows = [
                PhashRow(photo_id=pid, phash=bytes(ph), uploaded_at=ts)
                for pid, ph, ts in photos
                if ph
            ]
            components = cluster_phash_rows(
                rows,
                hamming_threshold=hamming_threshold,
                time_window_seconds=time_window,
                bands=bands,
                rows_per_band=rows_per_band,
            )

            Photo.objects.filter(scene_id=scene_uuid).update(burst_cluster=None)
            PhotoBurstCluster.objects.filter(scene_id=scene_uuid).delete()

            clustered_ids: list = []
            for members in components:
                ordered = sorted(
                    members,
                    key=lambda pid: next(
                        r.uploaded_at for r in rows if r.photo_id == pid
                    ),
                )
                cluster = PhotoBurstCluster.objects.create(
                    scene_id=scene_uuid,
                    representative_photo_id=ordered[0],
                    member_count=len(ordered),
                    phash_version=version,
                    hamming_threshold=hamming_threshold,
                )
                Photo.objects.filter(id__in=ordered).update(burst_cluster=cluster)
                clustered_ids.extend(ordered)

        return {
            "status": "completed",
            "scene_id": str(scene_uuid),
            "cluster_count": len(components),
            "clustered_photos": len(clustered_ids),
        }
    except Exception as exc:
        logger.exception("[BURST] Clustering failed for scene %s: %s", scene_id, exc)
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            return {"status": "error", "scene_id": str(scene_uuid)}


def _handle_abandoned_upload(
    photo_id: str,
    workspace_id: Any,
    file_size_bytes: int,
) -> Dict[str, Any]:
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
        release_workspace_bytes(workspace_id, refund_bytes)

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
    archive_scope = "full"
    if job.archive_type == GalleryArchiveType.FAVORITES and job.access_session_id:
        archive_scope = f"favorites/session_{job.access_session_id}"

    return (
        f"archives/tenant_{job.gallery.workspace_id}/"
        f"gallery_{job.gallery_id}/{archive_scope}/{job.id}.zip"
    )


def _archive_photos_queryset(job: GalleryArchiveJob):
    queryset = (
        Photo.objects
        .select_related("scene")
        .filter(
            scene__event=job.gallery,
            status="READY",
        )
        .exclude(r2_object_key__isnull=True)
        .exclude(r2_object_key="")
    )

    if job.archive_type == GalleryArchiveType.FAVORITES:
        if not job.access_session_id:
            raise RuntimeError("Favorites archive requires an access session.")

        allowed_visibility = [VisibilityChoices.PUBLIC]
        if job.access_session.role == "CLIENT":
            allowed_visibility.append(VisibilityChoices.CLIENT_ONLY)

        queryset = queryset.filter(
            favorite_selections__session_id=job.access_session_id,
            visibility__in=allowed_visibility,
        ).distinct()
    else:
        queryset = queryset.filter(
            visibility__in=[
                VisibilityChoices.PUBLIC,
                VisibilityChoices.CLIENT_ONLY,
            ],
        )

    return queryset


def _get_archive_photos_queryset(job: GalleryArchiveJob):
    """Stable pk-ordered queryset for ZIP builds (no named-cursor iterator)."""
    return _archive_photos_queryset(job).order_by("pk")


def _enqueue_archive_job(job_id) -> None:
    queue = getattr(settings, "ARCHIVE_ZIP_QUEUE", "archive-zip")
    build_gallery_archive.apply_async(args=[str(job_id)], queue=queue)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="gallery.tasks.build_gallery_archive",
    queue="archive-zip",
    acks_late=True,
    reject_on_worker_lost=True,
)
def build_gallery_archive(self, archive_job_id: str) -> Dict[str, Any]:
    import time

    from gallery.db_batch import iter_pk_batches
    from gallery.models import GalleryArchiveJob  # noqa: PLC0415
    from gallery.storage import get_r2_client, open_multipart_r2_upload  # noqa: PLC0415
    from gallery.zip_lease import (
        acquire_zip_lease,
        heartbeat_zip_lease,
        release_zip_lease,
    )

    try:
        job = GalleryArchiveJob.objects.select_related(
            "gallery__workspace",
            "access_session",
        ).get(id=archive_job_id)
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

    lease_decision = acquire_zip_lease(str(job.id), str(job.gallery_id))
    if not lease_decision.acquired:
        logger.info(
            "[ARCHIVE] ZIP lease unavailable for job %s (%s); retrying.",
            archive_job_id,
            lease_decision.reason,
        )
        raise self.retry(countdown=30, exc=RuntimeError(lease_decision.reason))

    lease = lease_decision.lease
    sink = None
    try:
        GalleryArchiveJob.objects.filter(id=job.id).update(
            status=GalleryArchiveJob.Status.PROCESSING
        )

        required_bytes = (
            _archive_photos_queryset(job).aggregate(
                total=Sum("file_size_bytes")
            )["total"]
            or 0
        )
        # ZIP is streamed to R2 (no full tempfile). Guard only that the worker
        # still has a small ephemeral margin for source-chunk spill / OS needs.
        margin = int(getattr(settings, "ARCHIVE_DISK_MARGIN_BYTES", 50 * 1024 * 1024))
        available_bytes = shutil.disk_usage(tempfile.gettempdir()).free
        if available_bytes < margin:
            GalleryArchiveJob.objects.filter(id=job.id).update(
                status=GalleryArchiveJob.Status.FAILED
            )
            logger.warning(
                "[ARCHIVE] Refusing job %s: free disk %s < margin %s (gallery bytes=%s).",
                archive_job_id,
                available_bytes,
                margin,
                required_bytes,
            )
            return {
                "status": "error",
                "reason": "insufficient_disk_space",
                "archive_job_id": archive_job_id,
            }

        zip_key = _build_archive_r2_key(job)
        r2_client = get_r2_client()
        heartbeat_seconds = int(
            getattr(settings, "ARCHIVE_ZIP_LEASE_HEARTBEAT_SECONDS", 10)
        )
        last_heartbeat = time.monotonic()
        sink = open_multipart_r2_upload(zip_key, "application/zip")
        with zipfile.ZipFile(
            sink,
            mode="w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as archive_file:
            for photo in iter_pk_batches(_get_archive_photos_queryset(job)):
                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_seconds:
                    heartbeat_zip_lease(lease)
                    last_heartbeat = now
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

        sink.close()

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
        if sink is not None:
            sink.abort()
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
        if lease is not None:
            release_zip_lease(lease)


build_gallery_archive.queue = "archive-zip"


@shared_task(
    name="gallery.tasks.send_gallery_magic_link_email",
    acks_late=True,
)
def send_gallery_magic_link_email(
    email: str,
    gallery_title: str,
    magic_url: str,
) -> Dict[str, Any]:
    """Deliver magic-link mail off the request path (SMTP must not block guests)."""
    from django.core.mail import send_mail

    send_mail(
        subject=f"Your secure access link for {gallery_title}",
        message=(
            f"Use this single-use link within 15 minutes to access {gallery_title}: "
            f"{magic_url}"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )
    return {"status": "sent", "email": email}


upload_photo_to_r2 = process_fast_lane_asset


# Plan 03 registers this on CLIENT_ACCESS_BEAT_SCHEDULE — daily 03:00 UTC.
@shared_task(
    name="gallery.tasks.purge_expired_gallery_access_artifacts",
    acks_late=True,
)
def purge_expired_gallery_access_artifacts() -> Dict[str, Any]:
    """Delete expired magic links and inert access sessions."""
    from gallery.models import GalleryAccessSession, GalleryMagicLink

    now = timezone.now()
    expired_links, _ = GalleryMagicLink.objects.filter(expires_at__lte=now).delete()

    cutoff = now - timedelta(days=int(getattr(settings, "GALLERY_SESSION_RETENTION_DAYS", 30)))
    stale_sessions, _ = (
        GalleryAccessSession.objects
        .filter(created_at__lt=cutoff)
        .exclude(favorite_selections__isnull=False)
        .exclude(archive_jobs__isnull=False)
        .delete()
    )

    logger.info(
        "[RETENTION] Purged %d expired magic links and %d inert access sessions.",
        expired_links,
        stale_sessions,
    )
    return {
        "status": "completed",
        "expired_magic_links": expired_links,
        "stale_access_sessions": stale_sessions,
    }
