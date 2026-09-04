"""Reject credentialed CORS wildcards / preview hosts in production."""
from django.core.exceptions import ImproperlyConfigured


def assert_cors_origins_safe(origins, *, debug: bool) -> None:
    if debug:
        return
    for origin in origins:
        raw = (origin or "").strip()
        if "*" in raw or raw.endswith(".vercel.app") or raw.endswith(".netlify.app"):
            raise ImproperlyConfigured(
                "CORS_ALLOWED_ORIGINS must be exact origins in production "
                "(no wildcards or *.vercel.app / *.netlify.app)."
            )
