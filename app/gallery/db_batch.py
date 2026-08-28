"""PgBouncer-safe queryset iteration via pk windows (no named server cursors)."""
from __future__ import annotations

from typing import Iterator, TypeVar

from django.db.models import Model, QuerySet

T = TypeVar("T", bound=Model)

DEFAULT_CHUNK_SIZE = 500


def iter_pk_batches(
    queryset: QuerySet[T],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Iterator[T]:
    """Yield rows in ascending-pk chunks without ``.iterator()`` / named cursors.

    Each chunk is a short-lived ``LIMIT`` query with ``pk__gt`` keyset, so
    transaction-pooling proxies never need a held server-side cursor.
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")

    last_pk = None
    base = queryset.order_by("pk")
    while True:
        page = base
        if last_pk is not None:
            page = page.filter(pk__gt=last_pk)
        batch: list[T] = list(page[:chunk_size])
        if not batch:
            return
        yield from batch
        last_pk = batch[-1].pk


def list_pk_batches(
    queryset: QuerySet[T],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> list[T]:
    """Materialise a queryset via pk batches (same semantics as ``list(qs)``)."""
    return list(iter_pk_batches(queryset, chunk_size=chunk_size))
