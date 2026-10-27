"""
Cloudflare R2 storage service for Tally document originals.
R2 is S3-compatible — uses boto3 with a custom endpoint URL.
"""

from __future__ import annotations

import logging
import mimetypes
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from config.settings import settings

logger = logging.getLogger(__name__)


def _bucket() -> str:
    return settings.R2_BUCKET_NAME or "eazybooks"


def _client():
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def build_r2_key(user_id: str, flow: str, draft_id: str, filename: str) -> str:
    """Deterministic key: {user_id}/{flow}/{draft_id}/{filename}"""
    safe_name = filename.replace(" ", "_")
    return f"{user_id}/{flow}/{draft_id}/{safe_name}"


def _public_url(key: str) -> str:
    """
    Return the download URL for a stored object.
    If CLOUDFLARE_R2_PUBLIC_URL is set (custom domain / public bucket),
    use it; otherwise fall back to the S3-compatible API endpoint.
    """
    base = settings.R2_PUBLIC_URL or settings.R2_ENDPOINT_URL.rstrip("/")
    if base:
        return f"{base}/{_bucket()}/{key}"
    return ""


def upload_to_r2(file_content: bytes, key: str, content_type: Optional[str] = None) -> str:
    """Upload bytes to R2 and return the public/download URL."""
    bucket = _bucket()
    if not content_type:
        content_type, _ = mimetypes.guess_type(key)
        content_type = content_type or "application/octet-stream"
    try:
        _client().put_object(
            Bucket=bucket,
            Key=key,
            Body=file_content,
            ContentType=content_type,
            ContentDisposition="inline",
        )
        return _public_url(key)
    except (BotoCoreError, ClientError) as exc:
        logger.error("R2 upload failed for key=%s: %s", key, exc)
        raise


def download_from_r2(key: str) -> bytes:
    """Download and return the raw bytes for a key."""
    try:
        resp = _client().get_object(Bucket=_bucket(), Key=key)
        return resp["Body"].read()
    except (BotoCoreError, ClientError) as exc:
        logger.error("R2 download failed for key=%s: %s", key, exc)
        raise


def delete_from_r2(key: str) -> None:
    """Delete a file from R2. Silently ignores missing/empty keys."""
    if not key:
        return
    try:
        _client().delete_object(Bucket=_bucket(), Key=key)
    except (BotoCoreError, ClientError) as exc:
        logger.warning("R2 delete failed for key=%s: %s", key, exc)


def generate_presigned_download_url(
    key: str,
    expires_in: int = 86400,
    content_type: Optional[str] = None,
) -> str:
    """Generate an S3-style presigned GET URL for a stored object.

    Preferred over the public URL for browser previews — the signature is
    embedded in the query string, so the bucket does not need public access
    and the URL works regardless of how R2_PUBLIC_URL is configured.
    Caller is responsible for handling expiry on the frontend (refetch on
    reopen).

    `ResponseContentDisposition=inline` and an explicit `ResponseContentType`
    override the object's stored headers so browsers render PDFs/images
    in-place rather than triggering a download.
    """
    filename = key.rsplit("/", 1)[-1] or "file"
    if not content_type:
        guessed, _ = mimetypes.guess_type(key)
        content_type = guessed or "application/octet-stream"
    params = {
        "Bucket": _bucket(),
        "Key": key,
        "ResponseContentDisposition": f'inline; filename="{filename}"',
        "ResponseContentType": content_type,
    }
    try:
        return _client().generate_presigned_url(
            ClientMethod="get_object",
            Params=params,
            ExpiresIn=expires_in,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.error("R2 presigned URL generation failed for key=%s: %s", key, exc)
        raise
