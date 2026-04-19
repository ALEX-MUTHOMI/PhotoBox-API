"""
gallery/throttles.py — Custom Per-Scope Rate Limiters

Extends DRF's UserRateThrottle with scopes defined in settings.REST_FRAMEWORK
so each endpoint has an independent counter bucket in the cache backend.

Usage in views:
    throttle_classes = [FastLaneUploadThrottle]

Rates are configured in settings.py:
    'DEFAULT_THROTTLE_RATES': {
        'fast_lane_upload': '30/minute',
        'heavy_lane_ticket': '10/minute',
    }
"""
from rest_framework.throttling import UserRateThrottle


class FastLaneUploadThrottle(UserRateThrottle):
    """
    30 uploads per minute per authenticated user.
    Prevents the Fast Lane from being used as a file-flooding vector
    even if the 5MB gate and quota gate are somehow bypassed.
    """
    scope = 'fast_lane_upload'


class HeavyLaneTicketThrottle(UserRateThrottle):
    """
    10 manifest submissions per minute per authenticated user.
    Each manifest can contain up to 2000 files — this throttle prevents
    an attacker from generating millions of presigned URLs to enumerate R2.
    """
    scope = 'heavy_lane_ticket'
