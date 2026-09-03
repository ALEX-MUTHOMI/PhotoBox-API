"""Offline LSH + Union-Find burst clustering (Phase 4)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable
from uuid import UUID

from gallery.phash import collect_lsh_candidate_pairs, hamming_distance


@dataclass(frozen=True)
class PhashRow:
    photo_id: UUID
    phash: bytes
    uploaded_at: datetime


class UnionFind:
    def __init__(self, ids: Iterable[UUID]):
        self.parent = {i: i for i in ids}
        self.rank = {i: 0 for i in ids}

    def find(self, x: UUID) -> UUID:
        parent = self.parent[x]
        if parent != x:
            self.parent[x] = self.find(parent)
        return self.parent[x]

    def union(self, a: UUID, b: UUID) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            self.parent[ra] = rb
        elif self.rank[ra] > self.rank[rb]:
            self.parent[rb] = ra
        else:
            self.parent[rb] = ra
            self.rank[ra] += 1


def cluster_phash_rows(
    rows: list[PhashRow],
    *,
    hamming_threshold: int = 8,
    time_window_seconds: int = 90,
    bands: int = 8,
    rows_per_band: int = 8,
) -> list[list[UUID]]:
    """Return components with size >= 2."""
    if len(rows) < 2:
        return []

    by_id = {row.photo_id: row for row in rows}
    uf = UnionFind(by_id.keys())
    pairs = collect_lsh_candidate_pairs(
        ((row.photo_id, row.phash) for row in rows),
        bands=bands,
        rows=rows_per_band,
    )
    window = timedelta(seconds=time_window_seconds)

    for a, b in pairs:
        ra, rb = by_id[a], by_id[b]
        if hamming_distance(ra.phash, rb.phash) > hamming_threshold:
            continue
        if abs(ra.uploaded_at - rb.uploaded_at) > window:
            continue
        uf.union(a, b)

    components: dict[UUID, list[UUID]] = {}
    for photo_id in by_id:
        root = uf.find(photo_id)
        components.setdefault(root, []).append(photo_id)

    return [members for members in components.values() if len(members) >= 2]
