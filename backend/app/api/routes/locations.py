from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_or_404
from app.core.config import get_settings
from app.core.paths import PathNotAllowedError, validate_scan_root
from app.db.models import FileRecord, ScanJob, StorageLocation
from app.schemas.api import BrowseEntry, BrowseOut, LocationCreate, LocationOut, ScanCreate, ScanOut
from app.services import s3_service, scanner
from app.services.audit import log_operation

router = APIRouter(tags=["locations & scans"])


def _location_out(db: Session, loc: StorageLocation) -> LocationOut:
    count, total = (
        db.query(func.count(FileRecord.id), func.coalesce(func.sum(FileRecord.size_bytes), 0))
        .filter(FileRecord.location_id == loc.id, FileRecord.status != "missing")
        .one()
    )
    last = db.query(ScanJob).filter(ScanJob.location_id == loc.id).order_by(ScanJob.id.desc()).first()
    out = LocationOut.model_validate(loc)
    out.file_count, out.total_bytes = int(count), int(total)
    out.last_scan_status = last.status if last else None
    return out


@router.get("/locations", response_model=list[LocationOut])
def list_locations(db: Session = Depends(get_db)):
    return [_location_out(db, loc) for loc in db.query(StorageLocation).order_by(StorageLocation.id)]


@router.post("/locations", response_model=LocationOut, status_code=201)
def create_location(body: LocationCreate, db: Session = Depends(get_db)):
    if body.kind == "local":
        if not body.path:
            raise HTTPException(422, "'path' is required for local locations.")
        resolved = validate_scan_root(body.path)
        existing = (
            db.query(StorageLocation).filter(StorageLocation.kind == "local", StorageLocation.path == str(resolved)).first()
        )
        if existing:
            raise HTTPException(409, f"Location already registered (id {existing.id}).")
        loc = StorageLocation(kind="local", path=str(resolved), name=body.name or resolved.name)
    else:
        if not body.bucket:
            raise HTTPException(422, "'bucket' is required for S3 locations.")
        s3_service.check_bucket_allowed(body.bucket)
        loc = StorageLocation(
            kind="s3", bucket=body.bucket, prefix=body.prefix or "", name=body.name or f"s3://{body.bucket}/{body.prefix or ''}"
        )
    db.add(loc)
    db.commit()
    log_operation(db, "location_registered", "success", target=loc.path or f"s3://{loc.bucket}/{loc.prefix}")
    return _location_out(db, loc)


@router.delete("/locations/{location_id}", status_code=204)
def delete_location(location_id: int, db: Session = Depends(get_db)):
    """Removes the location and its analysis records from OptiStore. Files on disk/S3 are untouched."""
    loc = get_or_404(db, StorageLocation, location_id)
    target = loc.path or f"s3://{loc.bucket}/{loc.prefix}"
    db.delete(loc)
    db.commit()
    from app.services import duplicates, semantic

    duplicates.recompute_duplicate_groups(db)
    semantic.recompute_similarity(db)
    log_operation(db, "location_removed", "success", target=target, details={"note": "Analysis records only; no files deleted."})


@router.get("/locations/browse", response_model=BrowseOut)
def browse(path: str | None = Query(default=None, max_length=1024)):
    """List sub-directories inside the authorised scan roots (to help pick a folder)."""
    roots = [r for r in get_settings().resolved_scan_roots() if r.is_dir()]
    if not path:
        return BrowseOut(
            roots=[str(r) for r in roots],
            current=None,
            parent=None,
            directories=[BrowseEntry(name=r.name or str(r), path=str(r)) for r in roots],
        )
    current = validate_scan_root(path)
    dirs = []
    try:
        for child in sorted(current.iterdir()):
            if child.is_dir() and not child.is_symlink() and not child.name.startswith("."):
                dirs.append(BrowseEntry(name=child.name, path=str(child)))
    except PermissionError as exc:
        raise PathNotAllowedError("Directory cannot be read.") from exc
    parent = None
    if current not in roots:
        try:
            parent = str(validate_scan_root(str(Path(current).parent)))
        except PathNotAllowedError:
            parent = None
    return BrowseOut(roots=[str(r) for r in roots], current=str(current), parent=parent, directories=dirs[:500])


@router.post("/scans", response_model=ScanOut, status_code=202)
def start_scan(body: ScanCreate, background: BackgroundTasks, db: Session = Depends(get_db)):
    loc = get_or_404(db, StorageLocation, body.location_id)
    running = db.query(ScanJob).filter(ScanJob.location_id == loc.id, ScanJob.status.in_(["pending", "running"])).first()
    if running:
        raise HTTPException(409, f"Scan {running.id} is already in progress for this location.")
    if loc.kind == "local":
        validate_scan_root(loc.path or "")  # re-check authorisation at scan time
    job = ScanJob(location_id=loc.id)
    db.add(job)
    db.commit()
    background.add_task(scanner.run_scan, job.id)
    return job


@router.get("/scans", response_model=list[ScanOut])
def list_scans(limit: int = Query(default=20, ge=1, le=200), db: Session = Depends(get_db)):
    return db.query(ScanJob).order_by(ScanJob.id.desc()).limit(limit).all()


@router.get("/scans/{scan_id}", response_model=ScanOut)
def get_scan(scan_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, ScanJob, scan_id)


@router.post("/scans/{scan_id}/cancel", response_model=ScanOut)
def cancel_scan(scan_id: int, db: Session = Depends(get_db)):
    job = get_or_404(db, ScanJob, scan_id)
    if job.status not in ("pending", "running"):
        raise HTTPException(409, f"Scan is already {job.status}.")
    job.cancel_requested = True
    db.commit()
    return job
