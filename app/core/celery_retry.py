"""Celery retry helpers that stay safe under eager / direct execution.

Workers must still raise ``task.retry()``. Tests and any process with
``CELERY_TASK_ALWAYS_EAGER`` must never let Celery's ``Retry`` exception
bubble into HTTP handlers (DRF turns it into 500).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional, TypeVar

from celery.exceptions import MaxRetriesExceededError

logger = logging.getLogger(__name__)

T = TypeVar("T")


def is_eager_execution(task: Any) -> bool:
    request = getattr(task, "request", None)
    if request is None:
        return False
    return bool(
        getattr(request, "is_eager", False) or getattr(request, "called_directly", False)
    )


def retry_or_return(
    task: Any,
    exc: Exception,
    *,
    fallback: T,
    countdown: Optional[int] = None,
) -> T:
    """Retry in workers; return ``fallback`` under eager or when retries are exhausted."""
    if is_eager_execution(task):
        logger.warning(
            "[%s] Eager execution: not retrying after %s",
            getattr(task, "name", task.__class__.__name__),
            exc,
        )
        return fallback
    try:
        kwargs: dict[str, Any] = {"exc": exc}
        if countdown is not None:
            kwargs["countdown"] = countdown
        raise task.retry(**kwargs)
    except MaxRetriesExceededError:
        return fallback


def retry_or_call(
    task: Any,
    exc: Exception,
    *,
    on_exhausted: Callable[[], T],
    countdown: Optional[int] = None,
) -> T:
    """Like ``retry_or_return``, but runs ``on_exhausted`` when retries are spent or eager."""
    if is_eager_execution(task):
        logger.warning(
            "[%s] Eager execution: not retrying after %s",
            getattr(task, "name", task.__class__.__name__),
            exc,
        )
        return on_exhausted()
    try:
        kwargs: dict[str, Any] = {"exc": exc}
        if countdown is not None:
            kwargs["countdown"] = countdown
        raise task.retry(**kwargs)
    except MaxRetriesExceededError:
        return on_exhausted()
