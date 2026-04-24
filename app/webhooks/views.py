import json
import hmac
import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone

# Import the aliased MediaAsset from gallery
from gallery.models import MediaAsset

logger = logging.getLogger(__name__)

class CloudflareWebhookView(APIView):
    """
    Ingests Cloudflare R2 webhooks.
    Validates HMAC signature and updates MediaAsset state asynchronously.
    """
    authentication_classes = [] # Disable global auth
    permission_classes = []     # Disable global permissions
    throttle_classes = []       # Disable rate limiting for webhooks (Cloudflare IPs)

    # Optional: If you want to use a custom parser to ensure you always get bytes:
    # However, request.body is always available in DRF.
    
    def post(self, request, *args, **kwargs):
        # 1. Size Limit before parsing (Memory Exhaustion DoS Protection)
        content_length = request.META.get('CONTENT_LENGTH')
        if content_length and int(content_length) > 5 * 1024 * 1024: # 5MB limit
            logger.warning("Webhook payload too large.")
            return Response({"error": "Payload too large"}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Get Raw Payload
        payload_bytes = request.body
        
        # 3. Retrieve Signature
        cloudflare_signature = request.META.get('HTTP_X_CLOUDFLARE_SIGNATURE')
        if not cloudflare_signature:
            logger.warning("Missing Cloudflare Signature")
            return Response({"error": "Missing signature"}, status=status.HTTP_403_FORBIDDEN)

        # 4. Validate HMAC Signature (Timing Attack Resistant)
        # SECURITY FIX: Was previously using CLOUDFLARE_SECRET_ACCESS_KEY (the R2 IAM key!)
        # Must use the dedicated webhook signing secret, NOT the storage credentials.
        secret = getattr(settings, 'CLOUDFLARE_WEBHOOK_SECRET', '').encode('utf-8')
        if not secret:
            logger.critical("[WEBHOOK] CLOUDFLARE_WEBHOOK_SECRET is not configured!")
            return Response({"error": "Server misconfiguration"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        expected_signature = hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()
        
        if not hmac.compare_digest(expected_signature, cloudflare_signature):
            logger.warning("Invalid Cloudflare Signature")
            return Response({"error": "Invalid signature"}, status=status.HTTP_403_FORBIDDEN)

        # 5. Parse JSON Payload
        try:
            payload = json.loads(payload_bytes)
        except json.JSONDecodeError:
            logger.error("Invalid JSON in webhook payload")
            return Response({"error": "Invalid JSON"}, status=status.HTTP_400_BAD_REQUEST)

        action = payload.get('action')
        r2_object_key = payload.get('r2_object_key')
        size = payload.get('size')
        
        # 6. Replay Protection (fail closed)
        webhook_timestamp_str = request.META.get('HTTP_Webhook-Timestamp') or request.META.get('HTTP_WEBHOOK_TIMESTAMP')
        if not webhook_timestamp_str:
            logger.warning("Missing webhook timestamp.")
            return Response({"error": "Missing timestamp"}, status=status.HTTP_403_FORBIDDEN)

        try:
            webhook_dt = timezone.datetime.fromtimestamp(int(webhook_timestamp_str), tz=timezone.utc)
        except ValueError:
            logger.warning("Invalid webhook timestamp: %s", webhook_timestamp_str)
            return Response({"error": "Invalid timestamp"}, status=status.HTTP_403_FORBIDDEN)

        if timezone.now() - webhook_dt > timedelta(minutes=5):
            logger.warning("Replay attack detected. Timestamp: %s", webhook_timestamp_str)
            return Response({"error": "Webhook expired"}, status=status.HTTP_403_FORBIDDEN)

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
                return Response({"status": "already_ready"}, status=status.HTTP_200_OK)

            if locked_asset.status != "QUARANTINED":
                locked_asset.status = "READY"
                locked_asset.is_processed = True
                locked_asset.save(update_fields=['status', 'is_processed'])

        logger.info("Asset %s successfully marked as READY", r2_object_key)
        return Response({"status": "success"}, status=status.HTTP_200_OK)
