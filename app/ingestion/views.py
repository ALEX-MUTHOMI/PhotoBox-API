# ingestion/views.py
"""
Heavy Lane EDA Ingestion Gateway.

Two views:
  BulkIngestionView  — Validates manifests, generates R2 presigned POST
                       tickets, then atomically locks quota and creates assets.
  R2WebhookView      — Receives Cloudflare R2 upload-complete signals,
                       verifies HMAC, transitions asset PENDING → READY.

Lock contention architecture:
  Phase 1 (no lock): UUID generation, key validation, HMAC ticket generation.
  Phase 2 (lock):    Quota check, quota deduction, bulk_create.

  Lock hold time is O(1) regardless of batch size.
  Phase 1 is O(N) CPU-bound but holds NO database lock.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.utils import OperationalError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.security import (
    verify_webhook_signature as verify_signed_webhook_signature,
    verify_webhook_timestamp as validate_signed_webhook_timestamp,
)
from gallery.models import MediaAsset, Scene, Workspace
from gallery.storage import (
    R2KeyValidationError,
    generate_r2_presigned_post,
    validate_r2_key,
)
from gallery.throttles import HeavyLaneTicketThrottle
from .serializers import (
    MAX_IMAGE_SIZE_BYTES,
    MAX_VIDEO_SIZE_BYTES,
    BulkManifestSerializer,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — never hardcode policy values inline
# ---------------------------------------------------------------------------

_UPLOAD_TICKET_TTL: int = getattr(
    settings, "HEAVY_LANE_UPLOAD_URL_TTL_SECONDS", 900
)
_MAX_WEBHOOK_PAYLOAD_BYTES: int = 1 * 1024 * 1024  # 1 MB soft guard
_WEBHOOK_REPLAY_WINDOW: int = getattr(
    settings, "WEBHOOK_REPLAY_WINDOW_SECONDS", 300
)
_MAX_CLIENT_REF_LEN: int = 255


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------

@dataclass
class _PreparedAsset:
    """
    Holds everything generated in Phase 1 for a single file in the manifest.

    Immutable after construction — Phase 2 reads from this, never writes.
    Using a dataclass instead of a raw dict prevents key typo bugs and
    makes the data contract between Phase 1 and Phase 2 explicit.
    """
    unique_file_id: uuid.UUID
    object_key: str
    sanitized_name: str
    media_type: str
    file_size: int
    client_ref: Optional[str]
    presigned: Dict[str, Any]   # output of generate_r2_presigned_post


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_r2_object_key(
    user_id: Any,
    scene_id: Any,
    unique_file_id: uuid.UUID,
    sanitized_name: str,
) -> str:
    """
    Build and validate the R2 object key before any signing occurs.

    Raises R2KeyValidationError if the key is unsafe.
    This is the last line of defence against filename injection that
    slipped past the serializer's sanitisation.
    """
    key = (
        f"raw/tenant_{user_id}/scene_{scene_id}/"
        f"{unique_file_id}_{sanitized_name}"
    )
    return validate_r2_key(key)


def _build_upload_ticket(asset: _PreparedAsset) -> Dict[str, Any]:
    """
    Build the stable upload ticket contract returned to the client.

    Contract keys (stable — never remove without an API version bump):
      upload_url            — canonical (all clients must use this)
      post_url              — deprecated alias (remove in API v2)
      post_fields           — presigned POST policy fields
      upload_id             — asset UUID for client-side tracking
      client_reference_id   — echoed back for client idempotency
    """
    return {
        "upload_url": asset.presigned["upload_url"],
        "post_url": asset.presigned["post_url"],
        "post_fields": asset.presigned.get("post_fields", {}),
        "upload_id": str(asset.unique_file_id),
        "client_reference_id": (
            str(asset.client_ref)[:_MAX_CLIENT_REF_LEN]
            if asset.client_ref
            else ""
        ),
    }


def _phase1_prepare_assets(
    files: List[Dict[str, Any]],
    user_id: Any,
    scene_id: Any,
) -> Tuple[Optional[List[_PreparedAsset]], Optional[Response]]:
    """
    Phase 1: CPU-bound work with NO database lock held.

    Generates UUIDs, validates R2 keys, and generates presigned POST URLs
    for every file in the manifest.

    Returns:
        (prepared_assets, None)     — success
        (None, error_response)      — failure, caller should return the response

    Design notes:
        - Fails fast on first error (no partial ticket sets returned)
        - generate_r2_presigned_post is pure HMAC crypto — no network call
        - If this function returns successfully but Phase 2 rejects for quota,
          the generated presigned URLs become orphaned. This is safe:
          no MediaAsset records exist, so R2 webhook finds no match (ghost key)
          and ignores any upload. URLs expire after _UPLOAD_TICKET_TTL seconds.
    """
    prepared: List[_PreparedAsset] = []

    for file_item in files:
        sanitized_name: str = file_item["sanitized_filename"]
        media_type: str = file_item["media_type"]
        client_ref: Optional[str] = file_item.get("client_reference_id")
        file_size: int = file_item["file_size"]

        unique_file_id = uuid.uuid4()

        # Validate the key before signing it.
        # R2KeyValidationError here means the serializer accepted a filename
        # that storage.py rejects — a serializer regression, not user error.
        try:
            object_key = _build_r2_object_key(
                user_id, scene_id, unique_file_id, sanitized_name
            )
        except R2KeyValidationError as exc:
            logger.error(
                "[VANGUARD] R2 key validation failed after serialiser accepted "
                "filename — serialiser gap. exc_type=%s filename=%r",
                type(exc).__qualname__,
                sanitized_name,
            )
            return None, Response(
                {"detail": "Invalid filename in manifest."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_bytes = (
            MAX_IMAGE_SIZE_BYTES if media_type == "IMAGE" else MAX_VIDEO_SIZE_BYTES
        )
        mime_prefix = "image/" if media_type == "IMAGE" else "video/"

        # Pure HMAC computation — no network call, safe outside DB transaction
        presigned = generate_r2_presigned_post(
            r2_object_key=object_key,
            max_size_bytes=max_bytes,
            expires_in=_UPLOAD_TICKET_TTL,
            extra_conditions=[
                ["starts-with", "$Content-Type", mime_prefix],
            ],
            extra_fields={
                "x-amz-meta-media-type": media_type,
                "x-amz-meta-client-ref": str(client_ref or ""),
                "Content-Type": f"{mime_prefix}*",
            },
        )

        if presigned is None:
            # gallery.storage already logged the boto3 error detail
            logger.error(
                "[VANGUARD] generate_r2_presigned_post returned None. "
                "media_type=%s Aborting batch.",
                media_type,
            )
            return None, Response(
                {"detail": "Storage provider is temporarily unavailable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        prepared.append(
            _PreparedAsset(
                unique_file_id=unique_file_id,
                object_key=object_key,
                sanitized_name=sanitized_name,
                media_type=media_type,
                file_size=file_size,
                client_ref=client_ref,
                presigned=presigned,
            )
        )

    return prepared, None


def _phase2_commit(
    prepared_assets: List[_PreparedAsset],
    total_incoming_bytes: int,
    user: Any,
    scene_id: Any,
) -> Tuple[Optional[List[Dict[str, Any]]], Optional[Response]]:
    """
    Phase 2: O(1) atomic DB operation with lock held as briefly as possible.

    The lock is acquired, used for three instant DB operations, then released.
    No CPU-bound work occurs inside this block.

    Returns:
        (response_tickets, None)  — success
        (None, error_response)    — failure

    Transaction order:
        1. Lock workspace row             ~0.1ms
        2. Re-validate scene ownership    ~1ms  (FK + ownership in one query)
        3. Quota headroom check           ~0ms  (pure arithmetic)
        4. Deduct quota                   ~1ms  (single UPDATE)
        5. bulk_create assets             ~5-15ms (single INSERT)
        Total lock hold time:             ~7-17ms regardless of batch size
    """
    try:
        with transaction.atomic():

            # Acquire row lock — nowait=True raises OperationalError immediately
            # on contention rather than queuing. We return 409 so the client
            # retries with backoff rather than stacking up waiting connections.
            workspace = (
                Workspace.objects
                .select_for_update(nowait=True)
                .get(user=user)
            )

            # Re-validate scene inside the lock.
            # Phase 1 validated scene ownership. Between Phase 1 and Phase 2:
            #   - The scene could be deleted → IntegrityError on bulk_create
            #   - The user could lose workspace access → DoesNotExist
            # Re-validating here catches both races before we touch quota.
            # Using event__workspace=workspace enforces ownership in the query
            # itself — not in Python — which is atomic with the workspace lock.
            try:
                scene = Scene.objects.get(
                    id=scene_id,
                    event__workspace=workspace,
                )
            except Scene.DoesNotExist:
                # 403 not 404 — don't confirm whether scene exists for others
                return None, Response(
                    {"detail": "Scene not found or access denied."},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Quota check uses fresh workspace data from the locked row.
            # Phase 1's total_incoming_bytes is trusted — it was computed
            # from serialiser-validated file sizes, not from raw user input.
            projected_usage = workspace.storage_used_bytes + total_incoming_bytes
            if projected_usage > workspace.storage_limit_bytes:
                return None, Response(
                    {"detail": "Storage quota exceeded. Please upgrade your plan."},
                    status=status.HTTP_402_PAYMENT_REQUIRED,
                )

            # Build MediaAsset objects from the immutable _PreparedAsset data
            db_assets = [
                MediaAsset(
                    id=a.unique_file_id,
                    scene=scene,
                    original_filename=a.sanitized_name,
                    r2_object_key=a.object_key,
                    media_type=a.media_type,
                    file_size_bytes=a.file_size,
                    status="PENDING",
                )
                for a in prepared_assets
            ]

            # Atomic quota deduction (inside lock — cannot race)
            workspace.storage_used_bytes = projected_usage
            workspace.save(update_fields=["storage_used_bytes"])

            # O(1) bulk insert — one SQL statement regardless of batch size
            MediaAsset.objects.bulk_create(db_assets)

    except Workspace.DoesNotExist:
        return None, Response(
            {"detail": "Workspace not found."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    except OperationalError:
        logger.warning(
            "[VANGUARD] DB lock collision — concurrent upload in progress. "
            "user_id=%s",
            getattr(user, "id", "unknown"),
        )
        return None, Response(
            {"detail": "Another upload is in progress. Please retry in a few seconds."},
            status=status.HTTP_409_CONFLICT,
        )

    except IntegrityError as exc:
        # Most likely cause: scene was deleted between Phase 1 and Phase 2.
        # This is a race condition — log as WARNING, not CRITICAL.
        # CRITICAL is reserved for programming errors and data corruption.
        logger.warning(
            "[VANGUARD] IntegrityError during bulk_create. "
            "Scene likely deleted between validation and commit. "
            "exc_type=%s",
            type(exc).__qualname__,
        )
        return None, Response(
            {"detail": "Target scene was modified. Please retry."},
            status=status.HTTP_409_CONFLICT,
        )

    # Build response tickets from the immutable prepared data
    response_tickets = [_build_upload_ticket(a) for a in prepared_assets]
    return response_tickets, None


# ---------------------------------------------------------------------------
# Internal webhook helpers
# ---------------------------------------------------------------------------

def _verify_webhook_hmac(
    payload_bytes: bytes,
    timestamp: str,
    signature_header: str,
) -> Tuple[bool, str]:
    """
    Verify Cloudflare webhook HMAC-SHA256 signature.

    Returns (is_valid, failure_reason).
    Never raises — all failure modes return (False, reason_string).

    failure_reason values:
        ""                      — success
        "secret_not_configured" — CLOUDFLARE_WEBHOOK_SECRET is empty
        "secret_encoding_error" — secret contains non-UTF-8 bytes
        "signature_mismatch"    — forgery or tampered payload
    """
    return verify_signed_webhook_signature(
        payload_bytes,
        timestamp,
        signature_header,
        secret_setting="CLOUDFLARE_WEBHOOK_SECRET",
    )


def _verify_webhook_timestamp(
    timestamp_header: Optional[str],
) -> Tuple[bool, str, Optional[str]]:
    """
    Verify webhook timestamp is within the replay attack window and canonicalise it.
    """
    return validate_signed_webhook_timestamp(
        timestamp_header,
        max_age_seconds=_WEBHOOK_REPLAY_WINDOW,
    )


# =============================================================================
# PART 1: VANGUARD GATEWAY — Bulk Ticket Factory
# =============================================================================

class BulkIngestionView(APIView):
    """
    Heavy Lane Bulk Ingestion Gateway.

    Lock contention architecture:
        Phase 1 — CPU-bound, NO lock:
            Validate scene ownership, generate UUIDs,
            validate R2 keys, generate presigned POST URLs.
            Duration: O(N × HMAC_cost). Lock held: zero.

        Phase 2 — DB-only, lock held O(1):
            Re-validate scene (race guard), quota check,
            quota deduction, bulk_create.
            Duration: ~7-17ms regardless of batch size.

    If Phase 2 rejects (quota exceeded, scene deleted, lock contention),
    the Phase 1 presigned URLs are orphaned. This is safe: no MediaAsset
    records exist, so any upload against those URLs hits the ghost key
    handler in R2WebhookView and is silently ignored. URLs expire after
    _UPLOAD_TICKET_TTL seconds.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [HeavyLaneTicketThrottle]

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:

        # ------------------------------------------------------------------
        # Step 1: Validate the incoming manifest
        # ------------------------------------------------------------------
        serializer = BulkManifestSerializer(
            data=request.data,
            context={"request": request},
        )
        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )

        validated = serializer.validated_data
        scene_id: uuid.UUID = validated["scene_id"]
        files: List[Dict[str, Any]] = validated["files"]
        user = request.user
        total_incoming_bytes: int = sum(f["file_size"] for f in files)

        # ------------------------------------------------------------------
        # Step 2: Pre-flight scene ownership check (NO lock)
        #
        # Why check here AND inside the transaction?
        #   Here:       Fast rejection for clearly invalid requests.
        #               Avoids acquiring a DB lock for a nonexistent scene.
        #   In Phase 2: Guards against the race where scene is deleted
        #               between here and the atomic commit.
        #
        # Why 403 and not 404?
        #   Returning 404 would confirm to a cross-tenant attacker whether
        #   the scene_id exists in someone else's workspace (IDOR info leak).
        #   403 is neutral: "you can't access this", not "this doesn't exist".
        # ------------------------------------------------------------------
        try:
            user_workspace = Workspace.objects.get(user=user)
        except Workspace.DoesNotExist:
            return Response(
                {"detail": "Workspace not found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        scene_exists = Scene.objects.filter(
            id=scene_id,
            event__workspace=user_workspace,
        ).exists()

        if not scene_exists:
            return Response(
                {"detail": "Scene not found or access denied."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # ------------------------------------------------------------------
        # Phase 1: CPU-bound work — NO database lock held
        #
        # Generates UUIDs, validates R2 keys, computes HMAC presigned URLs.
        # Lock hold time contribution: ZERO.
        # ------------------------------------------------------------------
        prepared_assets, error = _phase1_prepare_assets(
            files=files,
            user_id=user.id,
            scene_id=scene_id,
        )
        if error is not None:
            return error

        # ------------------------------------------------------------------
        # Phase 2: Atomic DB commit — lock held for O(1) time
        #
        # Re-validates scene, checks quota, deducts quota, bulk_creates.
        # CPU-bound work is completely absent from this block.
        # ------------------------------------------------------------------
        response_tickets, error = _phase2_commit(
            prepared_assets=prepared_assets,
            total_incoming_bytes=total_incoming_bytes,
            user=user,
            scene_id=scene_id,
        )
        if error is not None:
            return error

        # 202 Accepted: tickets issued, assets are PENDING client upload.
        # NOT 201 Created: no resource is fully created yet.
        return Response(
            {"upload_tickets": response_tickets},
            status=status.HTTP_202_ACCEPTED,
        )


# =============================================================================
# PART 2: R2 WEBHOOK — Heavy Lane Completion Signal Receiver
# =============================================================================

class R2WebhookView(APIView):
    """
    Cloudflare R2 Upload Completion Webhook Receiver.

    Security layers applied in strict order:
      1.  Content-Length soft guard (hard limit must be in nginx)
      2.  Raw body capture before DRF stream consumption
      3.  Signature header presence check
      4.  Replay attack window (timestamp-based, mandatory header)
      5.  Secret misconfiguration guard (fail closed → 500, not 403)
      6.  HMAC-SHA256 constant-time verification over "<ts>.<raw_body>"
      7.  JSON parse with explicit error handling
      8.  Action filter (PutObject only, 200 on others)
      9.  r2_object_key presence check
      10. Ghost key tolerance (200 on unknown keys)
      11. Atomic: select_for_update + idempotency + size check + transition
    """
    authentication_classes = []  # HMAC replaces JWT for machine-to-machine
    permission_classes = []
    throttle_classes = []        # Cloudflare IPs must never be throttled

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:

        # ------------------------------------------------------------------
        # Step 1: Content-Length soft guard
        # Hard limit MUST be enforced in nginx (client_max_body_size).
        # This check only stops honest oversized requests.
        # ------------------------------------------------------------------
        raw_cl = request.META.get("CONTENT_LENGTH")
        if raw_cl:
            try:
                if int(raw_cl) > _MAX_WEBHOOK_PAYLOAD_BYTES:
                    logger.warning("[R2-WEBHOOK] Content-Length exceeds soft cap.")
                    return Response(
                        {"detail": "Payload too large."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            except (ValueError, TypeError):
                pass

        # ------------------------------------------------------------------
        # Step 2: Capture raw bytes before DRF parsing
        # ------------------------------------------------------------------
        payload_bytes: bytes = request.body

        # ------------------------------------------------------------------
        # Step 3: Signature header presence
        # ------------------------------------------------------------------
        signature_header = request.META.get("HTTP_X_CLOUDFLARE_SIGNATURE", "")
        if not signature_header:
            logger.warning("[R2-WEBHOOK] Missing X-Cloudflare-Signature header.")
            return Response(
                {"detail": "Missing signature."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # ------------------------------------------------------------------
        # Step 4: Replay attack window + canonical timestamp
        # ------------------------------------------------------------------
        ts_header = (
            request.META.get("HTTP_WEBHOOK_TIMESTAMP")
            or request.META.get("HTTP_X_WEBHOOK_TIMESTAMP")
        )
        ts_valid, ts_reason, canonical_ts = _verify_webhook_timestamp(ts_header)
        if not ts_valid:
            logger.warning(
                "[R2-WEBHOOK] Timestamp validation failed. reason=%s",
                ts_reason,
            )
            return Response(
                {"detail": "Webhook expired or timestamp invalid."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # ------------------------------------------------------------------
        # Steps 5 + 6: HMAC verification over "<ts>.<raw_body>"
        # ------------------------------------------------------------------
        valid, reason = _verify_webhook_hmac(payload_bytes, canonical_ts, signature_header)
        if not valid:
            if reason in ("secret_not_configured", "secret_encoding_error"):
                logger.critical(
                    "[R2-WEBHOOK] Server misconfiguration: reason=%s. "
                    "All webhooks rejected until fixed.",
                    reason,
                )
                return Response(
                    {"detail": "Server misconfiguration."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            logger.warning(
                "[R2-WEBHOOK] Signature verification failed. reason=%s remote=%s",
                reason,
                (request.META.get("HTTP_X_FORWARDED_FOR", "")
                 .split(",")[0].strip()
                 or request.META.get("REMOTE_ADDR", "unknown")),
            )
            return Response(
                {"detail": "Invalid signature."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # ------------------------------------------------------------------
        # Step 7: JSON parse
        # ------------------------------------------------------------------
        try:
            payload: Dict[str, Any] = json.loads(payload_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.error("[R2-WEBHOOK] Malformed JSON payload.")
            return Response(
                {"detail": "Invalid JSON payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        action: Optional[str] = payload.get("action")
        r2_object_key: Optional[str] = payload.get("r2_object_key")
        reported_size: Any = payload.get("size")

        # ------------------------------------------------------------------
        # Step 8: Action filter
        # Return 200 for non-PutObject events — halts Cloudflare retries
        # ------------------------------------------------------------------
        if action != "PutObject":
            logger.info("[R2-WEBHOOK] Ignored non-PutObject action: %r", action)
            return Response({"status": "ignored"}, status=status.HTTP_200_OK)

        # ------------------------------------------------------------------
        # Step 9: r2_object_key presence
        # ------------------------------------------------------------------
        if not r2_object_key:
            logger.warning("[R2-WEBHOOK] PutObject event missing r2_object_key.")
            return Response(
                {"detail": "Missing r2_object_key."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ------------------------------------------------------------------
        # Step 10: Ghost key tolerance
        # 200 on unknown keys — 404 causes Cloudflare to retry indefinitely
        # ------------------------------------------------------------------
        try:
            asset = MediaAsset.objects.get(r2_object_key=r2_object_key)
        except MediaAsset.DoesNotExist:
            logger.warning(
                "[R2-WEBHOOK] Unknown key — returning 200 to halt retry storm. "
                "key=%r",
                r2_object_key,
            )
            return Response({"status": "ignored"}, status=status.HTTP_200_OK)

        # ------------------------------------------------------------------
        # Step 11: Atomic idempotency + size check + state transition
        #
        # All three operations in ONE select_for_update transaction.
        # Eliminates the TOCTOU race between idempotency check and save.
        #
        # Size policy:
        #   absent / None     → allow (Cloudflare may omit this field)
        #   0                 → quarantine (zero-byte upload is suspicious)
        #   > declared size   → quarantine (possible file substitution)
        #   <= declared size  → allow
        # ------------------------------------------------------------------
        try:
            with transaction.atomic():
                locked = (
                    MediaAsset.objects
                    .select_for_update()
                    .get(id=asset.id)
                )

                # Idempotency — inside lock, safe from race
                if locked.status == "READY":
                    logger.info(
                        "[R2-WEBHOOK] Already READY — idempotent skip. key=%r",
                        r2_object_key,
                    )
                    return Response(
                        {"status": "already_ready"},
                        status=status.HTTP_200_OK,
                    )

                # Size mismatch quarantine
                if reported_size is not None:
                    try:
                        actual_size = int(reported_size)
                        declared = locked.file_size_bytes or 0

                        if actual_size == 0 or actual_size > declared:
                            logger.error(
                                "[R2-WEBHOOK] Size anomaly. key=%r "
                                "declared=%d actual=%d. QUARANTINING.",
                                r2_object_key,
                                declared,
                                actual_size,
                            )
                            MediaAsset.objects.filter(
                                id=locked.id
                            ).update(status="QUARANTINED")
                            return Response(
                                {"status": "quarantined"},
                                status=status.HTTP_200_OK,
                            )
                    except (ValueError, TypeError):
                        logger.warning(
                            "[R2-WEBHOOK] Unparseable size field. "
                            "key=%r size=%r",
                            r2_object_key,
                            reported_size,
                        )

                # Atomic PENDING → READY
                # .update() on the locked queryset is one SQL statement.
                # The status="PENDING" guard prevents overwriting QUARANTINED.
                updated = MediaAsset.objects.filter(
                    id=locked.id,
                    status="PENDING",
                ).update(
                    status="READY",
                    is_processed=True,
                )

                if updated == 0:
                    # Status changed between lock acquisition and update
                    # (extremely rare — another thread beat us to it)
                    logger.warning(
                        "[R2-WEBHOOK] Update had no effect — status changed "
                        "concurrently. key=%r",
                        r2_object_key,
                    )

        except OperationalError:
            # Lock contention: Fast Lane task or another webhook processing now.
            # Return 200 — the concurrent processor will complete the transition.
            logger.warning(
                "[R2-WEBHOOK] Lock contention — concurrent processor detected. "
                "key=%r Returning 200.",
                r2_object_key,
            )
            return Response(
                {"status": "processing"},
                status=status.HTTP_200_OK,
            )

        logger.info("[R2-WEBHOOK] ✅ Asset marked READY. key=%r", r2_object_key)
        return Response({"status": "success"}, status=status.HTTP_200_OK)
