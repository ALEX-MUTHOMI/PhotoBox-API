"""DRF permission classes for photographer-scoped gallery API routes."""

from rest_framework.permissions import IsAuthenticated


class IsPhotographerUser(IsAuthenticated):
    """Reject gallery-client JWTs that carry a scoped gallery_id claim."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False

        return getattr(request.user, "gallery_id", None) is None
