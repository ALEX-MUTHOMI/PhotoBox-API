"""
gallery/throttles.py
"""
from rest_framework.throttling import BaseThrottle, SimpleRateThrottle, UserRateThrottle

from core.rate_limiter import consume_sliding_window


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


class RedisSlidingWindowThrottle(BaseThrottle):
    """DRF throttle backed by the atomic Redis sliding window."""
    scope: str = ""
    limit: int = 10
    window_seconds: int = 300
    fail_open: bool = False

    def get_ident_suffix(self, request, view) -> str:
        return self.get_ident(request)

    def allow_request(self, request, view) -> bool:
        decision = consume_sliding_window(
            f"{self.scope}:{self.get_ident_suffix(request, view)}",
            limit=self.limit,
            window_seconds=self.window_seconds,
            fail_open=self.fail_open,
        )
        self._retry_after = decision.retry_after_seconds
        return decision.allowed

    def wait(self):
        return getattr(self, "_retry_after", float(self.window_seconds))


class MagicLinkConsumeThrottle(RedisSlidingWindowThrottle):
    scope = "magic_link_consume"
    limit = 10
    window_seconds = 300
