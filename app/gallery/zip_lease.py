"""Redis leased semaphore for concurrent archive ZIP jobs.

Leases auto-expire on worker crash (TTL + heartbeat). Capacity returns without
manual release. Per-job holder secret prevents cross-tenant DEL races.
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

LEASE_KEY_PREFIX = "photobox:zip:lease:"
SLOT_SET_KEY = "photobox:zip:slots"
GALLERY_ACTIVE_KEY_PREFIX = "photobox:zip:gallery:"

_ACQUIRE_LUA = """
local slot_key = KEYS[1]
local lease_key = KEYS[2]
local gallery_key = KEYS[3]
local capacity = tonumber(ARGV[1])
local lease_ttl = tonumber(ARGV[2])
local gallery_cap = tonumber(ARGV[3])
local holder = ARGV[4]
local job_id = ARGV[5]
local now = tonumber(ARGV[6])

-- Drop expired lease members from the global slot set
local members = redis.call('ZRANGE', slot_key, 0, -1, 'WITHSCORES')
for i = 1, #members, 2 do
  local mid = members[i]
  local score = tonumber(members[i + 1])
  if score < now then
    redis.call('ZREM', slot_key, mid)
  elseif redis.call('EXISTS', 'photobox:zip:lease:' .. mid) == 0 then
    redis.call('ZREM', slot_key, mid)
  end
end

local gallery_active = tonumber(redis.call('GET', gallery_key) or '0')
if gallery_active >= gallery_cap then
  return {0, 'gallery_cap'}
end

local used = redis.call('ZCARD', slot_key)
if used >= capacity then
  return {0, 'global_cap'}
end

redis.call('SET', lease_key, holder, 'EX', lease_ttl)
redis.call('ZADD', slot_key, now + lease_ttl, job_id)
redis.call('INCR', gallery_key)
redis.call('EXPIRE', gallery_key, lease_ttl * 4)
return {1, 'ok'}
"""

_RELEASE_LUA = """
local slot_key = KEYS[1]
local lease_key = KEYS[2]
local gallery_key = KEYS[3]
local holder = ARGV[1]
local job_id = ARGV[2]
local current = redis.call('GET', lease_key)
if current and current == holder then
  redis.call('DEL', lease_key)
  redis.call('ZREM', slot_key, job_id)
  local g = tonumber(redis.call('GET', gallery_key) or '0')
  if g > 0 then
    redis.call('DECR', gallery_key)
  end
  return 1
end
return 0
"""

_HEARTBEAT_LUA = """
local lease_key = KEYS[1]
local slot_key = KEYS[2]
local holder = ARGV[1]
local job_id = ARGV[2]
local lease_ttl = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local current = redis.call('GET', lease_key)
if current and current == holder then
  redis.call('EXPIRE', lease_key, lease_ttl)
  redis.call('ZADD', slot_key, now + lease_ttl, job_id)
  return 1
end
return 0
"""

_client_cache: dict[str, redis.Redis] = {}


def _redis_url() -> str:
    return (
        getattr(settings, "RATE_LIMIT_REDIS_URL", "")
        or os.environ.get("REDIS_URL", "")
        or os.environ.get("CELERY_BROKER_URL", "")
    )


def _client() -> Optional[redis.Redis]:
    url = _redis_url()
    if not url or url.startswith("memory://"):
        return None
    cached = _client_cache.get(url)
    if cached is None:
        cached = redis.Redis.from_url(
            url, socket_timeout=0.5, socket_connect_timeout=0.5
        )
        _client_cache[url] = cached
    return cached


@dataclass(frozen=True)
class ZipLease:
    job_id: str
    gallery_id: str
    holder: str


@dataclass(frozen=True)
class ZipLeaseDecision:
    acquired: bool
    reason: str
    lease: Optional[ZipLease] = None


def acquire_zip_lease(job_id: str, gallery_id: str) -> ZipLeaseDecision:
    """Acquire a leased ZIP slot. Fail-open when Redis is unavailable (dev/test)."""
    capacity = int(getattr(settings, "ARCHIVE_ZIP_GLOBAL_LEASES", 20))
    gallery_cap = int(getattr(settings, "ARCHIVE_ZIP_PER_GALLERY_LEASES", 1))
    lease_ttl = int(getattr(settings, "ARCHIVE_ZIP_LEASE_TTL_SECONDS", 60))
    holder = uuid.uuid4().hex
    client = _client()
    if client is None:
        return ZipLeaseDecision(
            True,
            "redis_unavailable_fail_open",
            ZipLease(job_id=job_id, gallery_id=str(gallery_id), holder=holder),
        )
    try:
        result = client.eval(
            _ACQUIRE_LUA,
            3,
            SLOT_SET_KEY,
            f"{LEASE_KEY_PREFIX}{job_id}",
            f"{GALLERY_ACTIVE_KEY_PREFIX}{gallery_id}",
            capacity,
            lease_ttl,
            gallery_cap,
            holder,
            str(job_id),
            int(time.time()),
        )
        ok = int(result[0]) == 1
        reason = result[1].decode() if isinstance(result[1], bytes) else str(result[1])
        if not ok:
            return ZipLeaseDecision(False, reason)
        return ZipLeaseDecision(
            True,
            reason,
            ZipLease(job_id=str(job_id), gallery_id=str(gallery_id), holder=holder),
        )
    except redis.RedisError as exc:
        logger.warning("[ZIP LEASE] acquire failed open: %s", exc)
        return ZipLeaseDecision(
            True,
            "redis_error_fail_open",
            ZipLease(job_id=str(job_id), gallery_id=str(gallery_id), holder=holder),
        )


def heartbeat_zip_lease(lease: ZipLease) -> bool:
    lease_ttl = int(getattr(settings, "ARCHIVE_ZIP_LEASE_TTL_SECONDS", 60))
    client = _client()
    if client is None:
        return True
    try:
        result = client.eval(
            _HEARTBEAT_LUA,
            2,
            f"{LEASE_KEY_PREFIX}{lease.job_id}",
            SLOT_SET_KEY,
            lease.holder,
            lease.job_id,
            lease_ttl,
            int(time.time()),
        )
        return int(result) == 1
    except redis.RedisError:
        return False


def release_zip_lease(lease: ZipLease) -> None:
    client = _client()
    if client is None:
        return
    try:
        client.eval(
            _RELEASE_LUA,
            3,
            SLOT_SET_KEY,
            f"{LEASE_KEY_PREFIX}{lease.job_id}",
            f"{GALLERY_ACTIVE_KEY_PREFIX}{lease.gallery_id}",
            lease.holder,
            lease.job_id,
        )
    except redis.RedisError as exc:
        logger.warning("[ZIP LEASE] release failed: %s", exc)
