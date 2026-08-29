"""
The single quota ledger service.

Workspace.storage_used_bytes is the only usage counter in the system.
"""
from __future__ import annotations

import logging
from typing import Any

from django.db.models import F, Value
from django.db.models.functions import Greatest

logger = logging.getLogger(__name__)


class QuotaError(Exception):
    """Base class for quota ledger failures."""


class QuotaExceededError(QuotaError):
    """The requested reservation would push usage past the workspace limit."""

    def __init__(self, *, requested: int, used: int, limit: int):
        self.requested = requested
        self.used = used
        self.limit = limit
        super().__init__(
            f"Storage quota exceeded: {used} + {requested} > {limit} bytes."
        )


def reserve_workspace_bytes(
    workspace_id: Any,
    byte_count: int,
    *,
    nowait: bool = False,
) -> int:
    """Lock the workspace row, verify headroom, and charge byte_count against it."""
    from core.models import Workspace

    if byte_count < 0:
        raise ValueError(f"byte_count must not be negative, got {byte_count}.")
    if byte_count == 0:
        return Workspace.objects.values_list("storage_used_bytes", flat=True).get(id=workspace_id)

    locked = Workspace.objects.select_for_update(nowait=nowait).get(id=workspace_id)
    projected = locked.storage_used_bytes + byte_count

    if projected > locked.storage_limit_bytes:
        raise QuotaExceededError(
            requested=byte_count,
            used=locked.storage_used_bytes,
            limit=locked.storage_limit_bytes,
        )

    Workspace.objects.filter(id=workspace_id).update(
        storage_used_bytes=F("storage_used_bytes") + byte_count
    )
    return projected


def release_workspace_bytes(workspace_id: Any, byte_count: int) -> None:
    """Refund byte_count to the workspace ledger, floored at zero."""
    from core.models import Workspace

    if byte_count <= 0:
        return

    Workspace.objects.filter(id=workspace_id).update(
        storage_used_bytes=Greatest(Value(0), F("storage_used_bytes") - byte_count)
    )
