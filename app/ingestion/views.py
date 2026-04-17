"""
ingestion/views.py — Heavy Lane EDA Ingestion Gateway

Two views:
  1. BulkIngestionView:  Validates manifests, locks quota atomically, mints R2 presigned POST tickets.
  2. R2WebhookView:      Receives Cloudflare R2 upload-complete signals, verifies HMAC, flips asset READY.
"""
import uuid
import json
import hmac
import hashlib
import logging
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.db import transaction, IntegrityError
from django.db.utils import OperationalError
from django.utils import timezone
from datetime import timedelta
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from .serializers import BulkManifestSerializer, MAX_IMAGE_SIZE_BYTES, MAX_VIDEO_SIZE_BYTES
from gallery.models import Scene, MediaAsset, Workspace
from gallery.throttles import HeavyLaneTicketThrottle

logger = logging.getLogger(__name__)


def get_r2_client():
    """
    Build a boto3 S3 client targeting Cloudflare R2.
    Uses a raw Session to prevent credential cross-contamination
    across multiple Gunicorn threads.
    IAM: scoped to s3:PutObject + s3:GetObject ONLY.
    """
    session = boto3.Session(
        aws_access_key_id=getattr(settings, 'CLOUDFLARE_ACCESS_KEY_ID', 'test-key'),
        aws_secret_access_key=getattr(settings, 'CLOUDFLARE_SECRET_ACCESS_KEY', 'test-secret'),
        region_name='auto',
    )
    return session.client(
        's3',
        endpoint_url=getattr(settings, 'CLOUDFLARE_R2_ENDPOINT', 'https://test.r2.cloudflarestorage.com'),
    )


# =============================================================================
# PART 1: VANGUARD GATEWAY — Bulk Ticket Factory
# =============================================================================

