"""
Views for the billing API.
"""

import hmac
import hashlib
import json
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from rest_framework_simplejwt.authentication import JWTAuthentication
from drf_spectacular.utils import extend_schema

from gallery.permissions import IsPhotographerUser

from .models import Subscription
from .serializers import SubscriptionSerializer
from .tasks import process_lemon_squeezy_webhook
from .daraja import consume_daraja_callback_token, record_daraja_webhook_once
from gallery.client_ip import get_request_client_ip


def _webhook_rejected(http_status):
    return Response({"detail": "Webhook rejected."}, status=http_status)


def _daraja_ip_allowed(request) -> bool:
    allowlist = getattr(settings, "DARAJA_CALLBACK_IP_ALLOWLIST", None) or []
    if not allowlist:
        return True
    return get_request_client_ip(request) in allowlist


# ==========================================
# MODULE 1: THE HIGH-CONCURRENCY PAYMENT GATEWAY
# ==========================================
class WebhookReceiverView(APIView):
    """
    Lightweight Bouncer: Validates Lemon Squeezy cryptography and instantly
    offloads business logic to Celery. Replay attacks are handled by the Task ledger.
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        # 0. OOM DEFENSE: Reject absurdly large payloads before any processing
        content_length = request.META.get('CONTENT_LENGTH')
        if content_length and int(content_length) > 1 * 1024 * 1024:  # 1MB max
            return _webhook_rejected(status.HTTP_400_BAD_REQUEST)

        raw_payload = request.body
        incoming_signature = request.META.get('HTTP_X_SIGNATURE', '')
        event_id = request.META.get('HTTP_X_EVENT_ID')

        if not incoming_signature or not event_id:
            return _webhook_rejected(status.HTTP_401_UNAUTHORIZED)

        # ENGINEER FIX: Zero-Downtime Key Rotation Support
        primary_secret = getattr(settings, 'LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY', '').encode('utf-8')
        secondary_secret = getattr(settings, 'LEMON_SQUEEZY_WEBHOOK_SECRET_SECONDARY', '').encode('utf-8')

        # SECURITY FIX: If BOTH secrets are empty, reject ALL webhooks.
        # Empty HMAC matches empty HMAC — this is a full authentication bypass.
        if not primary_secret and not secondary_secret:
            return Response({"detail": "Webhook unavailable."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        primary_signature = hmac.new(primary_secret, raw_payload, hashlib.sha256).hexdigest() if primary_secret else ''

        # Check primary key first (Constant-time comparison prevents timing attacks)
        if not primary_secret or not hmac.compare_digest(incoming_signature, primary_signature):
            # Fallback to secondary key if primary fails
            secondary_signature = hmac.new(secondary_secret, raw_payload, hashlib.sha256).hexdigest() if secondary_secret else ''
            if not secondary_secret or not hmac.compare_digest(incoming_signature, secondary_signature):
                return _webhook_rejected(status.HTTP_401_UNAUTHORIZED)

        try:
            payload_data = json.loads(raw_payload)
        except json.JSONDecodeError:
            return _webhook_rejected(status.HTTP_400_BAD_REQUEST)

        # The event id header is not authenticated by the HMAC, so never trust it
        # as the sole idempotency key. We derive a deterministic payload hash and
        # let the task use that for replay protection.
        payload_hash = hashlib.sha256(raw_payload).hexdigest()

        # Async Handoff
        process_lemon_squeezy_webhook.delay(payload_data, event_id, payload_hash)
        return Response("Payload queued for processing.", status=status.HTTP_202_ACCEPTED)


@extend_schema(exclude=True)
class DarajaCallbackView(APIView):
    """
    Safaricom STK callback stub (Phase D).

    Auth is secret_token (query) + optional IP allowlist — not an HMAC header.
    Never fail-open when the token is missing.
    """
    authentication_classes = []
    permission_classes = []

    @extend_schema(exclude=True)
    def post(self, request):
        if not _daraja_ip_allowed(request):
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        raw_token = request.query_params.get("secret_token") or request.data.get("secret_token")
        token_row = consume_daraja_callback_token(raw_token)
        if token_row is None:
            return Response({"detail": "Forbidden."}, status=status.HTTP_403_FORBIDDEN)

        body = request.data if isinstance(request.data, dict) else {}
        checkout_id = (
            body.get("CheckoutRequestID")
            or body.get("checkout_request_id")
            or token_row.checkout_request_id
            or ""
        )
        result_code = body.get("ResultCode", body.get("result_code"))
        provider_event_id = f"daraja:{checkout_id}:{result_code}:{token_row.token_hash[:16]}"

        if not record_daraja_webhook_once(provider_event_id):
            # Idempotent replay — acknowledge without mutating quota again.
            return Response({"detail": "Already processed."}, status=status.HTTP_200_OK)

        # STK success path is intentionally a stub: ledger mutation lands with KES product.
        return Response({"detail": "Accepted."}, status=status.HTTP_202_ACCEPTED)


class SubscriptionStatusView(generics.RetrieveAPIView):
    """GET-only photographer subscription and quota read model."""

    serializer_class = SubscriptionSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsPhotographerUser]

    def get_object(self):
        return Subscription.objects.select_related('user__workspace').get(
            user=self.request.user
        )
