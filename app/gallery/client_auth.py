"""Gallery access sessions, magic links, and cookie-backed JWT helpers."""

import hashlib
from dataclasses import dataclass
from datetime import timedelta

import jwt
from django.conf import settings
from django.core import signing
from django.utils import timezone
from rest_framework import authentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied


COOKIE_NAME = 'gallery_access'
SESSION_COOKIE_NAME = 'gallery_session'
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600
DEFAULT_GUEST_TOKEN_LIFETIME_SECONDS = 7 * 24 * 3600
DEFAULT_CLIENT_TOKEN_LIFETIME_SECONDS = 1800
COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SALT = 'gallery-access-session'


def _cookie_name() -> str:
    return getattr(settings, 'GALLERY_ACCESS_COOKIE_NAME', COOKIE_NAME)


def _token_lifetime_seconds(role: str | None = None) -> int:
    if role == 'GUEST':
        return int(
            getattr(
                settings,
                'GALLERY_GUEST_ACCESS_TOKEN_LIFETIME_SECONDS',
                DEFAULT_GUEST_TOKEN_LIFETIME_SECONDS,
            )
        )
    if role == 'CLIENT':
        return int(
            getattr(
                settings,
                'GALLERY_CLIENT_ACCESS_TOKEN_LIFETIME_SECONDS',
                DEFAULT_CLIENT_TOKEN_LIFETIME_SECONDS,
            )
        )
    return int(
        getattr(
            settings,
            'GALLERY_ACCESS_TOKEN_LIFETIME_SECONDS',
            DEFAULT_TOKEN_LIFETIME_SECONDS,
        )
    )


def _session_cookie_name() -> str:
    return getattr(settings, 'GALLERY_ACCESS_SESSION_COOKIE_NAME', SESSION_COOKIE_NAME)


def normalize_gallery_email(email: str) -> str:
    return (email or '').strip().lower()


def hash_magic_link_token(raw_token: str) -> str:
    return hashlib.sha256((raw_token or '').encode('utf-8')).hexdigest()


def issue_gallery_access_token(
    gallery_id,
    email: str,
    role: str,
    *,
    pin_version: int = 0,
) -> str:
    now = timezone.now()
    lifetime = _token_lifetime_seconds(role)
    payload = {
        'gallery_id': str(gallery_id),
        'email': normalize_gallery_email(email),
        'role': role,
        'pv': int(pin_version or 0),
        'iat': int(now.timestamp()),
        'exp': int((now + timedelta(seconds=lifetime)).timestamp()),
    }
    return jwt.encode(
        payload,
        getattr(settings, 'JWT_SIGNING_KEY', settings.SECRET_KEY),
        algorithm='HS256',
    )


def decode_gallery_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            getattr(settings, 'JWT_SIGNING_KEY', settings.SECRET_KEY),
            algorithms=['HS256'],
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationFailed('Gallery access token expired.') from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationFailed('Invalid gallery access token.') from exc

    required = {'gallery_id', 'email', 'role'}
    missing = required.difference(payload.keys())
    if missing:
        raise AuthenticationFailed(f'Gallery access token missing claims: {sorted(missing)}')

    return payload


@dataclass(frozen=True)
class GalleryAccessPrincipal:
    gallery_id: str
    email: str
    role: str
    pin_version: int = 0

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def pk(self) -> str:
        return f"{self.gallery_id}:{self.email}:{self.role}"

    def __str__(self) -> str:
        return f'{self.email} [{self.role}]'


class GalleryCookieJWTAuthentication(authentication.BaseAuthentication):
    """
    Cookie/header auth for gallery-scoped JWTs.

    These tokens are intentionally separate from photographer JWTs so a leaked
    client token cannot be used against photographer/admin APIs.
    """

    def authenticate(self, request):
        token = request.COOKIES.get(_cookie_name())

        header = authentication.get_authorization_header(request).decode('utf-8')
        if not token and header.startswith('Bearer '):
            token = header.split(' ', 1)[1].strip()

        if not token:
            return None

        payload = decode_gallery_access_token(token)
        role = payload['role']
        pin_version = int(payload.get('pv', 0) or 0)

        if role == 'GUEST':
            from gallery.models import Event

            event = (
                Event.objects.filter(id=payload['gallery_id'])
                .only('pin_version')
                .first()
            )
            if event is None or int(event.pin_version or 0) != pin_version:
                raise AuthenticationFailed('Gallery PIN was rotated. Please re-enter the PIN.')

        principal = GalleryAccessPrincipal(
            gallery_id=str(payload['gallery_id']),
            email=normalize_gallery_email(payload['email']),
            role=role,
            pin_version=pin_version,
        )
        return principal, payload