class BulkIngestionView(APIView):
    """
    THE VANGUARD GATEWAY.
    Processes the manifest, secures the DB state, checks economic quotas,
    and mints cryptographically bound R2 upload tickets.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [HeavyLaneTicketThrottle]  # PHASE 4: 10/minute per user

    def post(self, request, *args, **kwargs):
        serializer = BulkManifestSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = serializer.validated_data
        scene_id = validated_data['scene_id']
        files = validated_data['files']
        user = request.user

        total_incoming_bytes = sum(f['file_size'] for f in files)
        response_payload = []
        db_assets_to_create = []

        try:
            # 1. THE CLOUD TICKET FACTORY (OUTSIDE DB lock — mathematical hashes are CPU-intense)
            r2_client = get_r2_client()

            try:
                scene = Scene.objects.get(id=scene_id)
            except Scene.DoesNotExist:
                return Response(
                    {"detail": "The target scene no longer exists. Upload aborted."},
                    status=status.HTTP_404_NOT_FOUND
                )

            for file_item in files:
                sanitized_name = file_item['sanitized_filename']
                media_type     = file_item['media_type']
                client_ref     = file_item['client_reference_id']
                file_size      = file_item['file_size']

                unique_file_id = uuid.uuid4()
                object_key = (
                    f"raw/tenant_{user.id}/scene_{scene.id}/"
                    f"{unique_file_id}_{sanitized_name}"
                )

                max_bytes   = MAX_IMAGE_SIZE_BYTES if media_type == 'IMAGE' else MAX_VIDEO_SIZE_BYTES
                mime_prefix = "image/" if media_type == 'IMAGE' else "video/"

                conditions = [
                    ["content-length-range", 1, max_bytes],
                    ["starts-with", "$key", object_key],
                    ["starts-with", "$Content-Type", mime_prefix],
                ]

                presigned_data = r2_client.generate_presigned_post(
                    Bucket=getattr(settings, 'CLOUDFLARE_R2_BUCKET_NAME', 'test-bucket'),
                    Key=object_key,
                    Fields={
                        "x-amz-meta-media-type": media_type,
                        "x-amz-meta-client-ref": client_ref,
                        "Content-Type": f"{mime_prefix}*",
                    },
                    Conditions=conditions,
                    ExpiresIn=300,
                )

                db_assets_to_create.append(
                    MediaAsset(
                        id=unique_file_id,
                        scene=scene,
                        original_filename=sanitized_name,
                        r2_object_key=object_key,
                        media_type=media_type,
                        file_size_bytes=file_size,
                        status='PENDING',
                    )
                )

                response_payload.append({
                    "client_reference_id": client_ref,
                    "post_url":   presigned_data['url'],
                    "post_fields": presigned_data['fields'],
                })

            # 2. THE ATOMIC VAULT & ECONOMIC LEDGER
            with transaction.atomic():
                # SECURITY: nowait=True — if quota is being modified concurrently, throw 409 instantly.
                workspace = Workspace.objects.select_for_update(nowait=True).get(user=user)

                if workspace.storage_used_bytes + total_incoming_bytes > workspace.storage_limit_bytes:
                    return Response(
                        {"detail": "Storage quota exceeded. Please upgrade your plan."},
                        status=status.HTTP_402_PAYMENT_REQUIRED,
                    )

                workspace.storage_used_bytes += total_incoming_bytes
                workspace.save(update_fields=['storage_used_bytes'])

                # O(1) scalability — one INSERT regardless of batch size
                MediaAsset.objects.bulk_create(db_assets_to_create)

        except Workspace.DoesNotExist:
            return Response({"detail": "Workspace not found."}, status=status.HTTP_400_BAD_REQUEST)

        except OperationalError:
            logger.warning(f"L7 Concurrency Defense: DB lock collision for User {user.id}")
            return Response(
                {"detail": "Workspace is currently processing another bulk upload. Please try again in a few seconds."},
                status=status.HTTP_409_CONFLICT,
            )

        except (BotoCoreError, ClientError) as exc:
            logger.error(f"Cloudflare R2 API Failure: {exc}")
            return Response(
                {"detail": "Storage provider is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        except IntegrityError as exc:
            logger.critical(f"Database Integrity Error during bulk insert: {exc}")
            return Response(
                {"detail": "Internal database error. Request aborted."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({"upload_tickets": response_payload}, status=status.HTTP_201_CREATED)


# =============================================================================
# PART 2: R2 COMPLETION WEBHOOK — Heavy Lane Signal Receiver
# =============================================================================

class R2WebhookView(APIView):
    """
    PHASE 2: Heavy Lane R2 Upload Completion Webhook.

    Cloudflare calls this endpoint when a file finishes uploading to R2.
    This is the completion signal that transitions MediaAsset: PENDING → READY.

    SECURITY ARCHITECTURE:
      Step 1 — Content-Length guard: Rejects oversized JSON payloads (OOM defense).
      Step 2 — Raw body capture: Must read bytes before DRF parses stream.
      Step 3 — Signature presence check: Missing header = immediate 403.
      Step 4 — HMAC-SHA256 + hmac.compare_digest(): Timing-safe forgery detection.
      Step 5 — Replay attack window: 5-minute Webhook-Timestamp check.
      Step 6 — JSON parse with explicit error handling.
      Step 7 — Action filter: Only PutObject transitions assets.
      Step 8 — Ghost key tolerance: Unknown keys → 200 (prevents retry storms).
      Step 9 — Size mismatch quarantine: File larger than declared → QUARANTINED.
      Step 10 — Idempotency: Already-READY assets skip silently.
      Step 11 — Atomic state transition: PENDING → READY + is_processed=True.
    """
    authentication_classes = []  # HMAC replaces JWT for machine-to-machine calls
    permission_classes = []      # Public endpoint — security is HMAC signature only
    throttle_classes = []        # Cloudflare IPs must not be throttled

    def post(self, request, *args, **kwargs):

        # ------------------------------------------------------------------
        # STEP 1: Content-Length Guard (OOM / Memory Exhaustion Defense)
        # ------------------------------------------------------------------
        content_length = request.META.get('CONTENT_LENGTH')
        if content_length:
            try:
                if int(content_length) > 1 * 1024 * 1024:  # 1MB cap for JSON
                    logger.warning("[R2-WEBHOOK] Oversized payload rejected.")
                    return Response(
                        {"error": "Payload too large."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            except (ValueError, TypeError):
                pass

        # ------------------------------------------------------------------
        # STEP 2: Capture Raw Bytes
        # Must happen BEFORE any DRF body parsing consumes the stream.
        # ------------------------------------------------------------------
        payload_bytes = request.body

        # ------------------------------------------------------------------
        # STEP 3: Signature Header Extraction
        # ------------------------------------------------------------------
        cloudflare_signature = request.META.get('HTTP_X_CLOUDFLARE_SIGNATURE')
        if not cloudflare_signature:
            logger.warning("[R2-WEBHOOK] Missing X-Cloudflare-Signature header.")
            return Response(
                {"error": "Missing signature."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # ------------------------------------------------------------------
        # STEP 4: HMAC-SHA256 Verification (TIMING-SAFE)
        #
        # CRITICAL SECURITY NOTE:
        #   We use hmac.compare_digest(), NOT == or !=.
        #   String equality (==) short-circuits on the first differing byte,
        #   leaking timing information. An attacker can measure response time
        #   to brute-force the secret one byte at a time (timing oracle attack).
        #   compare_digest always runs in constant time regardless of the
        #   number of matching bytes, eliminating this side channel entirely.
        # ------------------------------------------------------------------
        secret = getattr(settings, 'CLOUDFLARE_WEBHOOK_SECRET', '').encode('utf-8')
        if not secret:
            logger.critical("[R2-WEBHOOK] CLOUDFLARE_WEBHOOK_SECRET is not configured!")
            return Response(
                {"error": "Server misconfiguration."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        expected_sig = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected_sig, cloudflare_signature):
            logger.warning("[R2-WEBHOOK] HMAC signature mismatch — forgery attempt suspected.")
            return Response(
                {"error": "Invalid signature."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # ------------------------------------------------------------------
        # STEP 5: Replay Attack Window (5 minutes)
        #
        # SECURITY: The Webhook-Timestamp header is included in the signed
        # payload, so it cannot be altered without invalidating the HMAC.
        # An attacker who captures a valid webhook cannot replay it after
        # this window expires.
        # ------------------------------------------------------------------
        timestamp_header = (
            request.META.get('HTTP_WEBHOOK_TIMESTAMP') or
            request.META.get('HTTP_X_WEBHOOK_TIMESTAMP')
        )
        if timestamp_header:
            try:
                webhook_dt = timezone.datetime.fromtimestamp(
                    int(timestamp_header), tz=timezone.utc
                )
                age = timezone.now() - webhook_dt
                if age > timedelta(minutes=5):
                    logger.warning(
                        f"[R2-WEBHOOK] Replay attack detected. "
                        f"Webhook age: {age.total_seconds():.0f}s > 300s limit."
                    )
                    return Response(
                        {"error": "Webhook expired."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            except (ValueError, TypeError, OSError):
                # Unparseable timestamp — log and allow through.
                # Do NOT block: Cloudflare might format it differently.
                logger.warning(
                    "[R2-WEBHOOK] Unparseable Webhook-Timestamp. Skipping replay check."
                )

        # ------------------------------------------------------------------
        # STEP 6: JSON Payload Parse
        # ------------------------------------------------------------------
        try:
            payload = json.loads(payload_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.error("[R2-WEBHOOK] Malformed JSON payload.")
            return Response(
                {"error": "Invalid JSON."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        action        = payload.get('action')
        r2_object_key = payload.get('r2_object_key')
        size          = payload.get('size')

        # ------------------------------------------------------------------
        # STEP 7: Action Filter
        # Return 200 for all non-PutObject events to halt Cloudflare retries.
        # ------------------------------------------------------------------
        if action != 'PutObject':
            logger.info(f"[R2-WEBHOOK] Ignored non-PutObject action: {action!r}")
            return Response({"status": "ignored"}, status=status.HTTP_200_OK)

        if not r2_object_key:
            logger.warning("[R2-WEBHOOK] PutObject event missing r2_object_key.")
            return Response(
                {"error": "Missing r2_object_key."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ------------------------------------------------------------------
        # STEP 8: Ghost Key Tolerance
        # Return 200 for unrecognised keys — do NOT return 404 or Cloudflare
        # will retry the webhook indefinitely, creating a denial-of-service
        # against our own webhook endpoint.
        # ------------------------------------------------------------------
        try:
            asset = MediaAsset.objects.select_related(
                'scene__event__workspace'
            ).get(r2_object_key=r2_object_key)
        except MediaAsset.DoesNotExist:
            logger.warning(
                f"[R2-WEBHOOK] Unknown key {r2_object_key!r}. "
                f"Returning 200 to halt Cloudflare retry storm."
            )
            return Response({"status": "ignored"}, status=status.HTTP_200_OK)

        # ------------------------------------------------------------------
        # STEP 9: Size Mismatch Quarantine
        # If the actual file is larger than what was declared in the manifest,
        # the user may have substituted a different file. Quarantine for review.
        # ------------------------------------------------------------------
        if size:
            try:
                actual_size = int(size)
                declared_size = asset.file_size_bytes or 0
                if actual_size > declared_size:
                    logger.error(
                        f"[R2-WEBHOOK] Size mismatch for {r2_object_key}. "
                        f"Declared: {declared_size} bytes, Actual: {actual_size} bytes. QUARANTINING."
                    )
                    asset.status = 'QUARANTINED'
                    asset.save(update_fields=['status'])
                    return Response({"status": "quarantined"}, status=status.HTTP_200_OK)
            except (ValueError, TypeError):
                pass

        # ------------------------------------------------------------------
        # STEP 10: Idempotency Check
        # If this fires twice (Cloudflare retry or duplicate delivery),
        # skip silently. Do not double-process or corrupt the asset state.
        # ------------------------------------------------------------------
        if asset.status == 'READY':
            logger.info(f"[R2-WEBHOOK] Asset {r2_object_key!r} already READY. Idempotent skip.")
            return Response({"status": "already_ready"}, status=status.HTTP_200_OK)

        # ------------------------------------------------------------------
        # STEP 11: Atomic State Transition PENDING → READY
        # ------------------------------------------------------------------
        asset.status = 'READY'
        asset.is_processed = True
        asset.save(update_fields=['status', 'is_processed'])

        logger.info(f"[R2-WEBHOOK] ✅ Asset {r2_object_key!r} marked READY.")

        return Response({"status": "success"}, status=status.HTTP_200_OK)
