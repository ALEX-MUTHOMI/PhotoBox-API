"""
URL mappings for the gallery app.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from gallery import views

# The DefaultRouter automatically generates the RESTful URLs for our ViewSets.
# It handles:
# GET /galleries/         -> List all
# POST /galleries/        -> Create
# GET /galleries/<id>/    -> Retrieve
# DELETE /galleries/<id>/ -> Soft-Delete (via our View override)

router = DefaultRouter()

# ==========================================
# 1. GALLERY ROUTES
# ==========================================
router.register('galleries', views.GalleryViewSet)

# ==========================================
# 2. IMAGE HANDLING ROUTES
# ==========================================
router.register('images', views.ImageViewSet)

app_name = 'gallery'

urlpatterns = [
    path('', include(router.urls)),
]
