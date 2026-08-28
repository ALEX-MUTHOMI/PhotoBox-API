"""Photographer Fast Lane filename search (Postgres pg_trgm).

Filter-only: never reorder by similarity — keyset cursors stay on
(-uploaded_at, -id).

PostgreSQL path uses ``ILIKE`` (not Django's ``icontains`` / ``UPPER()``)
and the ``%>`` word-similarity operator so ``gin_trgm_ops`` stays usable.
"""
from __future__ import annotations

from django.db import connection
from django.db.models import Q, QuerySet
from django.db.models.expressions import RawSQL
from rest_framework.exceptions import ValidationError

FILENAME_SEARCH_MAX_LEN = 64
# pg_trgm.word_similarity_threshold GUC (%> operator); keep in sync with DB default
# unless operators set a session GUC. Stricter than the old function form at 0.4.
FILENAME_SEARCH_MIN_FUZZY_LEN = 2


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def apply_filename_search(queryset: QuerySet, raw_q: str | None) -> QuerySet:
    """Apply infix + optional trigram word-similarity filter.

    Empty / whitespace ``q`` is a no-op. Overlong ``q`` raises 400.
    LIKE metacharacters in ``q`` are escaped (fail-closed for wildcards).
    """
    if raw_q is None:
        return queryset

    q = raw_q.strip()
    if not q:
        return queryset

    if len(q) > FILENAME_SEARCH_MAX_LEN:
        raise ValidationError({"q": "Search query too long."})

    if connection.vendor != "postgresql":
        return queryset.filter(original_filename__icontains=q)

    table = queryset.model._meta.db_table
    pattern = f"%{_escape_like(q)}%"
    ilike_expr = RawSQL(
        f"({table}.original_filename ILIKE %s ESCAPE '\\')",
        (pattern,),
    )

    if len(q) < FILENAME_SEARCH_MIN_FUZZY_LEN:
        return queryset.annotate(_ilike_hit=ilike_expr).filter(_ilike_hit=True)

    # ILIKE (~~*) and word-similarity (%>) are both GIN/trgm-indexable.
    # Avoid word_similarity() function form — it forces Seq Scan+Filter.
    return (
        queryset.annotate(_ilike_hit=ilike_expr)
        .filter(
            Q(_ilike_hit=True)
            | Q(original_filename__trigram_word_similar=q)
        )
    )
