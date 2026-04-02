"""
Views for the gallery APIs.
"""
from rest_framework import viewsets
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated

from core.models import Gallery
from gallery import serializers


class GalleryViewSet(viewsets.ModelViewSet):
    """View for managing gallery APIs."""
    serializer_class = serializers.GallerySerializer
    queryset = Gallery.objects.all()

    # SECURITY GUARDRAILS: Must be logged in with a valid token
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Retrieve galleries strictly for the authenticated user's workspace."""
        # This is the Tenant Isolation lock. It navigates the relationships:
        # Gallery -> Workspace -> User
        return self.queryset.filter(
            workspace__user=self.request.user
        ).order_by('-created_at')
