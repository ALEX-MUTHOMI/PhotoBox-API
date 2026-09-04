"""Unguessable Base62 share codes for public gallery URLs."""
from __future__ import annotations

import secrets
import string

from django.db import IntegrityError, transaction

SHARE_CODE_ALPHABET = string.ascii_letters + string.digits  # Base62
SHARE_CODE_LENGTH = 10
SHARE_CODE_MIN_LENGTH = 8
SHARE_CODE_MAX_LENGTH = 10
SHARE_CODE_MINT_MAX_ATTEMPTS = 3


def generate_share_code(length: int = SHARE_CODE_LENGTH) -> str:
    if length < SHARE_CODE_MIN_LENGTH or length > SHARE_CODE_MAX_LENGTH:
        raise ValueError(
            f"share_code length must be {SHARE_CODE_MIN_LENGTH}-{SHARE_CODE_MAX_LENGTH}"
        )
    return "".join(secrets.choice(SHARE_CODE_ALPHABET) for _ in range(length))


def is_valid_share_code_format(value: str) -> bool:
    if not isinstance(value, str):
        return False
    if not (SHARE_CODE_MIN_LENGTH <= len(value) <= SHARE_CODE_MAX_LENGTH):
        return False
    return all(ch in SHARE_CODE_ALPHABET for ch in value)


def mint_unique_share_code(*, model_cls, field: str = "share_code") -> str:
    """Allocate a unique share_code with a hard retry cap (plan R2.7)."""
    last_error: Exception | None = None
    for _ in range(SHARE_CODE_MINT_MAX_ATTEMPTS):
        code = generate_share_code()
        if model_cls.objects.filter(**{field: code}).exists():
            continue
        return code
    raise RuntimeError(
        "Failed to mint a unique gallery share_code after "
        f"{SHARE_CODE_MINT_MAX_ATTEMPTS} attempts."
    )


def assign_share_code(event) -> str:
    """Persist a share_code on an Event with collision retries."""
    from gallery.models import Event

    last_error: Exception | None = None
    for _ in range(SHARE_CODE_MINT_MAX_ATTEMPTS):
        code = generate_share_code()
        try:
            with transaction.atomic():
                if Event.objects.filter(share_code=code).exists():
                    continue
                Event.objects.filter(pk=event.pk).update(share_code=code)
                event.share_code = code
                return code
        except IntegrityError as exc:
            last_error = exc
            continue
    raise RuntimeError(
        "Failed to mint a unique gallery share_code after "
        f"{SHARE_CODE_MINT_MAX_ATTEMPTS} attempts."
    ) from last_error
