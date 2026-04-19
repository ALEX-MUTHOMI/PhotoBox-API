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
    # THE PHOTOGRAPHER DASHBOARD
    # Server-side rendered entry point for the photographer's management UI.
    # -------------------------------------------------------------------------
    path('dashboard/', views.PhotographerDashboardView.as_view(), name='dashboard'),

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
    path('', include(router.urls)),
]
# """
# URL mappings for the gallery app (The Pixieset Standard).
# """
# from django.urls import path, include
# from rest_framework.routers import DefaultRouter

# from gallery import views

# router = DefaultRouter()

# # ==========================================
# # 1. PIXIESET STANDARD: EVENT (The Collection)
# # ==========================================
# router.register('events', views.EventViewSet)

# # ==========================================
# # 2. PIXIESET STANDARD: THE STAGE (Scenes / Tabs)
# # ==========================================
# router.register('scenes', views.SceneViewSet)

# # ==========================================
# # 3. FAST LANE: SYNCHRONOUS ASSET HANDLER
# # ==========================================
# # Handles synchronous, small HTTP binary uploads only (<5MB)
# # basename is set explicitly to prevent ambiguous URL name inference across Django versions.
# router.register('fast-lane/photos', views.PhotoFastLaneViewSet, basename='photo')

# app_name = 'gallery'

# urlpatterns = [
#     # THE FRONTEND DASHBOARD
#     path('dashboard/', views.PhotographerDashboardView.as_view(), name='dashboard'),
    
#     # THE API ROUTER
#     path('', include(router.urls)),
# ]
