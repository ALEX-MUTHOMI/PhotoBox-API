from rest_framework.permissions import BasePermission


class HasGalleryAccessRole(BasePermission):
    allowed_roles = tuple()

    def has_permission(self, request, view):
        if not request.user or not getattr(request.user, 'is_authenticated', False):
            return False

        if not self.allowed_roles:
            return True

        return getattr(request.user, 'role', None) in self.allowed_roles


class HasClientGalleryAccess(HasGalleryAccessRole):
    allowed_roles = ('CLIENT',)


class HasClientOrGuestGalleryAccess(HasGalleryAccessRole):
    allowed_roles = ('CLIENT', 'GUEST')
