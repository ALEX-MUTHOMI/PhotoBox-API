# user/exceptions.py
"""
Global DRF Exception Handler.

Registration (required in settings.py):
    REST_FRAMEWORK = {
        "EXCEPTION_HANDLER": "user.exceptions.custom_exception_handler",
    }

Responsibility matrix:
    SocialApp.DoesNotExist  → 400  Infrastructure misconfiguration
    OAuth2Error             → 401  Forged / invalid social token
    jwt.DecodeError         → 401  Malformed JWT in social auth flow
    Everything else         → delegated to DRF default handler

What this handler deliberately does NOT do:
    - Catch generic database errors (let DRF/Django handle those)
    - Suppress 500s for unknown exceptions (visibility > silence)
    - Catch exceptions from non-social-auth endpoints
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple, Type

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exception type resolution
#
# Why module-level resolution instead of local imports?
#   1. Import errors surface at startup, not randomly during request handling
#   2. isinstance() checks in the hot-path avoid repeated import machinery
#   3. @override_settings in tests doesn't affect class identity
#
# Why not string matching on __class__.__name__?
#   String matching is bypassable — any class named "DecodeError" from any
#   library would match, swallowing unrelated exceptions as fake 401s.
#   We import the exact type or create a private sentinel that never matches.
# ---------------------------------------------------------------------------

def _resolve_exception_type(
    module_path: str,
    attr_path: str,
) -> Type[Exception]:
    """
    Safely resolve an exception type by dotted path at module load time.

    If the library is not installed, returns a private sentinel class that
    will never be raised at runtime — isinstance() checks against it always
    return False cleanly.

    Args:
        module_path: dotted import path to the module, e.g. "jwt.exceptions"
        attr_path:   attribute chain on the module, e.g. "DecodeError"
                     supports dot-chained attrs: "SocialApp.DoesNotExist"

    Returns:
        The resolved exception class, or a private never-raised sentinel.
    """
    try:
        import importlib
        module = importlib.import_module(module_path)
        obj = module
        for attr in attr_path.split("."):
            obj = getattr(obj, attr)
        if not (isinstance(obj, type) and issubclass(obj, BaseException)):
            raise TypeError(f"{module_path}.{attr_path} is not an exception class.")
        return obj
    except (ImportError, AttributeError, TypeError) as exc:
        logger.debug(
            "[EXCEPTION HANDLER] Could not resolve %s.%s: %s. "
            "Using sentinel — this exception type will never match.",
            module_path,
            attr_path,
            exc,
        )
        # Private sentinel — unique per call, can never be raised externally
        return type(f"_Sentinel_{module_path}_{attr_path}", (Exception,), {})


# Resolved once at import time — zero overhead on every request
_SocialAppDoesNotExist: Type[Exception] = _resolve_exception_type(
    "allauth.socialaccount.models",
    "SocialApp.DoesNotExist",
)

_OAuth2Error: Type[Exception] = _resolve_exception_type(
    "allauth.socialaccount.providers.oauth2.client",
    "OAuth2Error",
)

_JWTDecodeError: Type[Exception] = _resolve_exception_type(
    "jwt.exceptions",
    "DecodeError",
)


# ---------------------------------------------------------------------------
# Sub-handlers
#
# Each sub-handler is responsible for exactly one exception family.
# This makes the handler testable in isolation and readable as a flow chart.
# ---------------------------------------------------------------------------

def _handle_social_app_not_configured(
    exc: Exception,
    request: Optional[Request],
) -> Response:
    """
    SocialApp.DoesNotExist → 400 Bad Request.

    This is a CLIENT error: the client called a social auth endpoint that
    is not configured on this server. It is NOT a server availability error
    (503) — the server is healthy, the feature is simply not enabled.

    503 would:
      - Trigger uptime monitors / PagerDuty alerts for a config problem
      - Tell attackers "retry later, the feature exists"
      - Confuse clients into retrying (503 implies transience)

    400 communicates: "this request cannot be fulfilled as sent."
    """
    path = getattr(request, "path", "unknown")
    method = getattr(request, "method", "unknown")

    logger.error(
        "[SOCIAL AUTH] SocialApp not configured. "
        "path=%s method=%s exc_type=%s "
        "Action: create SocialApp DB record or configure SOCIALACCOUNT_PROVIDERS.",
        path,
        method,
        type(exc).__qualname__,
    )
    return Response(
        {"detail": "Social authentication is not enabled on this server."},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _handle_invalid_social_token(
    exc: Exception,
    request: Optional[Request],
) -> Response:
    """
    OAuth2Error / JWT DecodeError → 401 Unauthorized.

    These exceptions indicate the client sent a forged, expired, or malformed
    social auth token. This is a security event — log it as a warning with
    enough context for a SOC analyst to investigate.

    We intentionally do NOT log the token value or exception message, as
    these may contain partial credential data.
    """
    path = getattr(request, "path", "unknown")
    method = getattr(request, "method", "unknown")

    # Extract the remote IP safely — handle proxied requests
    # X-Forwarded-For is trusted only if you have a reverse proxy that sets it.
    # In production behind nginx/Cloudflare, use the first IP in the chain.
    forwarded_for = ""
    if request is not None:
        meta = getattr(request, "META", {})
        forwarded_for = meta.get("HTTP_X_FORWARDED_FOR", "") or meta.get(
            "REMOTE_ADDR", ""
        )
        # Take only the first IP — the rest are appended by intermediate proxies
        forwarded_for = forwarded_for.split(",")[0].strip()

    logger.warning(
        "[SOCIAL AUTH DEFENSE] Invalid/forged token rejected. "
        "path=%s method=%s exc_module=%s exc_type=%s remote=%s",
        path,
        method,
        type(exc).__module__,   # distinguishes jwt.DecodeError from others
        type(exc).__qualname__,
        forwarded_for,
        # Deliberately omitting str(exc) — may contain partial token data
    )
    return Response(
        {"detail": "Invalid or expired social authentication token."},
        status=status.HTTP_401_UNAUTHORIZED,
    )


# ---------------------------------------------------------------------------
# Public handler — wired into REST_FRAMEWORK["EXCEPTION_HANDLER"]
# ---------------------------------------------------------------------------

# Routing table: (exception_type, handler_function)
# Order matters — more specific exceptions must come before broader ones.
_EXCEPTION_ROUTES: Tuple[Tuple[Type[Exception], Any], ...] = (
    (_SocialAppDoesNotExist, _handle_social_app_not_configured),
    (_OAuth2Error,           _handle_invalid_social_token),
    (_JWTDecodeError,        _handle_invalid_social_token),
)


def custom_exception_handler(
    exc: Exception,
    context: Dict[str, Any],
) -> Optional[Response]:
    """
    Global DRF exception handler.

    Intercepts social auth infrastructure and security exceptions before
    they reach DRF's default handler (which would produce 500s).

    Returns:
        Response  — exception was handled, use this response
        None      — exception was not handled here, DRF will re-raise as 500

    Adding new exception handlers:
        1. Resolve the type with _resolve_exception_type() at module level
        2. Write a _handle_* function with (exc, request) -> Response
        3. Add a row to _EXCEPTION_ROUTES
        Do NOT add isinstance() checks inline in this function.
    """
    request: Optional[Request] = context.get("request")

    # Route to the first matching handler
    for exc_type, handler in _EXCEPTION_ROUTES:
        if isinstance(exc, exc_type):
            return handler(exc, request)

    # Delegate to DRF default for all other exceptions.
    # Log unhandled non-DRF exceptions so we have visibility into
    # what is reaching this path in production.
    response = exception_handler(exc, context)

    if response is None:
        # DRF couldn't handle it either — Django will produce a 500.
        # Log here so we know which exception types need new routes.
        logger.error(
            "[EXCEPTION HANDLER] Unhandled exception will produce 500. "
            "exc_module=%s exc_type=%s path=%s",
            type(exc).__module__,
            type(exc).__qualname__,
            getattr(request, "path", "unknown"),
            exc_info=True,  # includes full traceback in log
        )

    return response