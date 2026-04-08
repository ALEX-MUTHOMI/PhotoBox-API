from django.urls import path
from . import views

app_name = 'billing'

urlpatterns = [
    # The Lemon Squeezy Bridge
    path('webhook/', views.WebhookReceiverView.as_view(), name='lemon_squeezy_webhook'),

    # The Quota Vault
    path('gallery/upload/', views.GalleryUploadView.as_view(), name='gallery_upload'),
]
