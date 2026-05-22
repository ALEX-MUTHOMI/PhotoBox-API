import json
import logging

from django.conf import settings
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from core.security import verify_webhook_signature, verify_webhook_timestamp
# Import the aliased MediaAsset from gallery
from gallery.models import MediaAsset
from gallery.tasks import generate_photo_web_derivative

logger = logging.getLogger(__name__)
_MAX_WEBHOOK_PAYLOAD_BYTES = 5 * 1024 * 1024
_WEBHOOK_REPLAY_WINDOW_SECONDS = getattr(settings, "WEBHOOK_REPLAY_WINDOW_SECONDS", 300)

class CloudflareWebhookView(APIView):
    """
    Ingests Cloudflare R2 webhooks.
    Validates timestamp-bound HMAC signatures and updates MediaAsset state.
    """
    authentication_classes = [] # Disable global auth
    permission_classes = []     # Disable global permissions
    throttle_classes = []       # Disable rate limiting for webhooks (Cloudflare IPs)

    # Optional: If you want to use a custom parser to ensure you always get bytes:
    # However, request.body is always available in DRF.
    
    def post(self, request, *args, **kwargs):
        # 1. Size Limit before parsing (Memory Exhaustion DoS Protection)
        content_length = request.META.get('CONTENT_LENGTH')
        if content_length and int(content_length) > _MAX_WEBHOOK_PAYLOAD_BYTES:
            logger.warning("Webhook payload too large.")
            return Response({"error": "Payload too large"}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Get Raw Payload
        payload_bytes = request.body
        
        # 3. Retrieve Signature
        cloudflare_signature = request.META.get('HTTP_X_CLOUDFLARE_SIGNATURE')
        if not cloudflare_signature:
            logger.warning("Missing Cloudflare Signature")
            return Response({"error": "Missing signature"}, status=status.HTTP_403_FORBIDDEN)

        # 4. Replay Protection (fail closed) with canonical timestamp validation
        webhook_timestamp_str = (
            request.META.get('HTTP_WEBHOOK_TIMESTAMP')
            or request.META.get('HTTP_X_WEBHOOK_TIMESTAMP')
        )
        timestamp_valid, timestamp_reason, canonical_timestamp = verify_webhook_timestamp(
            webhook_timestamp_str,
            max_age_seconds=_WEBHOOK_REPLAY_WINDOW_SECONDS,
        )
        if not timestamp_valid:
            logger.warning("Webhook timestamp rejected. reason=%s", timestamp_reason)
            return Response({"error": "Invalid timestamp"}, status=status.HTTP_403_FORBIDDEN)

        # 5. Validate HMAC over "<timestamp>.<raw_payload>"
        signature_valid, signature_reason = verify_webhook_signature(
            payload_bytes,
            canonical_timestamp,
            cloudflare_signature,
            secret_setting="CLOUDFLARE_WEBHOOK_SECRET",  # nosec B106 - setting name, not a secret value.
        )
        if not signature_valid:
            if signature_reason in ("secret_not_configured", "secret_encoding_error"):
                logger.critical(
                    "[WEBHOOK] CLOUDFLARE_WEBHOOK_SECRET is not configured correctly. "
                    "reason=%s",
                    signature_reason,
                )
                return Response({"error": "Server misconfiguration"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            logger.warning("Invalid Cloudflare Signature. reason=%s", signature_reason)
            return Response({"error": "Invalid signature"}, status=status.HTTP_403_FORBIDDEN)

        # 6. Parse JSON Payload
        try:
            payload = json.loads(payload_bytes)
        except json.JSONDecodeError:
            logger.error("Invalid JSON in webhook payload")
            return Response({"error": "Invalid JSON"}, status=status.HTTP_400_BAD_REQUEST)

        action = payload.get('action')
        r2_object_key = payload.get('r2_object_key')
        size = payload.get('size')

        # 7. Action Filter
        if action != 'PutObject':
            logger.info(f"Ignoring non-PutObject action: {action}")
            return Response({"status": "ignored"}, status=status.HTTP_200_OK)

        # 8. Ghost File Tolerance
        try:
            asset = MediaAsset.objects.get(r2_object_key=r2_object_key)
        except MediaAsset.DoesNotExist:
            logger.warning(f"Webhook received for unknown object key: {r2_object_key}")
            # Return 200 OK to prevent Cloudflare retry storms
            return Response({"status": "ignored"}, status=status.HTTP_200_OK)

        # 9. Quota / Size Mismatch Quarantine
        # Check against integer size in DB to prevent unexpected scaling errors
        expected_size = asset.file_size_bytes or 0
        if size and int(size) > expected_size:
            logger.error(f"Asset size mismatch for {r2_object_key}. Expected: {expected_size}, Got: {size}")
            asset.status = "QUARANTINED"
            asset.save(update_fields=['status'])
            return Response({"status": "quarantined"}, status=status.HTTP_200_OK)

        # 10. Update state atomically
        with transaction.atomic():
            locked_asset = MediaAsset.objects.select_for_update().get(pk=asset.pk)

            if locked_asset.status == "READY" and locked_asset.is_processed:
                logger.info("Asset %s already READY. Idempotent skip.", r2_object_key)
                if locked_asset.media_type == "IMAGE":
                    generate_photo_web_derivative.delay(str(locked_asset.id))
                return Response({"status": "already_ready"}, status=status.HTTP_200_OK)

            if locked_asset.status != "QUARANTINED":
                locked_asset.status = "READY"
                locked_asset.is_processed = True
                locked_asset.save(update_fields=['status', 'is_processed'])

        if asset.media_type == "IMAGE":
            generate_photo_web_derivative.delay(str(asset.id))
        logger.info("Asset %s successfully marked as READY", r2_object_key)
        return Response({"status": "success"}, status=status.HTTP_200_OK)
