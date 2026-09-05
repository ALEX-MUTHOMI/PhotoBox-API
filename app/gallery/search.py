"""Photographer Fast Lane filename search (Postgres pg_trgm).

Filter-only: never reorder by similarity — keyset cursors stay on
(-uploaded_at, -id).

PostgreSQL path uses ``ILIKE … ESCAPE`` (not Django's ``icontains`` /
``UPPER()``) and the ``%>`` word-similarity operator so ``gin_trgm_ops``
stays usable. Identifiers come from model meta / quoted ops only; the
user pattern is a bound parameter.
"""
from __future__ import annotations

import re

from django.db import connection
from django.db.models import BooleanField, Func, Q, QuerySet, Value
from rest_framework.exceptions import ValidationError

FILENAME_SEARCH_MAX_LEN = 64
# pg_trgm.word_similarity_threshold GUC (%> operator); keep in sync with DB default
# unless operators set a session GUC. Stricter than the old function form at 0.4.
FILENAME_SEARCH_MIN_FUZZY_LEN = 2

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _require_sql_ident(name: str) -> str:
    if not _SAFE_IDENT.match(name):
        raise ValueError(f"Refusing unsafe SQL identifier: {name!r}")
    return name


class _FilenameILikeEscape(Func):
    """Boolean ILIKE with ESCAPE '\\' — pattern is parameterized."""

    output_field = BooleanField()
    arity = 1

    def __init__(self, table: str, column: str, pattern: str):
        self._table = _require_sql_ident(table)
        self._column = _require_sql_ident(column)
        super().__init__(Value(pattern), output_field=BooleanField())

    def as_postgresql(self, compiler, connection, **extra_context):
        pattern_sql, pattern_params = compiler.compile(self.source_expressions[0])
        table = connection.ops.quote_name(self._table)
        column = connection.ops.quote_name(self._column)
        return (
            f"({table}.{column} ILIKE {pattern_sql} ESCAPE '\\')",
            pattern_params,
        )

    def as_sql(self, compiler, connection, **extra_context):
        # Non-Postgres vendors should not reach here; fail closed if they do.
        if connection.vendor == "postgresql":
            return self.as_postgresql(compiler, connection, **extra_context)
        raise NotImplementedError("_FilenameILikeEscape is PostgreSQL-only")


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
    ilike_expr = _FilenameILikeEscape(table, "original_filename", pattern)

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
