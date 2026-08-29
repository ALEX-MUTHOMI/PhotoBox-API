"""
Atomic sliding-window rate limiting and failure counting.

Backed by a Redis sorted set of hit timestamps, trimmed and evaluated
inside a single EVAL so that the trim, the count and the admission are
one indivisible operation.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import redis
from django.conf import settings

logger = logging.getLogger(__name__)

RATE_LIMIT_KEY_PREFIX = "photobox:rl:"

_SLIDING_WINDOW_LUA = """
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now_ms - window_ms)
local used = redis.call('ZCARD', KEYS[1])

if used >= limit then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  local retry_ms = window_ms - (now_ms - tonumber(oldest[2]))
  if retry_ms < 0 then retry_ms = 0 end
  return {0, 0, math.ceil(retry_ms)}
end

redis.call('ZADD', KEYS[1], now_ms, ARGV[4])
redis.call('PEXPIRE', KEYS[1], window_ms)
return {1, limit - used - 1, 0}
"""

_client_cache: dict[str, redis.Redis] = {}


def _resolve_redis_url(override: Optional[str]) -> str:
    if override:
        return override
    return (
        getattr(settings, "RATE_LIMIT_REDIS_URL", "")
        or os.environ.get("REDIS_URL", "")
        or os.environ.get("CELERY_BROKER_URL", "")
    )


def _get_client(url: str) -> redis.Redis:
    client = _client_cache.get(url)
    if client is None:
        client = redis.Redis.from_url(
            url,
            socket_timeout=0.25,
            socket_connect_timeout=0.25,
        )
        _client_cache[url] = client
    return client


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: float


def consume_sliding_window(
    key_identifier: str,
    limit: int = 5,
    window_seconds: int = 60,
    prefix: str = RATE_LIMIT_KEY_PREFIX,
    fail_open: bool = False,
    redis_url: Optional[str] = None,
) -> RateLimitDecision:
    """Record one hit against a sliding window and decide whether to admit it."""
    if not key_identifier:
        return RateLimitDecision(True, limit, 0.0)

    url = _resolve_redis_url(redis_url)
    if not url:
        logger.error("Rate limiter has no Redis URL configured.")
        return RateLimitDecision(fail_open, limit if fail_open else 0, 0.0)

    window_key = f"{prefix}{key_identifier}"
    now_ms = int(time.time() * 1000)

    try:
        allowed, remaining, retry_ms = _get_client(url).eval(
            _SLIDING_WINDOW_LUA,
            1,
            window_key,
            now_ms,
            window_seconds * 1000,
            limit,
            f"{now_ms}-{uuid.uuid4().hex}",
        )
    except redis.RedisError as exc:
        logger.error(
            "Rate limiter Redis failure; fail_open=%s",
            fail_open,
            extra={"key": window_key, "error": str(exc)},
        )
        return RateLimitDecision(
            fail_open,
            limit if fail_open else 0,
            0.0 if fail_open else float(window_seconds),
        )

    return RateLimitDecision(
        allowed=bool(allowed),
        remaining=int(remaining),
        retry_after_seconds=int(retry_ms) / 1000.0,
    )


def peek_sliding_window(
    key_identifier: str,
    window_seconds: int = 60,
    prefix: str = RATE_LIMIT_KEY_PREFIX,
    redis_url: Optional[str] = None,
) -> int:
    """Count live hits in the window without recording one."""
    url = _resolve_redis_url(redis_url)
    if not url or not key_identifier:
        return 0
    window_key = f"{prefix}{key_identifier}"
    cutoff_ms = int(time.time() * 1000) - window_seconds * 1000
    try:
        return int(_get_client(url).zcount(window_key, cutoff_ms, "+inf"))
    except redis.RedisError:
        logger.error("Rate limiter peek failed.", extra={"key": window_key})
        return 0
