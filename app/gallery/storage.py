# gallery/storage.py
"""
Cloudflare R2 Storage Utilities.

Architecture contract:
  - This module owns all boto3 interaction for the gallery app.
  - Callers receive plain Python dicts/strings/booleans, never boto3 objects.
  - Download links are short-lived and fail closed on validation errors.
  - Dangerous operations like delete_objects are centralized for auditability.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Any, Dict, Optional
from urllib.parse import unquote

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

logger = logging.getLogger(__name__)


UPLOAD_URL_TTL_SECONDS: int = 900
DOWNLOAD_URL_TTL_SECONDS: int = 60
_TTL_FLOOR: int = 10

_SAFE_KEY_RE = re.compile(r"^[a-zA-Z0-9\-_./]+$")
_MAX_KEY_LENGTH = 1024

_thread_local = threading.local()


class R2KeyValidationError(ValueError):
    """Raised when an R2 object key fails validation."""


def validate_r2_key(key: str) -> str:
    if not key:
        raise R2KeyValidationError("R2 object key must not be empty.")

    if len(key) > _MAX_KEY_LENGTH:
        raise R2KeyValidationError(
            f"R2 object key exceeds maximum length of {_MAX_KEY_LENGTH}: {len(key)} chars."
        )

    if "\x00" in key:
        raise R2KeyValidationError(f"Null byte in R2 object key: {key!r}")

    decoded = unquote(key)
    if ".." in decoded or ".." in key:
        raise R2KeyValidationError(f"Path traversal sequence in R2 object key: {key!r}")

    if not _SAFE_KEY_RE.match(key):
        raise R2KeyValidationError(f"Unsafe characters in R2 object key: {key!r}")

    return key


def _credential_fingerprint() -> str:
    ep = getattr(settings, "CLOUDFLARE_R2_ENDPOINT", "") or ""
    ak = getattr(settings, "CLOUDFLARE_ACCESS_KEY_ID", "") or ""
    sk = getattr(settings, "CLOUDFLARE_SECRET_ACCESS_KEY", "") or ""
    return hashlib.sha256(f"{ep}:{ak}:{sk}".encode("utf-8")).hexdigest()


def _delete_credential_fingerprint() -> str:
    ep = getattr(settings, "CLOUDFLARE_R2_DELETE_ENDPOINT", "") or ""
    ak = getattr(settings, "CLOUDFLARE_R2_DELETE_ACCESS_KEY_ID", "") or ""
    sk = getattr(settings, "CLOUDFLARE_R2_DELETE_SECRET_ACCESS_KEY", "") or ""
    return hashlib.sha256(f"{ep}:{ak}:{sk}".encode("utf-8")).hexdigest()


def _assert_credentials() -> tuple[str, str, str, str]:
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


def _assert_delete_credentials() -> tuple[str, str, str, str]:
    endpoint = getattr(settings, "CLOUDFLARE_R2_DELETE_ENDPOINT", "") or ""
    access_key = getattr(settings, "CLOUDFLARE_R2_DELETE_ACCESS_KEY_ID", "") or ""
    secret_key = getattr(settings, "CLOUDFLARE_R2_DELETE_SECRET_ACCESS_KEY", "") or ""
    bucket = getattr(settings, "CLOUDFLARE_R2_DELETE_BUCKET_NAME", "") or getattr(
        settings,
        "CLOUDFLARE_R2_BUCKET_NAME",
        "",
    )

    if endpoint and access_key and secret_key and bucket:
        return endpoint, access_key, secret_key, bucket

    return _assert_credentials()


def _build_r2_client(endpoint: str, access_key: str, secret_key: str) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 1, "mode": "standard"},
            max_pool_connections=20,
        ),
    )


def get_r2_client() -> Any:
    endpoint, access_key, secret_key, _ = _assert_credentials()
    fingerprint = _credential_fingerprint()

    if getattr(_thread_local, "client_fingerprint", None) != fingerprint:
        _thread_local.client = _build_r2_client(endpoint, access_key, secret_key)
        _thread_local.client_fingerprint = fingerprint

    return _thread_local.client


def get_r2_delete_client() -> Any:
    endpoint, access_key, secret_key, _ = _assert_delete_credentials()
    fingerprint = _delete_credential_fingerprint()

    if getattr(_thread_local, "delete_client_fingerprint", None) != fingerprint:
        _thread_local.delete_client = _build_r2_client(endpoint, access_key, secret_key)
        _thread_local.delete_client_fingerprint = fingerprint

    return _thread_local.delete_client


def generate_r2_presigned_get_url(
    bucket: str,
    key: Optional[str] = None,
    expires_in: int = DOWNLOAD_URL_TTL_SECONDS,
) -> Optional[str]:
    expires_in = max(_TTL_FLOOR, min(expires_in, DOWNLOAD_URL_TTL_SECONDS))

    try:
        _, _, _, configured_bucket = _assert_credentials()

        if key is None:
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
    except Exception as exc:
        logger.error(
            "[R2] generate_presigned_get_url failed for key=%s error_type=%s",
            r2_object_key,
            type(exc).__name__,
        )
        return None


def upload_local_file_to_r2(
    local_path: str,
    r2_object_key: str,
    content_type: str = "application/octet-stream",
) -> bool:
    try:
        validate_r2_key(r2_object_key)
        _, _, _, bucket = _assert_credentials()
    except (R2KeyValidationError, RuntimeError) as exc:
        logger.error("[R2] upload aborted: %s", exc)
        return False

    config = TransferConfig(
        multipart_threshold=8 * 1024 * 1024,
        multipart_chunksize=8 * 1024 * 1024,
        max_concurrency=2,
        use_threads=False,
    )

    try:
        client = get_r2_client()
        client.upload_file(
            local_path,
            bucket,
            r2_object_key,
            ExtraArgs={"ContentType": content_type},
            Config=config,
        )
        return True
    except (ClientError, BotoCoreError, OSError) as exc:
        logger.error("[R2] upload_file failed for key=%s: %s", r2_object_key, exc)
        return False


def generate_r2_presigned_post(
    r2_object_key: str,
    max_size_bytes: int,
    expires_in: int = 900,
    extra_conditions: list = None,
    extra_fields: dict = None,
) -> Optional[Dict[str, Any]]:
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
        fields = dict(extra_fields or {})
        conditions = [["content-length-range", 1, max_size_bytes]] + (extra_conditions or [])
        for key, value in fields.items():
            conditions.append(["eq", f"${key}", value])
        result = client.generate_presigned_post(
            Bucket=bucket,
            Key=r2_object_key,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=expires_in,
        )
    except (ClientError, BotoCoreError) as exc:
        logger.error(
            "[R2] generate_presigned_post failed for key=%s: %s",
            r2_object_key,
            exc,
        )
        return None

    url = result["url"]
    return {
        "upload_url": url,
        "post_url": url,
        "post_fields": result["fields"],
    }


def r2_object_exists(r2_object_key: str) -> bool:
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
        raise


def r2_object_size(r2_object_key: str) -> int | None:
    """
    Return the authoritative ContentLength from R2, or None if the object does not exist.

    Raises ClientError / BotoCoreError on transport or configuration failures — callers
    should fail closed (503) rather than acknowledging the upload.
    """
    validate_r2_key(r2_object_key)
    _, _, _, bucket = _assert_credentials()

    try:
        client = get_r2_client()
        response = client.head_object(Bucket=bucket, Key=r2_object_key)
        return int(response.get("ContentLength", 0))
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey", "Not Found"):
            return None
        raise


def delete_r2_objects(object_keys: list[str], bucket: Optional[str] = None) -> bool:
    try:
        _, _, _, configured_bucket = _assert_delete_credentials()
        bucket_name = bucket or configured_bucket
        keys = [validate_r2_key(key) for key in object_keys if key]

        if not keys:
            return True

        client = get_r2_delete_client()
        for offset in range(0, len(keys), 1000):
            batch = keys[offset:offset + 1000]
            client.delete_objects(
                Bucket=bucket_name,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )
        return True
    except (R2KeyValidationError, RuntimeError, ClientError, BotoCoreError) as exc:
        logger.error("[R2] delete_objects failed: %s", exc)
        return False


_CONTENT_TYPE_MAP: Dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "tiff": "image/tiff",
    "tif": "image/tiff",
    "mkv": "video/x-matroska",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "avi": "video/x-msvideo",
    "webm": "video/webm",
}


def infer_content_type(filename: str) -> str:
    if not filename:
        return "application/octet-stream"

    if "\x00" in filename:
        logger.warning("[R2] Null byte in filename passed to infer_content_type.")
        return "application/octet-stream"

    if "." not in filename:
        return "application/octet-stream"

    ext = filename.rsplit(".", 1)[-1].lower()
    return _CONTENT_TYPE_MAP.get(ext, "application/octet-stream")
