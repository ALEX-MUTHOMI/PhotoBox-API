"""
gallery/throttles.py
"""
import hashlib

from rest_framework.throttling import BaseThrottle, SimpleRateThrottle, UserRateThrottle

from core.rate_limiter import consume_sliding_window
from gallery.client_auth import get_gallery_access_session_id


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
    """Authenticated favorites: key by gallery + session (venue NAT safe)."""

    scope = 'favorite_selection'

    def get_cache_key(self, request, view):
        gallery_id = view.kwargs.get('gallery_id', '')
        try:
            session_id = get_gallery_access_session_id(request)
        except Exception:
            session_id = None
        if session_id is None:
            return None
        return self.cache_format % {
            'scope': self.scope,
            'ident': f"{gallery_id}:{session_id}",
        }


class GallerySessionReadThrottle(SimpleRateThrottle):
    """Authenticated gallery GET: gallery_id + session_id, not IP-only."""

    scope = 'gallery_session_read'

    def get_cache_key(self, request, view):
        gallery_id = view.kwargs.get('gallery_id', '')
        try:
            session_id = get_gallery_access_session_id(request)
        except Exception:
            session_id = None
        if session_id is None:
            return None
        return self.cache_format % {
            'scope': self.scope,
            'ident': f"{gallery_id}:{session_id}",
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
    """Token stuffing vs venue NAT: key ip + token hash prefix (never raw token)."""

    scope = "magic_link_consume"
    limit = 10
    window_seconds = 300
    fail_open = False

    def get_ident_suffix(self, request, view) -> str:
        raw_token = ""
        try:
            data = getattr(request, "data", None)
            if data is not None:
                raw_token = data.get("token") or ""
        except Exception:
            raw_token = getattr(getattr(request, "POST", {}), "get", lambda *_: "")(
                "token"
            ) or ""
        token_digest = hashlib.sha256(
            str(raw_token).encode("utf-8")
        ).hexdigest()[:16]
        return f"{self.get_ident(request)}:{token_digest}"
