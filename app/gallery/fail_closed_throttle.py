"""Fail-closed SimpleRateThrottle: cache/Redis errors deny (429), never 500 or open."""
from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle


class FailClosedSimpleRateThrottle(SimpleRateThrottle):
    """
    Unauthenticated limiter base.

    DRF SimpleRateThrottle re-raises cache errors which become 500s and open a
    PIN / magic-link / share-code probe window. Catch ConnectionError/TimeoutError
    (and any Exception from the cache backend) and deny the request.
    """

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except (ConnectionError, TimeoutError, OSError):
            self.wait()
            return False
        except Exception:
            # LocMem / Redis backend failures mid-get/set — fail closed.
            return False

    def wait(self):
        try:
            return super().wait()
        except Exception:
            return 60.0
