"""Response headers that prevent CDN auth bleed (R2.3) and Referer leaks."""
from __future__ import annotations


GALLERY_CACHE_CONTROL = "private, no-store, max-age=0"
GALLERY_VARY = "Cookie"
GALLERY_REFERRER_POLICY = "no-referrer"


def apply_gallery_security_headers(response):
    response["Cache-Control"] = GALLERY_CACHE_CONTROL
    response["Vary"] = GALLERY_VARY
    response["Referrer-Policy"] = GALLERY_REFERRER_POLICY
    return response


class GallerySecurityHeadersMixin:
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        return apply_gallery_security_headers(response)
