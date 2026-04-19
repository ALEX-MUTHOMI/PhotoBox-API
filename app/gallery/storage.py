# gallery/storage.py
"""
Cloudflare R2 Storage Utilities.

Architecture contract:
  - This module owns ALL boto3 interaction for the gallery app.
  - Callers receive plain Python dicts/strings/booleans — never boto3 objects.
  - All failures return None or raise domain exceptions, never boto3 exceptions.
  - Credentials are NEVER stored in plaintext in any cache key.

IAM permission scope (enforce in Cloudflare dashboard):
  - s3:PutObject       — Fast Lane upload ticket generation
  - s3:GetObject       — ZIP download presigned GET
  - s3:HeadObject      — Reaper phantom-upload detection
  DENY: s3:DeleteObject, s3:ListBucket, s3:PutBucketPolicy, s3:*Acl*
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Any, Dict, Optional
from urllib.parse import unquote

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TTL Policy
# Two separate ceilings because upload and download have different risk profiles:
#   Upload POST: 15 min — client may be on a slow connection
#   Download GET: 60 sec — link must expire before it can be forwarded/cached
# ---------------------------------------------------------------------------
UPLOAD_URL_TTL_SECONDS: int = 900    # presigned POST — client upload window
DOWNLOAD_URL_TTL_SECONDS: int = 60   # presigned GET  — ZIP download, short-lived

_TTL_FLOOR: int = 10  # absolute minimum — prevents zero/negative TTL foot-guns


# ---------------------------------------------------------------------------
# Key validation
# ---------------------------------------------------------------------------

# Allowlist for R2 object key characters.
# R2/S3 supports any UTF-8 key, but we restrict to a safe subset to prevent:
#   - Path traversal via URL-encoded sequences (%2e%2e)
#   - Shell metacharacter injection if keys appear in logs or scripts
#   - Null byte injection
_SAFE_KEY_RE = re.compile(
    r"^[a-zA-Z0-9\-_./]+$"
    # Allowed: alphanumeric, hyphen, underscore, dot, forward slash
    # Rejected: spaces, %, +, \, null bytes, all special chars
)

# Maximum key length enforced by S3/R2 protocol
_MAX_KEY_LENGTH = 1024


class R2KeyValidationError(ValueError):
    """
    Raised when an R2 object key fails validation.

    Keeping this as a distinct exception type (not generic ValueError) lets
    callers distinguish between "bad input from user" vs "boto3 failure" and
    return the right HTTP status code (400 vs 502).
    """


def validate_r2_key(key: str) -> str:
    """
    Validate and return an R2 object key.

    Security checks performed (in order):
      1. Non-empty
      2. Length within S3 protocol limit
      3. Null byte injection
      4. URL-encoded traversal sequences (%2e%2e, %2F etc.)
      5. Raw path traversal (..)
      6. Allowlist: only safe character set

    Returns the validated key unchanged.
    Raises R2KeyValidationError for any violation.

    Why return unchanged rather than normalise?
    Normalisation changes the key and would silently break lookups
    for keys already stored in the database. Reject-on-invalid is safer.
    """
    if not key:
        raise R2KeyValidationError("R2 object key must not be empty.")

    if len(key) > _MAX_KEY_LENGTH:
        raise R2KeyValidationError(
            f"R2 object key exceeds maximum length of {_MAX_KEY_LENGTH}: {len(key)} chars."
        )

    # Null byte — terminates strings in C-based systems, breaks logging
    if "\x00" in key:
        raise R2KeyValidationError(f"Null byte in R2 object key: {key!r}")

    # URL-encoded traversal: decode first, then check
    # R2/S3 decodes %2e as "." and %2f as "/" during request processing
    decoded = unquote(key)
    if ".." in decoded:
        raise R2KeyValidationError(
            f"Path traversal sequence in R2 object key (decoded): {decoded!r}"
        )

    # Raw traversal (belt-and-suspenders after decode check)
    if ".." in key:
        raise R2KeyValidationError(
            f"Path traversal sequence in R2 object key: {key!r}"
        )

    # Allowlist enforcement
    if not _SAFE_KEY_RE.match(key):
        raise R2KeyValidationError(
            f"Unsafe characters in R2 object key: {key!r}"
        )

    return key


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

# Thread-local client cache.
# We cache per-thread because boto3 clients are NOT thread-safe when shared
# across threads, but ARE safe to reuse within a single thread.
_thread_local = threading.local()


def _credential_fingerprint() -> str:
    """
    Produce a short, non-reversible fingerprint of the current R2 credentials.

    Used as the thread-local cache key so that:
      1. @override_settings in tests gets a fresh client instantly
      2. Credentials rotation (endpoint/key change) invalidates the cache
      3. The actual secret key is NEVER stored in plaintext anywhere

    SHA-256 is used (not MD5) to avoid length-extension attacks, though
    the threat here is low — this is purely a cache invalidation key.
    """
    ep = getattr(settings, "CLOUDFLARE_R2_ENDPOINT", "") or ""
    ak = getattr(settings, "CLOUDFLARE_ACCESS_KEY_ID", "") or ""
    sk = getattr(settings, "CLOUDFLARE_SECRET_ACCESS_KEY", "") or ""

    raw = f"{ep}:{ak}:{sk}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _assert_credentials() -> tuple[str, str, str, str]:
    """
    Validate that all required R2 settings are present before constructing
    a boto3 client. Fail loudly with an actionable error message.

    Returns (endpoint, access_key, secret_key, bucket) if all present.
    Raises RuntimeError with the names of all missing settings if any absent.
    """
    required = {
        "CLOUDFLARE_R2_ENDPOINT": getattr(settings, "CLOUDFLARE_R2_ENDPOINT", None),
        "CLOUDFLARE_ACCESS_KEY_ID": getattr(settings, "CLOUDFLARE_ACCESS_KEY_ID", None),
        "CLOUDFLARE_SECRET_ACCESS_KEY": getattr(settings, "CLOUDFLARE_SECRET_ACCESS_KEY", None),
        "CLOUDFLARE_R2_BUCKET_NAME": getattr(settings, "CLOUDFLARE_R2_BUCKET_NAME", None),
    }

    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            f"R2 storage is misconfigured. Missing settings: {missing}. "
            "Set them as environment variables before starting the server."
        )

    return (
        required["CLOUDFLARE_R2_ENDPOINT"],
        required["CLOUDFLARE_ACCESS_KEY_ID"],
        required["CLOUDFLARE_SECRET_ACCESS_KEY"],
        required["CLOUDFLARE_R2_BUCKET_NAME"],
    )


def get_r2_client() -> Any:
    """
    Return a boto3 S3 client for Cloudflare R2, cached per thread.

    Cache invalidation:
      - Changes to any R2 credential/endpoint setting produce a new client.
      - The cache key is a SHA-256 fingerprint of the credentials —
        the secret key is NEVER stored in plaintext in thread-local memory.

    Timeouts:
      - connect_timeout=5s  — fail fast if R2 is unreachable
      - read_timeout=30s    — allow time for large presigned URL responses
      - boto3 internal retries are DISABLED (max_attempts=1) because
        retry logic lives in Celery tasks, not here. Double-retry produces
        invisible multiplied latency and wasted retry budget.
    """
    endpoint, access_key, secret_key, _ = _assert_credentials()

    fingerprint = _credential_fingerprint()

    if getattr(_thread_local, "client_fingerprint", None) != fingerprint:
        _thread_local.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",  # R2 ignores region but boto3 requires a value
            config=Config(
                signature_version="s3v4",
                # Timeouts prevent worker thread starvation when R2 is slow
                connect_timeout=5,
                read_timeout=30,
                # Retries disabled — Celery owns retry logic, not boto3
                # Allowing both to retry produces up to N² attempts with
                # compounded timeouts and no visibility into which layer fired
                retries={"max_attempts": 1, "mode": "standard"},
                # Socket pool sized to gunicorn worker thread count (default: 10)
                # Oversizing wastes file descriptors; undersizing causes blocking
                max_pool_connections=20,
            ),
        )
        _thread_local.client_fingerprint = fingerprint

    return _thread_local.client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_r2_presigned_get_url(
    bucket: str,
    key: Optional[str] = None,
    expires_in: int = DOWNLOAD_URL_TTL_SECONDS,
) -> Optional[str]:
    """
    Generate a short-lived presigned GET URL for direct R2 download.

    Use case: ZIP download flow — bypasses Cloudinary CDN for zero-egress cost.

    Backward compatibility:
      - Old callers passed only the object key positionally.
      - New callers may pass explicit bucket + key for clearer call auditing.
      - Any explicit bucket must match the configured bucket; signing arbitrary
        buckets is rejected fail-closed.

    Security:
      - TTL clamped to [_TTL_FLOOR, DOWNLOAD_URL_TTL_SECONDS]
      - Short ceiling (60s) prevents URL forwarding/caching attacks
      - Key validated against allowlist before reaching boto3

    Returns None on any failure — never raises to the HTTP layer.
    """
    # Clamp TTL — caller cannot override the security ceiling
    expires_in = max(_TTL_FLOOR, min(expires_in, DOWNLOAD_URL_TTL_SECONDS))

    try:
        _, _, _, configured_bucket = _assert_credentials()

        if key is None:
            # Legacy signature: generate_r2_presigned_get_url("object/key.jpg")
            r2_object_key = bucket
            bucket_name = configured_bucket
        else:
            r2_object_key = key
            bucket_name = bucket or configured_bucket
            if bucket_name != configured_bucket:
                logger.error(
                    "[R2] presigned GET aborted: bucket mismatch requested=%s configured=%s",
                    bucket_name,
                    configured_bucket,
                )
                return None

        validate_r2_key(r2_object_key)
    except (R2KeyValidationError, RuntimeError) as exc:
        logger.error("[R2] presigned GET aborted: %s", exc)
        return None

    try:
        client = get_r2_client()
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket_name, "Key": r2_object_key},
            ExpiresIn=expires_in,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.error(
            "[R2] generate_presigned_get_url failed for key=%s: %s",
            r2_object_key,
            exc,
        )
        return None


def generate_r2_presigned_post(
    r2_object_key: str,
    max_size_bytes: int,
    expires_in: int = 900,
    extra_conditions: list = None,
    extra_fields: dict = None,
) -> Optional[Dict[str, Any]]:
    """
    Generate a presigned POST policy for direct client-to-R2 uploads.

    Security:
      - content-length-range enforced at R2 policy level (not just application)
      - A client cannot swap in a larger file — R2 rejects oversized uploads
      - TTL clamped to [_TTL_FLOOR, UPLOAD_URL_TTL_SECONDS]
      - Key validated against allowlist before reaching boto3

    Response contract (stable — tests assert these keys):
      {
        "upload_url":  str,   # canonical — all clients must use this
        "post_url":    str,   # deprecated alias — remove in API v2
        "post_fields": dict,  # presigned POST policy fields
      }

    Returns None on any failure.
    """
    expires_in = max(_TTL_FLOOR, min(expires_in, UPLOAD_URL_TTL_SECONDS))

    if max_size_bytes <= 0:
        logger.error(
            "[R2] generate_presigned_post called with invalid max_size_bytes=%d",
            max_size_bytes,
        )
        return None

    try:
        validate_r2_key(r2_object_key)
        _, _, _, bucket = _assert_credentials()
    except (R2KeyValidationError, RuntimeError) as exc:
        logger.error("[R2] presigned POST aborted: %s", exc)
        return None

    try:
        client = get_r2_client()
        result = client.generate_presigned_post(
            Bucket=bucket,
            Key=r2_object_key,
            Conditions=[['content-length-range', 1, max_size_bytes]] + (extra_conditions or []),
            ExpiresIn=expires_in,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.error(
            "[R2] generate_presigned_post failed for key=%s: %s",
            r2_object_key,
            exc,
        )
        return None

    # Stable contract — both keys present to support migration
    url = result["url"]
    return {
        "upload_url": url,            # canonical field — tests assert this
        "post_url": url,              # deprecated alias — remove in API v2
        "post_fields": result["fields"],
    }


def r2_object_exists(r2_object_key: str) -> bool:
    """
    Probe R2 for object existence using HeadObject.

    Used exclusively by the Reaper task to detect phantom uploads.

    Returns:
      True  — object confirmed present in R2
      False — object confirmed absent (404 / NoSuchKey)

    Raises:
      R2KeyValidationError — key failed validation (programming error)
      RuntimeError         — credentials not configured
      ClientError          — R2 returned a non-404 error (caller must handle)
      BotoCoreError        — network-level failure (caller must handle)

    Why does this raise instead of returning False on non-404 errors?
    The Reaper must abort the entire reap cycle when R2 is unreachable.
    Returning False would trigger false-positive quota refunds for files
    that actually exist but are temporarily unreachable.
    """
    validate_r2_key(r2_object_key)
    _, _, _, bucket = _assert_credentials()

    try:
        client = get_r2_client()
        client.head_object(Bucket=bucket, Key=r2_object_key)
        return True
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey", "Not Found"):
            return False
        # 403, 503, throttle, etc. — caller (Reaper) must abort the cycle
        raise


# ---------------------------------------------------------------------------
# MIME type resolution
# ---------------------------------------------------------------------------

# Explicit type annotation compatible with Python 3.8+
_CONTENT_TYPE_MAP: Dict[str, str] = {
    # Images — Fast Lane (5MB limit)
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "png":  "image/png",
    "webp": "image/webp",
    # Video — Heavy Lane (no size limit at this layer)
    "mp4":  "video/mp4",
    "mov":  "video/quicktime",
    "avi":  "video/x-msvideo",
    "webm": "video/webm",
}


def infer_content_type(filename: str) -> str:
    """
    Map a sanitised filename to a MIME type for the R2 Content-Type header.

    Security:
      - Null byte guard (some OS truncate at null byte — "shell.php\x00.jpg")
      - Returns 'application/octet-stream' for unknown extensions
      - This is deliberately conservative: unknown types are NOT served with a
        permissive MIME type that could enable MIME-sniffing attacks in browsers

    This function must only be called with filenames that have already passed
    the Fast Lane upload validator. It is NOT a standalone security control.
    """
    if not filename:
        return "application/octet-stream"

    # Null byte guard — some OS truncate filenames at null byte
    if "\x00" in filename:
        logger.warning("[R2] Null byte in filename passed to infer_content_type.")
        return "application/octet-stream"

    if "." not in filename:
        return "application/octet-stream"

    ext = filename.rsplit(".", 1)[-1].lower()
    return _CONTENT_TYPE_MAP.get(ext, "application/octet-stream")
