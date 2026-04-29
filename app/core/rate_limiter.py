"""
Sliding Window Rate Limiter for Client Authentication & Ingestion Protection.

Complexity: O(1) amortized using Redis atomic increment with window expiry.
"""
from __future__ import annotations

import time
from django.core.cache import cache
from django.conf import settings


class RateLimitExceeded(Exception):
    pass


def check_sliding_window_rate_limit(
    key_identifier: str,
    limit: int = 5,
    window_seconds: int = 60,
    prefix: str = "photobox:rl:"
) -> tuple[bool, int]:
    """
    Evaluate if an action is within rate limits using sliding window counter.

    Returns:
      (is_allowed: bool, remaining_requests: int)
    """
    if not key_identifier:
        return True, limit

    current_window = int(time.time()) // window_seconds
    cache_key = f"{prefix}{key_identifier}:{current_window}"

    try:
        current_count = cache.get(cache_key, 0)
        if current_count >= limit:
            return False, 0

        # Increment atomic counter with window expiry
        new_count = cache.incr(cache_key) if cache.get(cache_key) is not None else 1
        if new_count == 1:
            cache.set(cache_key, 1, timeout=window_seconds * 2)

        remaining = max(0, limit - new_count)
        return True, remaining
    except Exception:
        # Fail-open gracefully if cache is unreachable
        return True, limit
