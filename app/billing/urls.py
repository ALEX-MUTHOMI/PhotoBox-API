"""URL routes for billing subscription status and related API endpoints."""

from django.urls import path
from . import views

app_name = 'billing'

urlpatterns = [
    path('webhook/', views.WebhookReceiverView.as_view(), name='lemon_squeezy_webhook'),
    path('subscription/', views.SubscriptionStatusView.as_view(), name='subscription-status'),
]
