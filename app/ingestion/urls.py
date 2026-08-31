"""URL routes for bulk ingestion and the canonical R2 completion webhook."""

from django.urls import path
from .views import BulkIngestionView, R2WebhookView

urlpatterns = [
    path('bulk/', BulkIngestionView.as_view(), name='bulk-ingest'),

    # PHASE 2: Heavy Lane R2 completion webhook.
    # Cloudflare calls this endpoint when a file is successfully uploaded to R2.
    # Authentication: HMAC-SHA256 signature (not JWT — machine-to-machine).
    path('webhook/', R2WebhookView.as_view(), name='r2-ingestion-webhook'),
]
