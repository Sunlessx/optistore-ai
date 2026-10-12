"""File inventory scanner.

A scan walks an authorised location, records metadata for every file,
hashes content (SHA-256), extracts text where supported, and then triggers
the downstream analyses (embeddings, duplicates, similarity, snapshot).

Source files are opened read-only and are never modified.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.paths import ensure_within
from app.core.timeutil import now_utc
from app.db.models import FileRecord, ScanJob, StorageLocation, StorageSnapshot
from app.db.session import SessionLocal
from app.services import extractors
from app.services.audit import log_operation

logger = logging.getLogger(__name__)

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}
HASH_BLOCK = 1024 * 1024


class ScanCancelled(Exception):
    pass


@dataclass
class FileInfo:
    """Normalised description of one file/object, independent of where it lives."""

    rel_path: str
    name: str
    size: int
    modified_at: datetime | None
    accessed_at: datetime | None = None
    created_at: datetime | None = None
    storage_class: str | None = None
    etag: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(HASH_BLOCK):
            digest.update(block)
    return digest.hexdigest()


def _ts(value: float) -> datetime:
    return datetime.fromtimestamp(value, tz=UTC)


def list_local_files(root: Path, limit: int) -> list[tuple[Path, FileInfo]]:
    """Walk ``root`` without following symlinks; skip tooling directories."""
    found: list[tuple[Path, FileInfo]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for filename in sorted(filenames):
            full = Path(dirpath) / filename
            try:
                st = full.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                continue
            ensure_within(full, root)
            rel = full.relative_to(root).as_posix()
            created = getattr(st, "st_birthtime", None)
            info = FileInfo(
                rel_path=rel,
                name=filename,
                size=st.st_size,
                modified_at=_ts(st.st_mtime),
                accessed_at=_ts(st.st_atime),
                created_at=_ts(created) if created else (_ts(st.st_ctime) if os.name == "nt" else None),
            )
            found.append((full, info))
            if len(found) >= limit:
                return found
    return found


def analyze_local_file(full_path: Path, info: FileInfo) -> dict[str, Any]:
    """Compute hash, MIME/category, text and metadata for a local file."""
    settings = get_settings()
    ext, category, mime, supported = extractors.classify(info.name)
    out: dict[str, Any] = {
        "extension": ext,
        "category": category,
        "mime_type": mime,
        "metadata_json": {},
        "text_content": None,
        "status": "analyzed",
        "error": None,
        "sha256": None,
    }
    if info.size > settings.max_file_size_bytes:
        out["status"] = "skipped"
        out["error"] = f"File exceeds the {settings.max_file_size_mb} MB analysis limit."
        return out
    out["sha256"] = sha256_file(full_path)
    if not supported:
        out["status"] = "skipped"
        out["error"] = "Unsupported format: inventoried and hashed only."
        return out
    with full_path.open("rb") as fh:
        head = fh.read(16)
    if not extractors.check_magic(ext, head):
        out["status"] = "error"
        out["error"] = "File signature does not match its extension (possibly malformed or mislabelled)."
        return out
    try:
        if ext in extractors.TEXT_EXTRACTABLE:
            if info.size > settings.max_text_extract_bytes:
                out["metadata_json"]["text_skipped"] = "File too large for text extraction."
            else:
                extraction = extractors.extract_text_from_bytes(ext, full_path.read_bytes(), settings.max_text_chars)
                out["text_content"] = extraction.text
                out["metadata_json"].update(extraction.metadata)
                if extraction.warnings:
                    out["metadata_json"]["warnings"] = extraction.warnings
        elif category == "image":
            out["metadata_json"].update(extractors.image_metadata(full_path).metadata)
            if info.size <= settings.max_text_extract_bytes:
                text = extractors.image_text(full_path, settings.max_text_chars)
                if text:
                    out["text_content"] = text
                    out["metadata_json"]["ocr"] = True
        elif category == "archive":
            extraction = extractors.zip_metadata(full_path)
            out["metadata_json"].update(extraction.metadata)
            if extraction.warnings:
                out["metadata_json"]["warnings"] = extraction.warnings
        elif category == "video":
            from app.services import video

            probed = video.probe(full_path)
            if probed:
                out["metadata_json"].update(probed)
            elif ext in {".mp4", ".m4v", ".mov"}:
                extraction = extractors.mp4_metadata(full_path)
                out["metadata_json"].update(extraction.metadata)
                if extraction.warnings:
                    out["metadata_json"]["warnings"] = extraction.warnings
            else:
                out["metadata_json"]["note"] = (
                    "Install ffmpeg (ffprobe) for metadata of this container; built-in parsing covers MP4/MOV."
                )
    except Exception as exc:  # noqa: BLE001 - malformed files must not abort the scan
        out["status"] = "error"
        out["error"] = f"Could not parse file: {exc.__class__.__name__}: {str(exc)[:300]}"
    return out


def _check_cancel(db: Session, job: ScanJob) -> None:
    db.refresh(job, attribute_names=["cancel_requested"])
    if job.cancel_requested:
        raise ScanCancelled()


def _upsert(db: Session, location: StorageLocation, info: FileInfo, analysis: dict[str, Any]) -> FileRecord:
    record = (
        db.query(FileRecord).filter(FileRecord.location_id == location.id, FileRecord.rel_path == info.rel_path).one_or_none()
    )
    now = now_utc()
    if record is None:
        record = FileRecord(location_id=location.id, rel_path=info.rel_path, first_seen_at=now)
        db.add(record)
    record.name = info.name
    record.size_bytes = info.size
    record.modified_at = info.modified_at
    record.accessed_at = info.accessed_at
    # Filesystem access times are frequently disabled or updated by scanners/antivirus,
    # so they are recorded but never treated as reliable access history.
    record.accessed_at_reliable = False
    record.created_at_fs = info.created_at
    record.storage_class = info.storage_class
    record.etag = info.etag
    record.last_scanned_at = now
    for key, value in analysis.items():
        setattr(record, key, value)
    record.text_chars = len(record.text_content or "")
    if info.extra:
        record.metadata_json = {**(record.metadata_json or {}), **info.extra}
    return record


def run_scan(job_id: int) -> None:
    """Execute a scan job in its own DB session (called from a background task)."""
    db = SessionLocal()
    try:
        job = db.get(ScanJob, job_id)
        if job is None:
            return
        location = job.location
        job.status, job.phase, job.started_at = "running", "listing", now_utc()
        db.commit()
        settings = get_settings()
        seen: set[str] = set()
        try:
            if location.kind == "local":
                root = Path(location.path or "")
                if not root.is_dir():
                    raise FileNotFoundError(f"Directory no longer exists: {root}")
                entries = list_local_files(root, settings.max_files_per_scan)
                job.total_files, job.phase = len(entries), "analyzing files"
                db.commit()
                for i, (full, info) in enumerate(entries):
                    if i % 10 == 0:
                        _check_cancel(db, job)
                    analysis = analyze_local_file(full, info)
                    _record_progress(db, job, location, info, analysis, seen)
            elif location.kind == "s3":
                from app.services import s3_service

                entries = s3_service.list_objects(location.bucket or "", location.prefix or "")
                entries = entries[: settings.max_files_per_scan]
                job.total_files, job.phase = len(entries), "analyzing objects"
                db.commit()
                for i, info in enumerate(entries):
                    if i % 10 == 0:
                        _check_cancel(db, job)
                    analysis = s3_service.analyze_object(location.bucket or "", info)
                    _record_progress(db, job, location, info, analysis, seen)
            else:
                raise ValueError(f"Unknown location kind {location.kind}")

            # Files that disappeared since the last scan are kept (history) but flagged.
            for record in db.query(FileRecord).filter(FileRecord.location_id == location.id):
                if record.rel_path not in seen:
                    record.status = "missing"
                    record.duplicate_group_id = None
            db.commit()

            _check_cancel(db, job)
            job.phase = "indexing documents"
            db.commit()
            from app.services import duplicates, semantic

            semantic.index_location(db, location.id, cancel_check=lambda: _check_cancel(db, job))
            job.phase = "detecting duplicates"
            db.commit()
            duplicates.recompute_duplicate_groups(db)
            job.phase = "computing similarity"
            db.commit()
            semantic.recompute_similarity(db)
            record_snapshot(db, location.id)
            job.status, job.phase = "completed", "done"
            job.message = f"Analysed {job.processed_files} files ({job.error_count} errors, {job.skipped_count} skipped)."
        except ScanCancelled:
            job.status, job.phase, job.message = "cancelled", "cancelled", "Scan cancelled by user."
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scan %s failed", job_id)
            db.rollback()
            job = db.get(ScanJob, job_id)
            job.status, job.phase, job.message = "failed", "failed", f"{exc.__class__.__name__}: {exc}"
        job.finished_at = now_utc()
        db.commit()
        log_operation(
            db,
            "scan",
            status="success" if job.status == "completed" else ("info" if job.status == "cancelled" else "failure"),
            target=location.path or f"s3://{location.bucket}/{location.prefix or ''}",
            details={"job_id": job.id, "status": job.status, "message": job.message},
        )
    finally:
        db.close()


def _record_progress(db, job, location, info, analysis, seen) -> None:
    _upsert(db, location, info, analysis)
    seen.add(info.rel_path)
    job.processed_files += 1
    if analysis["status"] == "error":
        job.error_count += 1
    elif analysis["status"] == "skipped":
        job.skipped_count += 1
    db.commit()


def record_snapshot(db: Session, location_id: int | None) -> StorageSnapshot:
    query = db.query(FileRecord).filter(FileRecord.status != "missing")
    if location_id is not None:
        query = query.filter(FileRecord.location_id == location_id)
    files = query.all()
    snap = StorageSnapshot(
        location_id=location_id,
        total_bytes=sum(f.size_bytes for f in files),
        file_count=len(files),
    )
    db.add(snap)
    db.commit()
    return snap
