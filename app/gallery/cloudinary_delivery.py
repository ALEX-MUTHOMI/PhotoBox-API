"""Cloudinary Fetch URL builder — sized, signed/strict transforms; never RAW originals."""
from __future__ import annotations

import hashlib
import hmac

from django.conf import settings


TILE_WIDTH = 480
LIGHTBOX_WIDTH = 1600
# Exact allowlisted transform strings (Strict Transformations / signed).
TILE_TRANSFORM = f"w_{TILE_WIDTH},c_limit,q_auto,f_webp"
LIGHTBOX_TRANSFORM = f"w_{LIGHTBOX_WIDTH},c_limit,q_auto,f_webp"


def _r2_public_host() -> str:
    return (getattr(settings, "CLOUDFLARE_R2_DOMAIN", "") or "").rstrip("/")


def is_safe_r2_object_key(key: str | None) -> bool:
    if not key or not isinstance(key, str):
        return False
    if "://" in key or ".." in key or key.startswith("/") or "\\" in key:
        return False
    return True


def build_r2_public_url(object_key: str) -> str | None:
    host = _r2_public_host()
    if not host or not is_safe_r2_object_key(object_key):
        return None
    return f"https://{host}/{object_key.lstrip('/')}"


def _sign_fetch_url(cloud_name: str, transform: str, r2_public_url: str) -> str | None:
    """
    Build a Cloudinary fetch URL. Prefer cloudinary SDK signing when credentials
    exist; otherwise append an HMAC over the allowlisted transform + source URL
    so arbitrary e_* mutations are detectable in unit tests / staging.
    """
    api_secret = getattr(settings, "CLOUDINARY_API_SECRET", "") or ""
    api_key = getattr(settings, "CLOUDINARY_API_KEY", "") or ""

    if api_secret and api_key:
        try:
            import cloudinary
            from cloudinary.utils import cloudinary_url

            cloudinary.config(
                cloud_name=cloud_name,
                api_key=api_key,
                api_secret=api_secret,
                secure=True,
            )
            url, _options = cloudinary_url(
                r2_public_url,
                type="fetch",
                raw_transformation=transform,
                sign_url=True,
                secure=True,
            )
            return url
        except Exception:
            pass

    # Deterministic signed path without SDK: s--{sig}-- style fragment for tests.
    to_sign = f"{transform}/{r2_public_url}"
    digest = hmac.new(
        (api_secret or getattr(settings, "SECRET_KEY", "dev")).encode("utf-8"),
        to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:8]
    return (
        f"https://res.cloudinary.com/{cloud_name}"
        f"/image/fetch/s--{digest}--/{transform}/{r2_public_url}"
    )


def build_delivery_url(*, object_key: str, transform: str) -> str | None:
    cloud_name = getattr(settings, "CLOUDINARY_CLOUD_NAME", "") or ""
    if not cloud_name:
        return None
    r2_public_url = build_r2_public_url(object_key)
    if not r2_public_url:
        return None
    if transform not in {TILE_TRANSFORM, LIGHTBOX_TRANSFORM}:
        raise ValueError("Transform not in allowlist.")
    return _sign_fetch_url(cloud_name, transform, r2_public_url)


def build_tile_url(object_key: str) -> str | None:
    return build_delivery_url(object_key=object_key, transform=TILE_TRANSFORM)


def build_lightbox_url(object_key: str) -> str | None:
    return build_delivery_url(object_key=object_key, transform=LIGHTBOX_TRANSFORM)
