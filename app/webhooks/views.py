"""
Legacy Cloudflare R2 webhook ingress — delegates to the canonical ingestion handler.

POST /api/v1/webhooks/cloudflare/r2/ remains wired for existing Cloudflare notification
rules. All logic lives in ingestion.views.R2WebhookView.
"""
import logging

from ingestion.views import R2WebhookView

logger = logging.getLogger(__name__)


class CloudflareWebhookView(R2WebhookView):
    """Deprecation shim: one implementation, two URL names."""

    def dispatch(self, request, *args, **kwargs):
        logger.warning(
            "[DEPRECATED-ROUTE] POST /api/v1/webhooks/cloudflare/r2/ — "
            "repoint the Cloudflare notification rule to /api/v1/ingestion/webhook/"
        )
        return super().dispatch(request, *args, **kwargs)
