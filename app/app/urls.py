"""app URL Configuration"""
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
)

from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from django.conf import settings

from core import views as core_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/health-check/', core_views.health_check, name='health-check'),
    path('api/schema/', SpectacularAPIView.as_view(), name='api-schema'),
    path(
        'api/docs/',
        SpectacularRedocView.as_view(url_name='api-schema'),
        name='api-docs',
    ),
    path('api/user/', include('user.urls')),

    # --- PHOTOBOX SAAS ROUTING ---
    path('api/gallery/', include('gallery.urls')),

    path('api/billing/', include('billing.urls')),
]

# This is CRITICAL for PhotoBox image uploads later
if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
