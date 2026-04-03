"""
Views for the Gallery API.
"""
from rest_framework import viewsets
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated

from core.models import Gallery, Workspace
from gallery import serializers

class GalleryViewSet(viewsets.ModelViewSet):
    """Viewset for managing gallery resources."""
    serializer_class = serializers.GallerySerializer
    queryset = Gallery.objects.all()

    authentication_classes = [TokenAuthentication]
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
        This sends it to the 30-day trash can instead of nuking the database.
        """
        instance.is_deleted = True
        instance.save()
