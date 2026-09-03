"""
URL mappings for the gallery app (The Pixieset Standard).

This module defines the routing for the photobox gallery system.
All API routes are namespaced under 'gallery:'.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from gallery import views

# Using DefaultRouter to provide the API root documentation in development
router = DefaultRouter()

# =============================================================================
# 1. PIXIESET STANDARD: EVENT (The Collection)
# =============================================================================
router.register(r'events', views.EventViewSet, basename='event')

# =============================================================================
# 2. PIXIESET STANDARD: THE STAGE (Scenes / Tabs)
# =============================================================================
router.register(r'scenes', views.SceneViewSet, basename='scene')

# =============================================================================
# 3. FAST LANE: SYNCHRONOUS ASSET MONITOR
# =============================================================================
# Handles synchronous upload tickets for small assets (<5MB).
#
# CRITICAL FOR TESTS:
# The basename is set to 'fastlane-photo' to ensure reverse('gallery:fastlane-photo-list')
# resolves correctly in the Pagination and Security test suites.
# =============================================================================
router.register(
    r'fast-lane/photos',
    views.PhotoFastLaneViewSet,
    basename='fastlane-photo'
)

app_name = 'gallery'

urlpatterns = [
    # -------------------------------------------------------------------------
    # PRODUCTION FIX: PRESIGNED URL ROUTE
    # Explicit route required by the download authorization test suite.
    # -------------------------------------------------------------------------
    path(
        'fast-lane/photos/<uuid:pk>/download-url/',
        views.PhotoFastLaneViewSet.as_view({'get': 'download_url'}),
        name='fastlane-photo-download-url'
    ),

    # -------------------------------------------------------------------------
    # THE API ROUTER
    # Includes all viewsets registered above.
    # -------------------------------------------------------------------------
    path(
        'events/<uuid:event_id>/allowlist/<int:pk>/',
        views.ClientAllowlistDetailView.as_view(),
        name='event-allowlist-detail',
    ),
    path(
        'events/<uuid:event_id>/allowlist/',
        views.ClientAllowlistListView.as_view(),
        name='event-allowlist',
    ),
    path(
        'workspace/',
        views.WorkspaceBrandingView.as_view(),
        name='workspace-branding',
    ),
    path('', include(router.urls)),
]
