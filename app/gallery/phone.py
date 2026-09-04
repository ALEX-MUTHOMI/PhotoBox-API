"""Phone helpers: E.164 validation for Kenya CRM fields."""
from __future__ import annotations

import re

from rest_framework import serializers

# E.164: + then country code + subscriber number (8–15 digits total after +).
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_e164_phone(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    raw = str(value).strip().replace(" ", "").replace("-", "")
    if raw.startswith("0") and len(raw) >= 9:
        # Common KE local form 07… → +2547…
        raw = "+254" + raw.lstrip("0")
    if not E164_RE.match(raw):
        raise serializers.ValidationError(
            "Enter a valid E.164 phone number (e.g. +254712345678)."
        )
    return raw
