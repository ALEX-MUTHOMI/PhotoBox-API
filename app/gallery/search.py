"""Photographer Fast Lane filename search (Postgres pg_trgm).

Filter-only: never reorder by similarity — keyset cursors stay on
(-uploaded_at, -id).
"""
from __future__ import annotations

from django.contrib.postgres.search import TrigramWordSimilarity
from django.db import connection
from django.db.models import Q, QuerySet, Value
from rest_framework.exceptions import ValidationError

FILENAME_SEARCH_MAX_LEN = 64
FILENAME_SEARCH_WORD_SIM_THRESHOLD = 0.4
FILENAME_SEARCH_MIN_FUZZY_LEN = 2


def apply_filename_search(queryset: QuerySet, raw_q: str | None) -> QuerySet:
    """Apply infix + optional trigram word-similarity filter.

    Empty / whitespace ``q`` is a no-op. Overlong ``q`` raises 400.
    Django's ``icontains`` already escapes LIKE metacharacters.
    """
    if raw_q is None:
        return queryset

    q = raw_q.strip()
    if not q:
        return queryset

    if len(q) > FILENAME_SEARCH_MAX_LEN:
        raise ValidationError({"q": "Search query too long."})

    infix = Q(original_filename__icontains=q)

    if connection.vendor != "postgresql" or len(q) < FILENAME_SEARCH_MIN_FUZZY_LEN:
        return queryset.filter(infix)

    return (
        queryset.annotate(
            _fname_rank=TrigramWordSimilarity(Value(q), "original_filename"),
        ).filter(
            infix | Q(_fname_rank__gte=FILENAME_SEARCH_WORD_SIM_THRESHOLD)
        )
    )
