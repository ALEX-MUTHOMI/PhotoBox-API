"""
URL mappings for the gallery app (The Pixieset Standard).
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from gallery import views

router = DefaultRouter()

# ==========================================
# 1. PIXIESET STANDARD: EVENT (The Collection)
# ==========================================
router.register('events', views.EventViewSet)

# ==========================================
# 2. PIXIESET STANDARD: THE STAGE (Scenes / Tabs)
# ==========================================
router.register('scenes', views.SceneViewSet)

# ==========================================
# 3. FAST LANE: SYNCHRONOUS ASSET HANDLER
# ==========================================
# Handles synchronous, small HTTP binary uploads only (<5MB)
# basename is set explicitly to prevent ambiguous URL name inference across Django versions.
router.register('fast-lane/photos', views.PhotoFastLaneViewSet, basename='photo')

app_name = 'gallery'

urlpatterns = [
    # THE FRONTEND DASHBOARD
    path('dashboard/', views.PhotographerDashboardView.as_view(), name='dashboard'),
    
    # THE API ROUTER
    path('', include(router.urls)),
]
