"""
Core views for app.
"""
from django.http import JsonResponse


def health_check(request):
    """Lightweight liveness probe for load balancers and Docker health checks."""
    return JsonResponse({'healthy': True})
