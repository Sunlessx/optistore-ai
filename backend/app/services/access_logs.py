"""Import observed access history from Amazon S3 server access logs.

Log format reference: https://docs.aws.amazon.com/AmazonS3/latest/userguide/LogFormat.html

Each line looks like::

    79a59df9... my-bucket [06/Feb/2019:00:00:38 +0000] 192.0.2.3 arn:... 3E57427F3EXAMPLE
    REST.GET.OBJECT reports/q3.pdf "GET /my-bucket/reports/q3.pdf HTTP/1.1" 200 - 2662992 ...

Only successful object reads (``REST.GET.OBJECT`` with a 2xx status) count as an
access. Events are matched to inventoried objects by (bucket, key) and stored
with ``source = "s3_access_log"``; duplicates of an already-imported event are skipped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import unquote

from sqlalchemy.orm import Session

from app.core.timeutil import as_utc
from app.db.models import AccessEvent, FileRecord, StorageLocation
from app.services.audit import log_operation

_FIELD = re.compile(r'"[^"]*"|\[[^\]]*\]|\S+')
READ_OPERATIONS = {"REST.GET.OBJECT"}
SOURCE = "s3_access_log"
MAX_LOG_OBJECTS = 5000


@dataclass(frozen=True)
class LogAccess:
    bucket: str
    key: str
    occurred_at: datetime


def parse_line(line: str) -> LogAccess | None:
    fields = _FIELD.findall(line.strip())
    if len(fields) < 10:
        return None
    _owner, bucket, raw_time, _ip, _requester, _request_id, operation, key, _uri, status = fields[:10]
    if operation not in READ_OPERATIONS or key == "-" or not status.isdigit() or not 200 <= int(status) < 300:
        return None
    try:
        occurred = datetime.strptime(raw_time.strip("[]"), "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None
    return LogAccess(bucket=bucket, key=unquote(key), occurred_at=as_utc(occurred))


def parse_lines(lines: Iterable[str]) -> tuple[list[LogAccess], int]:
    accesses, skipped = [], 0
    for line in lines:
        if not line.strip():
            continue
        parsed = parse_line(line)
        if parsed is None:
            skipped += 1
        else:
            accesses.append(parsed)
    return accesses, skipped


def store(db: Session, accesses: list[LogAccess]) -> dict[str, int]:
    """Match accesses to inventoried S3 objects and insert new events."""
    buckets = {a.bucket for a in accesses}
    locations = db.query(StorageLocation).filter(StorageLocation.kind == "s3", StorageLocation.bucket.in_(buckets)).all()
    loc_bucket = {loc.id: loc.bucket for loc in locations}
    index: dict[tuple[str, str], int] = {}
    if loc_bucket:
        for record in db.query(FileRecord).filter(FileRecord.location_id.in_(loc_bucket)):
            index[(loc_bucket[record.location_id], record.rel_path)] = record.id
    existing = {
        (fid, as_utc(ts))
        for fid, ts in db.query(AccessEvent.file_id, AccessEvent.occurred_at).filter(
            AccessEvent.source == SOURCE, AccessEvent.file_id.in_(set(index.values()))
        )
    }
    imported = unmatched = duplicate = 0
    for access in accesses:
        fid = index.get((access.bucket, access.key))
        if fid is None:
            unmatched += 1
            continue
        if (fid, access.occurred_at) in existing:
            duplicate += 1
            continue
        existing.add((fid, access.occurred_at))
        db.add(AccessEvent(file_id=fid, occurred_at=access.occurred_at, source=SOURCE))
        imported += 1
    db.commit()
    return {"imported": imported, "unmatched_objects": unmatched, "duplicates_skipped": duplicate}


def import_text(db: Session, text: str) -> dict[str, int]:
    accesses, skipped = parse_lines(text.splitlines())
    result = {**store(db, accesses), "non_read_or_invalid_lines": skipped}
    log_operation(db, "import_s3_access_log", "success", details=result)
    return result


def fetch_from_bucket(db: Session, log_bucket: str, prefix: str = "", max_objects: int = 1000) -> dict[str, int]:
    """Read log objects written by S3 server access logging into ``log_bucket``/``prefix``."""
    from app.services import s3_service

    s3_service.check_bucket_allowed(log_bucket)
    client = s3_service.client()
    paginator = client.get_paginator("list_objects_v2")
    lines: list[str] = []
    objects = 0
    for page in paginator.paginate(Bucket=log_bucket, Prefix=prefix or ""):
        for obj in page.get("Contents", []):
            if objects >= min(max_objects, MAX_LOG_OBJECTS):
                break
            body = client.get_object(Bucket=log_bucket, Key=obj["Key"])["Body"].read(10 * 1024 * 1024)
            lines.extend(body.decode("utf-8", errors="replace").splitlines())
            objects += 1
    accesses, skipped = parse_lines(lines)
    result = {**store(db, accesses), "log_objects_read": objects, "non_read_or_invalid_lines": skipped}
    log_operation(db, "fetch_s3_access_logs", "success", target=f"s3://{log_bucket}/{prefix}", details=result)
    return result
