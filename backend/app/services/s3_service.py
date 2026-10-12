"""Optional AWS S3 integration (boto3).

* Credentials come from the standard AWS chain (profile, env vars, IAM role);
  nothing is hard-coded and nothing is sent to the frontend.
* All reads are list/head/get operations.
* The only write operation is an in-place storage-class change, which is
  disabled unless ``S3_WRITE_ENABLED=true`` *and* the action was approved in an
  optimisation plan. Every attempt is written to the operation log.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.core.config import get_settings
from app.services import extractors
from app.services.scanner import FileInfo

WRITABLE_TARGET_CLASSES = {"STANDARD", "STANDARD_IA", "INTELLIGENT_TIERING", "GLACIER_IR", "GLACIER", "DEEP_ARCHIVE"}
RESTORE_REQUIRED_CLASSES = {"GLACIER", "DEEP_ARCHIVE"}
MAX_COPY_BYTES = 5 * 1024**3  # single-request CopyObject limit


class S3NotConfiguredError(RuntimeError):
    pass


class S3PermissionError(PermissionError):
    pass


def _session():
    import boto3

    settings = get_settings()
    return (
        boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        if settings.aws_profile
        else boto3.Session(region_name=settings.aws_region)
    )


def client():
    return _session().client("s3")


def status() -> dict[str, Any]:
    """Connection status without exposing secrets."""
    settings = get_settings()
    base = {
        "region": settings.aws_region,
        "profile": settings.aws_profile,
        "write_enabled": settings.s3_write_enabled,
        "allowed_buckets": settings.allowed_s3_buckets,
    }
    try:
        session = _session()
        if session.get_credentials() is None:
            return {
                **base,
                "configured": False,
                "connected": False,
                "message": "No AWS credentials found. Configure a profile, environment variables or an IAM role.",
            }
        identity = session.client("sts").get_caller_identity()
        arn = identity.get("Arn", "")
        return {
            **base,
            "configured": True,
            "connected": True,
            "account": identity.get("Account"),
            "identity_arn": arn,
            "message": "Connected.",
        }
    except Exception as exc:  # noqa: BLE001
        return {**base, "configured": True, "connected": False, "message": f"{exc.__class__.__name__}: {exc}"[:300]}


def check_bucket_allowed(bucket: str) -> None:
    allowed = get_settings().allowed_s3_buckets
    if allowed and bucket not in allowed:
        raise S3PermissionError(f"Bucket '{bucket}' is not in ALLOWED_S3_BUCKETS.")


def list_buckets() -> list[dict[str, Any]]:
    allowed = get_settings().allowed_s3_buckets
    response = client().list_buckets()
    buckets = []
    for b in response.get("Buckets", []):
        if allowed and b["Name"] not in allowed:
            continue
        buckets.append({"name": b["Name"], "created_at": b.get("CreationDate").isoformat() if b.get("CreationDate") else None})
    return buckets


def list_objects(bucket: str, prefix: str = "") -> list[FileInfo]:
    check_bucket_allowed(bucket)
    paginator = client().get_paginator("list_objects_v2")
    infos: list[FileInfo] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix or ""):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            infos.append(
                FileInfo(
                    rel_path=key,
                    name=key.rsplit("/", 1)[-1],
                    size=int(obj["Size"]),
                    modified_at=obj.get("LastModified"),
                    storage_class=obj.get("StorageClass", "STANDARD"),
                    etag=obj.get("ETag", "").strip('"'),
                )
            )
    return infos


def download_object(bucket: str, key: str, max_bytes: int) -> bytes:
    check_bucket_allowed(bucket)
    head = client().head_object(Bucket=bucket, Key=key)
    if head.get("StorageClass") in RESTORE_REQUIRED_CLASSES:
        raise S3PermissionError("Object is archived and must be restored before its content can be read.")
    if head["ContentLength"] > max_bytes:
        raise ValueError(f"Object larger than the {max_bytes // (1024 * 1024)} MB download limit.")
    return client().get_object(Bucket=bucket, Key=key)["Body"].read()


def analyze_object(bucket: str, info: FileInfo) -> dict[str, Any]:
    """Metadata analysis; content is downloaded only for small, readable objects."""
    settings = get_settings()
    ext, category, mime, supported = extractors.classify(info.name)
    out: dict[str, Any] = {
        "extension": ext,
        "category": category,
        "mime_type": mime,
        "metadata_json": {"etag": info.etag, "storage_class": info.storage_class},
        "text_content": None,
        "status": "analyzed",
        "error": None,
        "sha256": None,
    }
    limit = settings.s3_max_download_mb * 1024 * 1024
    if info.storage_class in RESTORE_REQUIRED_CLASSES:
        out["metadata_json"]["content"] = "Archived object: metadata only (restore required to read content)."
        return out
    if info.size > limit or not supported:
        out["metadata_json"]["content"] = "Not downloaded (size limit or unsupported type); metadata only."
        if not supported:
            out["status"] = "skipped"
            out["error"] = "Unsupported format: inventoried from metadata only."
        return out
    try:
        data = download_object(bucket, info.rel_path, limit)
        out["sha256"] = hashlib.sha256(data).hexdigest()
        if ext in extractors.TEXT_EXTRACTABLE:
            extraction = extractors.extract_text_from_bytes(ext, data, settings.max_text_chars)
            out["text_content"] = extraction.text
            out["metadata_json"].update(extraction.metadata)
        elif category == "image":
            out["metadata_json"].update(extractors.image_metadata(data).metadata)
        elif category == "archive":
            out["metadata_json"].update(extractors.zip_metadata(data).metadata)
    except Exception as exc:  # noqa: BLE001
        out["status"] = "error"
        out["error"] = f"{exc.__class__.__name__}: {str(exc)[:300]}"
    return out


def validate_transition(bucket: str, key: str, target_class: str) -> dict[str, Any]:
    """Validate a storage-class change against live object state. Raises on violation."""
    if target_class not in WRITABLE_TARGET_CLASSES:
        raise ValueError(f"Unsupported target storage class {target_class}.")
    check_bucket_allowed(bucket)
    head = client().head_object(Bucket=bucket, Key=key)
    current = head.get("StorageClass", "STANDARD")
    if current in RESTORE_REQUIRED_CLASSES:
        raise ValueError(f"Object is in {current}; it must be restored before its class can be changed.")
    if current == target_class:
        raise ValueError(f"Object is already in {target_class}.")
    if head["ContentLength"] > MAX_COPY_BYTES:
        raise ValueError("Objects over 5 GB require multipart copy, which is not supported; use a lifecycle rule.")
    return {"current_class": current, "size": head["ContentLength"], "etag": head.get("ETag")}


def change_storage_class(bucket: str, key: str, target_class: str, dry_run: bool = True) -> dict[str, Any]:
    """Copy an object onto itself with a new storage class (metadata preserved)."""
    validation = validate_transition(bucket, key, target_class)
    if dry_run:
        return {"dry_run": True, "would_change": f"{validation['current_class']} -> {target_class}", **validation}
    if not get_settings().s3_write_enabled:
        raise S3PermissionError("Cloud writes are disabled. Set S3_WRITE_ENABLED=true to allow approved transitions.")
    s3 = client()
    s3.copy_object(
        Bucket=bucket,
        Key=key,
        CopySource={"Bucket": bucket, "Key": key},
        StorageClass=target_class,
        MetadataDirective="COPY",
        TaggingDirective="COPY",
    )
    after = s3.head_object(Bucket=bucket, Key=key).get("StorageClass", "STANDARD")
    if after != target_class:
        raise RuntimeError(f"Verification failed: object reports {after}, expected {target_class}.")
    return {"dry_run": False, "changed": f"{validation['current_class']} -> {target_class}", "verified_class": after}
