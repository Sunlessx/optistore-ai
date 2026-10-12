from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_or_404
from app.db.models import AccessEvent, AccessPrediction, CompressionResult, DocumentChunk, FileRecord, SimilarityPair
from app.schemas.api import CompressionOut, FileDetail, FileOut, FileStats, Page, PredictionOut

router = APIRouter(prefix="/files", tags=["files"])

SORTS = {
    "name": FileRecord.name,
    "size": FileRecord.size_bytes,
    "modified": FileRecord.modified_at,
    "type": FileRecord.extension,
    "path": FileRecord.rel_path,
}


@router.get("", response_model=Page[FileOut])
def list_files(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    location_id: int | None = None,
    category: str | None = None,
    status: str | None = None,
    q: str | None = Query(default=None, max_length=200),
    duplicates_only: bool = False,
    sort: Literal["name", "size", "modified", "type", "path"] = "path",
    order: Literal["asc", "desc"] = "asc",
    db: Session = Depends(get_db),
):
    query = db.query(FileRecord)
    if location_id:
        query = query.filter(FileRecord.location_id == location_id)
    if category:
        query = query.filter(FileRecord.category == category)
    if status:
        query = query.filter(FileRecord.status == status)
    else:
        query = query.filter(FileRecord.status != "missing")
    if q:
        like = f"%{q}%"
        query = query.filter(or_(FileRecord.name.ilike(like), FileRecord.rel_path.ilike(like)))
    if duplicates_only:
        query = query.filter(FileRecord.duplicate_group_id.is_not(None))
    total = query.count()
    column = SORTS[sort]
    query = query.order_by(column.desc() if order == "desc" else column.asc(), FileRecord.id)
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return Page[FileOut](items=items, total=total, page=page, page_size=page_size)


@router.get("/stats", response_model=FileStats)
def file_stats(location_id: int | None = None, db: Session = Depends(get_db)):
    base = db.query(FileRecord).filter(FileRecord.status != "missing")
    if location_id:
        base = base.filter(FileRecord.location_id == location_id)
    by_category = {
        cat: {"files": int(n), "bytes": int(b or 0)}
        for cat, n, b in base.with_entities(FileRecord.category, func.count(), func.sum(FileRecord.size_bytes)).group_by(
            FileRecord.category
        )
    }
    by_ext = {
        (ext or "(none)"): {"files": int(n), "bytes": int(b or 0)}
        for ext, n, b in base.with_entities(FileRecord.extension, func.count(), func.sum(FileRecord.size_bytes)).group_by(
            FileRecord.extension
        )
    }
    by_status = dict(base.with_entities(FileRecord.status, func.count()).group_by(FileRecord.status).all())
    return FileStats(
        total_files=sum(v["files"] for v in by_category.values()),
        total_bytes=sum(v["bytes"] for v in by_category.values()),
        by_category=by_category,
        by_status={k: int(v) for k, v in by_status.items()},
        by_extension=by_ext,
        largest=base.order_by(FileRecord.size_bytes.desc()).limit(10).all(),
    )


@router.get("/{file_id}", response_model=FileDetail)
def file_detail(file_id: int, db: Session = Depends(get_db)):
    record = get_or_404(db, FileRecord, file_id)
    detail = FileDetail.model_validate(record)
    detail.text_preview = (record.text_content or "")[:5000] or None
    detail.chunk_count = db.query(DocumentChunk).filter(DocumentChunk.file_id == file_id).count()
    detail.location_name = record.location.name
    if record.duplicate_group_id:
        detail.duplicates = [
            FileOut.model_validate(f)
            for f in db.query(FileRecord).filter(
                FileRecord.duplicate_group_id == record.duplicate_group_id, FileRecord.id != file_id
            )
        ]
    pairs = (
        db.query(SimilarityPair)
        .filter(or_(SimilarityPair.file_a_id == file_id, SimilarityPair.file_b_id == file_id))
        .order_by(SimilarityPair.score.desc())
        .limit(10)
        .all()
    )
    for pair in pairs:
        other = pair.file_b if pair.file_a_id == file_id else pair.file_a
        detail.similar.append({"file_id": other.id, "name": other.name, "path": other.rel_path, "score": pair.score})
    detail.compression = [
        CompressionOut.model_validate(c) for c in db.query(CompressionResult).filter(CompressionResult.file_id == file_id)
    ]
    pred = db.query(AccessPrediction).filter(AccessPrediction.file_id == file_id).order_by(AccessPrediction.id.desc()).first()
    detail.prediction = PredictionOut.model_validate(pred) if pred else None
    detail.access_event_count = db.query(AccessEvent).filter(AccessEvent.file_id == file_id).count()
    return detail
