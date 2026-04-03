"""
URL mappings for the gallery app.
"""
from django.urls import (
    path,
    include,
)
from rest_framework.routers import DefaultRouter

from gallery import views

# The DefaultRouter automatically generates the URLs for our ViewSet
# It handles GET, POST, PUT, PATCH, and DELETE without us writing 5 different paths.
router = DefaultRouter()
router.register('galleries', views.GalleryViewSet)

app_name = 'gallery'

urlpatterns = [
    path('', include(router.urls)),
]
