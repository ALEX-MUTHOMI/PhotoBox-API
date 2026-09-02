"""DRF throttles for password-reset request and confirm endpoints."""

from rest_framework.throttling import SimpleRateThrottle


class PasswordResetRequestThrottle(SimpleRateThrottle):
    scope = "password_reset_request"

    def get_cache_key(self, request, view):
        email = (request.data.get("email") or "").strip().lower()
        ident = self.get_ident(request)
        return self.cache_format % {
            "scope": self.scope,
            "ident": f"{ident}:{email}",
        }
