from django.urls import path
from .views import CloudflareWebhookView

urlpatterns = [
    path('cloudflare/r2/', CloudflareWebhookView.as_view(), name='r2-webhook-ingress'),
]
