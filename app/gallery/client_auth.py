import hashlib
from dataclasses import dataclass
from datetime import timedelta

import jwt
from django.conf import settings
from django.core import signing
from django.utils import timezone
from rest_framework import authentication
from rest_framework.exceptions import AuthenticationFailed


COOKIE_NAME = 'gallery_access'
SESSION_COOKIE_NAME = 'gallery_session'
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600
COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SALT = 'gallery-access-session'


def _cookie_name() -> str:
    return getattr(settings, 'GALLERY_ACCESS_COOKIE_NAME', COOKIE_NAME)


def _token_lifetime_seconds() -> int:
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


def issue_gallery_access_token(gallery_id, email: str, role: str) -> str:
    now = timezone.now()
    payload = {
        'gallery_id': str(gallery_id),
        'email': normalize_gallery_email(email),
        'role': role,
        'iat': int(now.timestamp()),
        'exp': int((now + timedelta(seconds=_token_lifetime_seconds())).timestamp()),
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
        principal = GalleryAccessPrincipal(
            gallery_id=str(payload['gallery_id']),
            email=normalize_gallery_email(payload['email']),
            role=payload['role'],
        )
        return principal, payload


def set_gallery_access_cookie(response, token: str):
    max_age = _token_lifetime_seconds()
    response.set_cookie(
        key=_cookie_name(),
        value=token,
        max_age=max_age,
        expires=max_age,
        secure=True,
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


def set_gallery_access_session_cookie(response, session_id: int):
    max_age = _token_lifetime_seconds()
    signed_value = encode_gallery_access_session_cookie(session_id)
    response.set_cookie(
        key=_session_cookie_name(),
        value=signed_value,
        max_age=max_age,
        expires=max_age,
        secure=True,
        httponly=True,
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

    try:
        payload = signing.loads(
            raw_value,
            salt=SESSION_COOKIE_SALT,
            max_age=_token_lifetime_seconds(),
        )
    except signing.SignatureExpired as exc:
        raise AuthenticationFailed('Gallery session expired.') from exc
    except signing.BadSignature as exc:
        raise AuthenticationFailed('Invalid gallery session cookie.') from exc

    session_id = payload.get('session_id')
    if not isinstance(session_id, int):
        raise AuthenticationFailed('Invalid gallery session cookie payload.')

    return session_id
