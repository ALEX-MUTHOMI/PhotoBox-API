"""Keyset (cursor) pagination for photographer Fast Lane photo lists.

B+ tree descent on (scene, uploaded_at, id) — not OFFSET.

Filename search (?q=) is a filter-only pg_trgm path; order stays
(-uploaded_at, -id) so cursors remain valid.

Deferred (not implemented here):
- summed-area-table watermark corner selection
- LSH + Union-Find near-duplicate burst clustering
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from django.core import signing
from django.db.models import Q
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import BasePagination
from rest_framework.response import Response


CURSOR_SALT = "gallery.photo.keyset"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


def encode_photo_keyset_cursor(uploaded_at: datetime, photo_id) -> str:
    payload = {
        "uploaded_at": uploaded_at.isoformat(),
        "id": str(photo_id),
    }
    return signing.dumps(payload, salt=CURSOR_SALT, compress=True)


def decode_photo_keyset_cursor(raw: str) -> tuple[datetime, UUID]:
    if not raw:
        raise ValidationError({"cursor": "Invalid cursor."})
    try:
        payload = signing.loads(raw, salt=CURSOR_SALT, max_age=None)
    except signing.BadSignature as exc:
        raise ValidationError({"cursor": "Invalid cursor."}) from exc

    raw_ts = payload.get("uploaded_at")
    raw_id = payload.get("id")
    uploaded_at = parse_datetime(raw_ts) if isinstance(raw_ts, str) else None
    try:
        photo_id = UUID(str(raw_id))
    except (TypeError, ValueError) as exc:
        raise ValidationError({"cursor": "Invalid cursor."}) from exc
    if uploaded_at is None:
        raise ValidationError({"cursor": "Invalid cursor."})
    return uploaded_at, photo_id


class FastLaneKeysetPagination(BasePagination):
    """Newest-first keyset pagination: ORDER BY uploaded_at DESC, id DESC."""

    page_size = DEFAULT_PAGE_SIZE
    page_size_query_param = "page_size"
    max_page_size = MAX_PAGE_SIZE
    cursor_query_param = "cursor"

    def paginate_queryset(self, queryset, request, view=None):
        self.request = request
        page_size = self.get_page_size(request)
        cursor_raw = request.query_params.get(self.cursor_query_param)
        if cursor_raw:
            uploaded_at, photo_id = decode_photo_keyset_cursor(cursor_raw)
            queryset = queryset.filter(
                Q(uploaded_at__lt=uploaded_at)
                | Q(uploaded_at=uploaded_at, id__lt=photo_id)
            )

        rows = list(queryset.order_by("-uploaded_at", "-id")[: page_size + 1])
        self.has_more = len(rows) > page_size
        page = rows[:page_size]
        self.next_cursor = None
        if self.has_more and page:
            last = page[-1]
            self.next_cursor = encode_photo_keyset_cursor(last.uploaded_at, last.id)
        return page

    def get_page_size(self, request) -> int:
        raw = request.query_params.get(self.page_size_query_param)
        if raw is None:
            return self.page_size
        try:
            size = int(raw)
        except (TypeError, ValueError):
            return self.page_size
        if size < 1:
            return self.page_size
        return min(size, self.max_page_size)

    def get_paginated_response(self, data):
        return Response(
            {
                "results": data,
                "next_cursor": self.next_cursor,
                "has_more": self.has_more,
            }
        )
