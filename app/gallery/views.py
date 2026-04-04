"""
Views for the Gallery API.
"""
from rest_framework import viewsets, parsers
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

from core.models import Gallery, Workspace, Image
from gallery import serializers


# ==========================================
# 1. GALLERY MANAGEMENT
# ==========================================

class GalleryViewSet(viewsets.ModelViewSet):
    """Viewset for managing gallery resources."""
    serializer_class = serializers.GallerySerializer
    queryset = Gallery.objects.all()

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Retrieve galleries limited to the authenticated user's workspace."""
        # SAAS UPGRADE: Enforce tenant isolation AND hide soft-deleted items
        return self.queryset.filter(
            workspace__user=self.request.user,
            is_deleted=False
        ).order_by('-created_at')

    def perform_create(self, serializer):
        """Create a new gallery associated with the user's workspace."""
        workspace = Workspace.objects.get(user=self.request.user)
        serializer.save(workspace=workspace)

    def perform_destroy(self, instance):
        """
        SAAS SECURITY OVERRIDE:
        Intercept the standard DELETE request and replace it with a Soft Delete.
        This sends it to the 7-day trash can instead of nuking the database.
        """
        instance.is_deleted = True
        instance.save()


# ==========================================
# 2. IMAGE UPLOAD & HANDLING.
# ==========================================

class ImageViewSet(viewsets.ModelViewSet):
    """Viewset for uploading and managing image files."""
    serializer_class = serializers.ImageSerializer
    queryset = Image.objects.all()

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    # CRITICAL: Django must know how to parse binary multipart file uploads natively
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def get_queryset(self):
        """Retrieve active images, enforce the soft-delete cascade, and allow filtering."""

        # 1. THE CASCADE RULE:
        # Only return the image if BOTH the image AND its parent gallery are active.
        queryset = self.queryset.filter(
            gallery__workspace__user=self.request.user,
            is_deleted=False,
            gallery__is_deleted=False
        )

        # 2. THE REACT OPTIMIZATION RULE:
        # If the React frontend asks for a specific gallery (e.g., ?gallery=123), filter it.
        gallery_id = self.request.query_params.get('gallery')
        if gallery_id:
            queryset = queryset.filter(gallery_id=gallery_id)

        return queryset.order_by('order', '-created_at')

    def perform_create(self, serializer):
        """
        SECURITY: Prevent Cross-Tenant Hijacking.
        Ensure the user physically owns the gallery they are trying to inject an image into.
        """
        gallery = serializer.validated_data['gallery']

        if gallery.workspace.user != self.request.user:
            raise PermissionDenied("You do not have permission to upload to this gallery.")

        serializer.save()

    def perform_destroy(self, instance):
        """
        SAAS SECURITY OVERRIDE:
        Intercept standard DELETE to enforce the Trash Can recovery window.
        """
        instance.is_deleted = True
        instance.save()


















# """
# Views for the Gallery API.
# """
# from rest_framework import viewsets
# from rest_framework.authentication import TokenAuthentication
# from rest_framework.permissions import IsAuthenticated

# from core.models import Gallery, Workspace
# from gallery import serializers

# class GalleryViewSet(viewsets.ModelViewSet):
#     """Viewset for managing gallery resources."""
#     serializer_class = serializers.GallerySerializer
#     queryset = Gallery.objects.all()

#     authentication_classes = [TokenAuthentication]
#     permission_classes = [IsAuthenticated]

#     def get_queryset(self):
#         """Retrieve galleries limited to the authenticated user's workspace."""
#         # SAAS UPGRADE: Enforce tenant isolation AND hide soft-deleted items
#         return self.queryset.filter(
#             workspace__user=self.request.user,
#             is_deleted=False
#         ).order_by('-created_at')

#     def perform_create(self, serializer):
#         """Create a new gallery associated with the user's workspace."""
#         workspace = Workspace.objects.get(user=self.request.user)
#         serializer.save(workspace=workspace)

#     def perform_destroy(self, instance):
#         """
#         SAAS SECURITY OVERRIDE:
#         Intercept the standard DELETE request and replace it with a Soft Delete.
#         This sends it to the 30-day trash can instead of nuking the database.
#         """
#         instance.is_deleted = True
#         instance.save()
