"""
Core views for app.
"""
import hmac
import logging

from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from core.domain_index import get_workspace_id_by_domain

logger = logging.getLogger(__name__)


def health_check(request):
    """Readiness probe: DB and cache must respond."""
    checks = {}
    healthy = True

    try:
        connections['default'].ensure_connection()
        with connections['default'].cursor() as cursor:
            cursor.execute('SELECT 1')
        checks['database'] = 'ok'
    except Exception as exc:
        checks['database'] = str(exc)
        healthy = False

    try:
        cache.set('health_probe', '1', timeout=5)
        if cache.get('health_probe') != '1':
            raise RuntimeError('cache read/write mismatch')
        checks['cache'] = 'ok'
    except Exception as exc:
        checks['cache'] = str(exc)
        healthy = False

    payload = {
        'healthy': healthy,
        'checks': checks,
        'debug': settings.DEBUG,
    }
    status = 200 if healthy else 503
    return JsonResponse(payload, status=status)


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