def set_gallery_access_cookie(response, token: str, *, role: str | None = None):
    max_age = _token_lifetime_seconds(role)
    response.set_cookie(
        key=_cookie_name(),
        value=token,
        max_age=max_age,
        secure=bool(getattr(settings, 'SESSION_COOKIE_SECURE', True)),
        httponly=True,
        samesite=getattr(settings, 'GALLERY_ACCESS_COOKIE_SAMESITE', COOKIE_SAMESITE),
    )
    return response


def clear_gallery_access_cookie(response):
    response.delete_cookie(
        _cookie_name(),
        samesite=getattr(settings, 'GALLERY_ACCESS_COOKIE_SAMESITE', COOKIE_SAMESITE),
    )
    return response


def set_gallery_access_session_cookie(response, session_id: int, *, role: str | None = None):
    max_age = _token_lifetime_seconds(role)
    signed_value = encode_gallery_access_session_cookie(session_id)
    response.set_cookie(
        key=_session_cookie_name(),
        value=signed_value,
        max_age=max_age,
        secure=bool(getattr(settings, 'SESSION_COOKIE_SECURE', True)),
        httponly=True,
        samesite=getattr(settings, 'GALLERY_ACCESS_COOKIE_SAMESITE', COOKIE_SAMESITE),
    )
    return response


def clear_gallery_access_session_cookie(response):
    response.delete_cookie(
        _session_cookie_name(),
        samesite=getattr(settings, 'GALLERY_ACCESS_COOKIE_SAMESITE', COOKIE_SAMESITE),
    )
    return response


def encode_gallery_access_session_cookie(session_id: int) -> str:
    return signing.dumps(
        {'session_id': session_id},
        salt=SESSION_COOKIE_SALT,
        compress=True,
    )


def get_gallery_access_session_id(request) -> int | None:
    raw_value = request.COOKIES.get(_session_cookie_name())
    if not raw_value:
        return None

    # Use the longer of guest/client lifetimes so session cookie validation
    # does not reject a still-valid guest JWT early.
    max_age = max(
        _token_lifetime_seconds('GUEST'),
        _token_lifetime_seconds('CLIENT'),
        _token_lifetime_seconds(None),
    )
    try:
        payload = signing.loads(
            raw_value,
            salt=SESSION_COOKIE_SALT,
            max_age=max_age,
        )
    except signing.SignatureExpired as exc:
        raise AuthenticationFailed('Gallery session expired.') from exc
    except signing.BadSignature as exc:
        raise AuthenticationFailed('Invalid gallery session cookie.') from exc

    session_id = payload.get('session_id')
    if not isinstance(session_id, int):
        raise AuthenticationFailed('Invalid gallery session cookie payload.')

    return session_id


def resolve_gallery_access_session(request, gallery):
    """Resolve the live session behind a client JWT and re-authorise it."""
    from gallery.models import ClientAllowlist, GalleryAccessRole, GalleryAccessSession

    session_id = get_gallery_access_session_id(request)
    if session_id is None:
        raise PermissionDenied("Gallery session missing.")

    session = (
        GalleryAccessSession.objects
        .filter(
            id=session_id,
            gallery=gallery,
            email=normalize_gallery_email(getattr(request.user, "email", "")),
            role=getattr(request.user, "role", ""),
        )
        .first()
    )
    if not session:
        raise PermissionDenied("Gallery session invalid.")

    if session.role == GalleryAccessRole.CLIENT and not ClientAllowlist.objects.filter(
        gallery=gallery,
        email=session.email,
    ).exists():
        raise PermissionDenied("Gallery access revoked.")

    return session
