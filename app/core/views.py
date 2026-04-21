import logging
from django.http import JsonResponse
from django.db import connection

logger = logging.getLogger(__name__)

def health_check(request):
    """Lightweight health check for Docker/load balancer probes."""
    try:
        connection.ensure_connection()
        return JsonResponse({'status': 'healthy'})
    except Exception as e:
        logger.critical("Health check failed: %s", str(e))
        return JsonResponse(
            {'status': 'unhealthy', 'reason': 'Database unavailable'},
            status=503
        )