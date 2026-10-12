"""Duplicates, similarity, comparison and semantic search endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_or_404
from app.db.models import DuplicateGroup, FileRecord, SimilarityPair
from app.schemas.api import ComparisonOut, DuplicateGroupOut, DuplicateSummary, FileOut, SimilarityPairOut
from app.services import duplicates, runtime_settings, semantic
from app.services.audit import log_operation
from app.services.embeddings import embedder_status

router = APIRouter(tags=["analysis"])


@router.get("/duplicates/summary", response_model=DuplicateSummary)
def duplicate_summary(db: Session = Depends(get_db)):
    return duplicates.duplicate_summary(db)


@router.get("/duplicates", response_model=list[DuplicateGroupOut])
def duplicate_groups(location_id: int | None = None, db: Session = Depends(get_db)):
    out = []
    for group in db.query(DuplicateGroup).order_by(DuplicateGroup.redundant_bytes.desc()):
        files = group.files
        if location_id and not any(f.location_id == location_id for f in files):
            continue
        item = DuplicateGroupOut.model_validate(group)
        item.suggested_keep_id = duplicates.keeper_for_group(files).id if files else None
        out.append(item)
    return out


def _threshold(db: Session, min_score: float | None) -> float:
    return min_score if min_score is not None else float(runtime_settings.get(db, "similarity_threshold"))


@router.get("/similarity", response_model=list[SimilarityPairOut])
def similarity_pairs(
    min_score: float | None = Query(default=None, ge=0, le=1),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    pairs = (
        db.query(SimilarityPair)
        .filter(SimilarityPair.score >= _threshold(db, min_score))
        .order_by(SimilarityPair.score.desc())
        .limit(limit)
        .all()
    )
    return [
        SimilarityPairOut(
            id=p.id,
            score=p.score,
            coverage_a=p.coverage_a,
            coverage_b=p.coverage_b,
            embedding_model=p.embedding_model,
            file_a=FileOut.model_validate(p.file_a),
            file_b=FileOut.model_validate(p.file_b),
        )
        for p in pairs
    ]


@router.get("/similarity/groups")
def similarity_group_list(min_score: float | None = Query(default=None, ge=0, le=1), db: Session = Depends(get_db)):
    threshold = _threshold(db, min_score)
    pairs = db.query(SimilarityPair).all()
    out = []
    for group in semantic.similarity_groups(pairs, threshold):
        files = db.query(FileRecord).filter(FileRecord.id.in_(group)).all()
        scores = [p.score for p in pairs if p.file_a_id in group and p.file_b_id in group and p.score >= threshold]
        out.append(
            {
                "file_ids": sorted(group),
                "files": [FileOut.model_validate(f).model_dump(mode="json") for f in files],
                "max_score": max(scores),
                "min_score": min(scores),
            }
        )
    return sorted(out, key=lambda g: -g["max_score"])


@router.post("/similarity/recompute")
def recompute_similarity(db: Session = Depends(get_db)):
    stored = semantic.recompute_similarity(db)
    log_operation(db, "similarity_recompute", "success", details={"pairs": stored})
    return {"pairs_stored": stored}


@router.get("/compare", response_model=ComparisonOut)
def compare(a: int, b: int, db: Session = Depends(get_db)):
    if a == b:
        raise HTTPException(400, "Choose two different files.")
    file_a, file_b = get_or_404(db, FileRecord, a), get_or_404(db, FileRecord, b)
    result = semantic.compare_documents(db, file_a, file_b)
    return ComparisonOut(
        file_a=FileOut.model_validate(file_a),
        file_b=FileOut.model_validate(file_b),
        **{k: v for k, v in result.items() if k not in ("file_a_id", "file_b_id")},
    )


@router.get("/search")
def search(
    q: str = Query(min_length=2, max_length=500),
    limit: int = Query(default=10, ge=1, le=50),
    category: str | None = None,
    location_id: int | None = None,
    db: Session = Depends(get_db),
):
    results = semantic.search(db, q, limit=limit, category=category, location_id=location_id)
    status = embedder_status()
    return {
        "query": q,
        "results": results,
        "embedding_backend": status["active_backend"],
        "embedding_model": status["active_model"],
    }


@router.post("/search/reindex")
def reindex(force: bool = False, db: Session = Depends(get_db)):
    stats = semantic.index_location(db, None, force=force)
    pairs = semantic.recompute_similarity(db)
    log_operation(db, "search_reindex", "success", details={**stats, "pairs": pairs, "force": force})
    return {**stats, "similarity_pairs": pairs}
