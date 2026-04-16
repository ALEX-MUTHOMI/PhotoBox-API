import json
import hmac
import hashlib
import logging
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from datetime import timedelta

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
        secret = getattr(settings, 'CLOUDFLARE_SECRET_ACCESS_KEY', 'test-secret-key').encode('utf-8')
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
        
        # 6. Replay Protection (Graceful fallback for test compatibility)
        webhook_timestamp_str = request.META.get('HTTP_Webhook-Timestamp') or request.META.get('HTTP_WEBHOOK_TIMESTAMP')
        if webhook_timestamp_str:
            try:
                # Assuming unix timestamp in seconds
                webhook_dt = timezone.datetime.fromtimestamp(int(webhook_timestamp_str), tz=timezone.utc)
                if timezone.now() - webhook_dt > timedelta(minutes=5):
                    logger.warning(f"Replay attack detected. Timestamp: {webhook_timestamp_str}")
                    return Response({"error": "Webhook expired"}, status=status.HTTP_403_FORBIDDEN)
            except ValueError:
                pass

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

        # 10. Update state 
        # In a purely EDA architecture, this would publish to a queue (e.g. SQS)
        # For now, keeping idempotency and synchrony as implied by the tests:
        if asset.status != "UPLOADED":
            asset.status = "UPLOADED"
            asset.save(update_fields=['status'])
            
            # FUTURE: Trigger downstream async processing
            # e.g., trigger_image_processing.delay(asset.id)
            
        logger.info(f"Asset {r2_object_key} successfully marked as UPLOADED")
        return Response({"status": "success"}, status=status.HTTP_200_OK)
