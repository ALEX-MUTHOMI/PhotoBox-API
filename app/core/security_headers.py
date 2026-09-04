"""API-wide security response headers (CSP + nosniff) for every response."""
from __future__ import annotations

from django.core.exceptions import DisallowedHost, SuspiciousOperation
from django.http import JsonResponse

API_CSP = (
    "default-src 'none'; "
    "script-src 'none'; "
    "style-src 'none'; "
    "img-src 'none'; "
    "font-src 'none'; "
    "connect-src 'none'; "
    "media-src 'none'; "
    "object-src 'none'; "
    "frame-src 'none'; "
    "worker-src 'none'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)


def apply_api_security_headers(response):
    """Attach headers safe for JSON APIs. Idempotent."""
    response.setdefault("Content-Security-Policy", API_CSP)
    response.setdefault("X-Content-Type-Options", "nosniff")
    response.setdefault("Referrer-Policy", "no-referrer")
    # Do not leak runserver / container version strings.
    response["Server"] = "PhotoBox"
    return response


class ApiSecurityHeadersMiddleware:
    """Apply CSP + nosniff on all responses, including CORS OPTIONS and Django handlers.

    Also convert DisallowedHost to JSON 400 — Django's default is HTML and
    happens before urlconf handler400.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            response = self.get_response(request)
        except (DisallowedHost, SuspiciousOperation):
            response = JsonResponse({"detail": "Bad request."}, status=400)
            return apply_api_security_headers(response)
        return apply_api_security_headers(response)
