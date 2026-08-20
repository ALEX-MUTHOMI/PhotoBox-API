"""
Min-Heap Priority Queue for Multi-Gigabyte ZIP Archive Packaging.

Schedules ZIP archive generation jobs ordered by priority weight, file count,
and creation timestamp, guaranteeing O(log N) push/pop efficiency without
in-memory buffer bloat.
"""
from __future__ import annotations

import heapq
import time
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass(order=True)
class ArchiveJobItem:
    priority: int
    created_timestamp: float
    job_id: str = field(compare=False)
    gallery_id: str = field(compare=False)
    file_count: int = field(compare=False)
    total_bytes: int = field(compare=False)


class ArchiveMinHeap:
    def __init__(self):
        self._queue: List[ArchiveJobItem] = []

    def push_job(self, job_id: str, gallery_id: str, file_count: int, total_bytes: int, is_pro: bool = False) -> None:
        """Push an archive job in O(log N) time."""
        # Pro users get priority score 1, Free users get priority score 10
        priority_weight = 1 if is_pro else 10
        item = ArchiveJobItem(
            priority=priority_weight,
            created_timestamp=time.time(),
            job_id=job_id,
            gallery_id=gallery_id,
            file_count=file_count,
            total_bytes=total_bytes,
        )
        heapq.heappush(self._queue, item)

    def pop_next_job(self) -> Optional[ArchiveJobItem]:
        """Pop the highest priority job in O(log N) time."""
        if not self._queue:
            return None
        return heapq.heappop(self._queue)

    def __len__(self) -> int:
        return len(self._queue)


# Singleton queue instance
_archive_heap = ArchiveMinHeap()


def schedule_archive_packaging(job_id: str, gallery_id: str, file_count: int, total_bytes: int, is_pro: bool = False) -> None:
    _archive_heap.push_job(job_id, gallery_id, file_count, total_bytes, is_pro)


def get_next_archive_job() -> Optional[ArchiveJobItem]:
    return _archive_heap.pop_next_job()
