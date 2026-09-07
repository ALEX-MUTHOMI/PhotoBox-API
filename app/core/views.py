"""
Core views for app.
"""
import hmac
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.http import JsonResponse
from django.views.decorators.csrf import requires_csrf_token
from django.views.decorators.http import require_GET

from core.domain_index import get_workspace_id_by_domain
from core.security_headers import apply_api_security_headers

logger = logging.getLogger(__name__)


def _json_error(message: str, status: int) -> JsonResponse:
    response = JsonResponse({"detail": message}, status=status)
    return apply_api_security_headers(response)


def bad_request_json(request, exception=None):
    # DisallowedHost and other early 400s should still be JSON (no HTML recon).
    return _json_error("Bad request.", 400)


def permission_denied_json(request, exception=None):
    return _json_error("Forbidden.", 403)


def not_found_json(request, exception=None):
    return _json_error("Not found.", 404)


def server_error_json(request):
    # Must stay dumb — no DB/cache/template — or Django falls back to HTML.
    return _json_error("Server error.", 500)


@requires_csrf_token
def csrf_failure_json(request, reason=""):
    return _json_error("CSRF verification failed.", 403)


def health_check(request):
    """Readiness probe: DB and cache must respond. Does not leak DEBUG."""
    checks = {}
    healthy = True

    try:
        connections['default'].ensure_connection()
        with connections['default'].cursor() as cursor:
            cursor.execute('SELECT 1')
        checks['database'] = 'ok'
    except Exception as exc:
        checks['database'] = 'error'
        logger.warning("health_check database failed: %s", exc)
        healthy = False

    try:
        cache.set('health_probe', '1', timeout=5)
        if cache.get('health_probe') != '1':
            raise RuntimeError('cache read/write mismatch')
        checks['cache'] = 'ok'
    except Exception as exc:
        checks['cache'] = 'error'
        logger.warning("health_check cache failed: %s", exc)
        healthy = False

    payload = {
        'healthy': healthy,
        'checks': checks,
    }
    status = 200 if healthy else 503
    return apply_api_security_headers(JsonResponse(payload, status=status))


@require_GET
def liveness_probe(request):
    """Zero-dependency liveness for container orchestrators (no DB/cache)."""
    from django.http import HttpResponse

    return apply_api_security_headers(
        HttpResponse("ok", content_type="text/plain", status=200)
    )


@require_GET
def resolve_domain(request):
    """Edge tenant resolution for cloudflare-workers/domain-router.js."""
    expected = getattr(settings, 'CLOUDFLARE_WORKER_SHARED_SECRET', '')
    if not expected:
        logger.critical(
            "[DOMAIN-ROUTER] CLOUDFLARE_WORKER_SHARED_SECRET is unset."
        )
        return JsonResponse({'detail': 'Domain resolution unavailable.'}, status=503)

    presented = request.META.get('HTTP_X_WORKER_SECRET', '')
    if not hmac.compare_digest(presented, expected):
        logger.warning("[DOMAIN-ROUTER] Rejected request with invalid worker secret.")
        return JsonResponse({'detail': 'Forbidden.'}, status=403)

    domain = (request.GET.get('domain') or '').strip()
    if not domain:
        return JsonResponse({'detail': 'Query parameter "domain" is required.'}, status=400)

    workspace_id = get_workspace_id_by_domain(domain)
    return JsonResponse({'workspace_id': workspace_id or ''}, status=200)
