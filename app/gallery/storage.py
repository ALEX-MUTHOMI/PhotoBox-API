"""
gallery/storage.py — Cloudflare R2 Storage Utilities

Centralised boto3 client for the gallery app.
IAM credentials must be scoped to s3:PutObject + s3:GetObject ONLY.
Do NOT use this client for s3:DeleteObject, s3:ListBucket, or policy mutations.
"""
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


def get_r2_client():
    """
    Build a boto3 S3 client targeting the Cloudflare R2 endpoint.
    Each call creates a new client — boto3 clients are NOT thread-safe
    when mutated, but are safe to create per-request or per-task.
    """
    import boto3
    return boto3.client(
        's3',
        endpoint_url=settings.CLOUDFLARE_R2_ENDPOINT,
        aws_access_key_id=settings.CLOUDFLARE_ACCESS_KEY_ID,
        aws_secret_access_key=settings.CLOUDFLARE_SECRET_ACCESS_KEY,
        region_name='auto',  # Cloudflare R2 ignores region but boto3 requires it
    )


def generate_r2_presigned_get_url(r2_object_key: str, expires_in: int = 900) -> str | None:
    """
    Generate a time-limited presigned GET URL for an R2 object.

    SECURITY — URL Exfiltration Defense:
        Hard ceiling of 900 seconds (15 minutes) regardless of caller input.
        This limits the blast radius if a URL is intercepted or leaked.
        The caller CANNOT bypass this by passing a larger `expires_in`.

    Args:
        r2_object_key: The exact R2 object path to sign.
        expires_in:    Requested TTL in seconds. Capped at 900.

    Returns:
        A signed HTTPS URL string, or None if generation fails.
    """
    # HARD CAP — enforced here, not by the caller
    expires_in = min(expires_in, 900)

    bucket = settings.CLOUDFLARE_R2_BUCKET_NAME
    if not bucket or not r2_object_key:
        logger.warning("[R2] generate_presigned_get_url called with empty bucket or key.")
        return None

    try:
        client = get_r2_client()
        url = client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': r2_object_key},
            ExpiresIn=expires_in,
        )
        return url
    except Exception as exc:
        logger.error(f"[R2] Failed to generate presigned GET URL for {r2_object_key}: {exc}")
        return None


def infer_content_type(filename: str) -> str:
    """
    Map a sanitized filename extension to its MIME type for R2 Content-Type header.
    Conservative allowlist — only formats permitted through the Fast Lane.
    """
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return {
        'jpg':  'image/jpeg',
        'jpeg': 'image/jpeg',
        'png':  'image/png',
        'webp': 'image/webp',
    }.get(ext, 'application/octet-stream')
