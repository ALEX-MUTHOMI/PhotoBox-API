"""
gallery/throttles.py
"""
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle


class FastLaneUploadThrottle(UserRateThrottle):
    scope = 'fast_lane_upload'


class HeavyLaneTicketThrottle(UserRateThrottle):
    scope = 'heavy_lane_ticket'


class MagicLinkSendThrottle(SimpleRateThrottle):
    scope = 'magic_link_send'

    def get_cache_key(self, request, view):
        email = (request.data.get('email') or '').strip().lower()
        gallery_id = view.kwargs.get('gallery_id', '')
        ident = self.get_ident(request)
        return self.cache_format % {
            'scope': self.scope,
            'ident': f"{ident}:{gallery_id}:{email}",
        }


class GuestAccessThrottle(SimpleRateThrottle):
    scope = 'guest_access'

    def get_cache_key(self, request, view):
        gallery_id = view.kwargs.get('gallery_id', '')
        ident = self.get_ident(request)
        return self.cache_format % {
            'scope': self.scope,
            'ident': f"{ident}:{gallery_id}",
        }


class FavoriteSelectionThrottle(SimpleRateThrottle):
    scope = 'favorite_selection'

    def get_cache_key(self, request, view):
        gallery_id = view.kwargs.get('gallery_id', '')
        ident = self.get_ident(request)
        email = getattr(request.user, 'email', '')
        role = getattr(request.user, 'role', '')
        return self.cache_format % {
            'scope': self.scope,
            'ident': f"{ident}:{gallery_id}:{email}:{role}",
        }
