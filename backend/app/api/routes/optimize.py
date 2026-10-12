"""Consolidation, compression, ML, cost, forecasting and AWS endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_or_404
from app.core.config import get_settings
from app.core.paths import ensure_within
from app.db.models import CompressionResult, Consolidation, FileRecord, ModelEvaluation, StorageLocation
from app.schemas.api import (
    CompressionBatch,
    CompressionOut,
    ConsolidationCreate,
    ConsolidationExport,
    ConsolidationOut,
    ConsolidationUpdate,
    DemoEventsRequest,
    EvaluationOut,
    FetchS3LogsRequest,
    PredictionOut,
    PredictRequest,
    SimulateRequest,
    TrainRequest,
    WhatIfRequest,
)
from app.services import access_logs, compression, consolidation, cost_engine, forecasting, ml_service, s3_service
from app.services.audit import log_operation

router = APIRouter()

# --- Consolidation ---------------------------------------------------------------------------


@router.get("/consolidations", response_model=list[ConsolidationOut], tags=["consolidation"])
def list_consolidations(db: Session = Depends(get_db)):
    return db.query(Consolidation).order_by(Consolidation.id.desc()).all()


@router.post("/consolidations", response_model=ConsolidationOut, status_code=201, tags=["consolidation"])
def create_consolidation(body: ConsolidationCreate, db: Session = Depends(get_db)):
    try:
        result = consolidation.create(db, body.file_ids, body.title)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    log_operation(
        db,
        "consolidation_created",
        "success",
        target=f"consolidation:{result.id}",
        details={"sources": body.file_ids, "conflicts": result.report.get("conflict_count")},
    )
    return result


@router.get("/consolidations/{cid}", response_model=ConsolidationOut, tags=["consolidation"])
def get_consolidation(cid: int, db: Session = Depends(get_db)):
    return get_or_404(db, Consolidation, cid)


@router.put("/consolidations/{cid}", response_model=ConsolidationOut, tags=["consolidation"])
def update_consolidation(cid: int, body: ConsolidationUpdate, db: Session = Depends(get_db)):
    item = get_or_404(db, Consolidation, cid)
    consolidation.save_edit(db, item, body.markdown)
    log_operation(db, "consolidation_edited", "success", target=f"consolidation:{cid}")
    return item


@router.post("/consolidations/{cid}/export", response_model=ConsolidationOut, tags=["consolidation"])
def export_consolidation(cid: int, body: ConsolidationExport, db: Session = Depends(get_db)):
    item = get_or_404(db, Consolidation, cid)
    path = consolidation.export(db, item, body.format)
    log_operation(db, "consolidation_exported", "success", target=str(path), details={"bytes": item.output_bytes})
    return item


@router.get("/consolidations/{cid}/download", tags=["consolidation"])
def download_consolidation(cid: int, db: Session = Depends(get_db)):
    item = get_or_404(db, Consolidation, cid)
    if not item.export_path:
        raise HTTPException(409, "Export the consolidation first.")
    path = ensure_within(Path(item.export_path), get_settings().outputs_dir)
    if not path.exists():
        raise HTTPException(404, "Exported file no longer exists.")
    media = "text/markdown" if item.export_format == "md" else "text/plain"
    return FileResponse(path, media_type=media, filename=path.name)


@router.delete("/consolidations/{cid}", status_code=204, tags=["consolidation"])
def delete_consolidation(cid: int, db: Session = Depends(get_db)):
    """Deletes the draft record (and its exported file in the outputs folder). Sources are untouched."""
    item = get_or_404(db, Consolidation, cid)
    if item.export_path:
        path = ensure_within(Path(item.export_path), get_settings().outputs_dir)
        path.unlink(missing_ok=True)
    db.delete(item)
    db.commit()
    log_operation(db, "consolidation_deleted", "success", target=f"consolidation:{cid}")


# --- Compression ---------------------------------------------------------------------------------


@router.post("/compression/files/{file_id}", response_model=list[CompressionOut], tags=["compression"])
def analyze_compression(file_id: int, db: Session = Depends(get_db)):
    record = get_or_404(db, FileRecord, file_id)
    results = compression.analyze_file(db, record)
    log_operation(db, "compression_benchmark", "success", target=record.rel_path, details={"methods": len(results)})
    return results


@router.post("/compression/batch", tags=["compression"])
def analyze_compression_batch(body: CompressionBatch, db: Session = Depends(get_db)):
    query = db.query(FileRecord).filter(FileRecord.status == "analyzed", FileRecord.category.in_(body.categories))
    if body.location_id:
        query = query.filter(FileRecord.location_id == body.location_id)
    records = query.order_by(FileRecord.size_bytes.desc()).limit(body.limit).all()
    for record in records:
        compression.analyze_file(db, record)
    log_operation(db, "compression_batch", "success", details={"files": len(records)})
    return {"files_analyzed": len(records), "summary": compression.summary(db)}


@router.get("/compression/results", tags=["compression"])
def compression_results(best_only: bool = False, limit: int = Query(500, ge=1, le=5000), db: Session = Depends(get_db)):
    query = db.query(CompressionResult)
    if best_only:
        query = query.filter(CompressionResult.is_best.is_(True))
    rows = query.order_by(CompressionResult.file_id, CompressionResult.output_bytes).limit(limit).all()
    files = {f.id: f for f in db.query(FileRecord).filter(FileRecord.id.in_({r.file_id for r in rows}))}
    return [
        {
            **CompressionOut.model_validate(r).model_dump(mode="json"),
            "file_name": files[r.file_id].name,
            "file_path": files[r.file_id].rel_path,
            "category": files[r.file_id].category,
        }
        for r in rows
        if r.file_id in files
    ]


@router.get("/compression/summary", tags=["compression"])
def compression_summary(db: Session = Depends(get_db)):
    return compression.summary(db)


# --- ML ------------------------------------------------------------------------------------------------


@router.post("/ml/train", response_model=EvaluationOut, tags=["ml"])
def train(body: TrainRequest, db: Session = Depends(get_db)):
    if body.dataset == "synthetic":
        return ml_service.train_synthetic(db, body.n_files, body.horizon_days, body.seed)
    return ml_service.train_observed(db, body.horizon_days)


@router.get("/ml/evaluations", response_model=list[EvaluationOut], tags=["ml"])
def evaluations(db: Session = Depends(get_db)):
    return db.query(ModelEvaluation).order_by(ModelEvaluation.id.desc()).limit(50).all()


@router.get("/ml/status", tags=["ml"])
def ml_status(db: Session = Depends(get_db)):
    latest = ml_service.latest_evaluation(db)
    return {
        "active_evaluation_id": latest.id if latest else None,
        "active_dataset_kind": latest.dataset_kind if latest else None,
        "access_events_by_source": ml_service.event_stats(db),
        "observed_requirements": {"min_events": ml_service.MIN_OBSERVED_EVENTS, "min_files": ml_service.MIN_OBSERVED_FILES},
    }


@router.post("/ml/predict", response_model=list[PredictionOut], tags=["ml"])
def predict(body: PredictRequest, db: Session = Depends(get_db)):
    return ml_service.predict_files(db, body.file_ids)


@router.post("/ml/demo-events", tags=["ml"])
def demo_events(body: DemoEventsRequest, db: Session = Depends(get_db)):
    get_or_404(db, StorageLocation, body.location_id)
    count = ml_service.generate_demo_events(db, body.location_id, body.seed)
    return {
        "events_created": count,
        "source": "synthetic_demo",
        "warning": "These access events are SYNTHETIC and exist only to demonstrate the workflow.",
    }


@router.post("/ml/access-events/import", tags=["ml"])
async def import_events(file: UploadFile = File(...), location_id: int | None = None, db: Session = Depends(get_db)):
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "CSV larger than 20 MB.")
    return ml_service.import_events_csv(db, content, location_id)


@router.post("/ml/access-events/import-s3-log", tags=["ml"])
async def import_s3_log(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload one or more concatenated S3 server access log files."""
    content = await file.read(50 * 1024 * 1024 + 1)
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "Log upload larger than 50 MB; use fetch-s3-logs instead.")
    return access_logs.import_text(db, content.decode("utf-8", errors="replace"))


@router.post("/ml/access-events/fetch-s3-logs", tags=["ml"])
def fetch_s3_logs(body: FetchS3LogsRequest, db: Session = Depends(get_db)):
    """Read S3 server access logs directly from the log bucket (requires AWS credentials)."""
    state = s3_service.status()
    if not state["connected"]:
        raise HTTPException(503, f"AWS not connected: {state['message']}")
    return access_logs.fetch_from_bucket(db, body.log_bucket, body.prefix, body.max_objects)


# --- Costs & forecasting -----------------------------------------------------------------------


@router.get("/costs/pricing", tags=["costs"])
def pricing():
    doc = cost_engine.pricing_document()
    return {**doc, "strategies": {k: v.__dict__ for k, v in cost_engine.STRATEGIES.items()}}


@router.post("/costs/simulate", tags=["costs"])
def simulate(body: SimulateRequest, db: Session = Depends(get_db)):
    result = cost_engine.simulate(db, body.strategy, body.location_id, body.horizon_months, body.region)
    result["rows"] = result["rows"][:500]
    return result


@router.post("/costs/compare", tags=["costs"])
def compare_strategies(body: SimulateRequest, db: Session = Depends(get_db)):
    out = {}
    for name in cost_engine.STRATEGIES:
        sim = cost_engine.simulate(db, name, body.location_id, body.horizon_months, body.region, persist=False)
        out[name] = {
            "totals": sim["totals"],
            "by_recommended_class": sim["by_recommended_class"],
            "description": sim["strategy_description"],
        }
    return out


@router.post("/costs/what-if", tags=["costs"])
def what_if(body: WhatIfRequest, db: Session = Depends(get_db)):
    """Cost of a hypothetical workload in every storage class, plus the strategy's recommendation."""
    from app.services import runtime_settings

    region = body.region or runtime_settings.get(db, "pricing_region")
    prices = cost_engine.region_prices(region)
    if body.current_class not in prices:
        raise HTTPException(400, f"Unknown storage class {body.current_class}.")
    size = int(body.object_size_mb * 1024 * 1024)
    n = body.object_count
    options = []
    for cls in prices:
        cost = cost_engine.class_cost(
            cls, prices, size, body.reads_per_object_per_month, body.horizon_months, cls != body.current_class
        )
        options.append(
            {
                **{k: (v * n if isinstance(v, float) and k not in ("billed_months",) else v) for k, v in cost.items()},
                "allowed_by_strategy": cls in cost_engine.STRATEGIES[body.strategy].allowed_classes,
            }
        )
    rec = cost_engine.recommend(
        size,
        body.current_class,
        body.reads_per_object_per_month,
        "ok",
        None,
        cost_engine.STRATEGIES[body.strategy],
        prices,
        body.horizon_months,
    )
    return {
        "region": region,
        "horizon_months": body.horizon_months,
        "options": sorted(options, key=lambda o: o["total"]),
        "recommendation": {
            "class": rec["recommended_class"],
            "current_cost": rec["current_cost"] * n,
            "projected_cost": rec["projected_cost"] * n,
            "saving": rec["saving"] * n,
            "breakeven_months": rec["breakeven_months"],
            "risks": rec["risks"],
        },
        "note": "Hypothetical workload with access treated as known (reads/month you entered). Approximate list prices.",
    }


@router.get("/forecast", tags=["costs"])
def forecast(
    location_id: int | None = None,
    months: int = Query(12, ge=1, le=60),
    growth_pct: float = Query(5.0, ge=-50, le=200),
    db: Session = Depends(get_db),
):
    return forecasting.forecast(db, location_id, months, growth_pct)


# --- AWS ------------------------------------------------------------------------------------------------


@router.get("/aws/status", tags=["aws"])
def aws_status():
    return s3_service.status()


@router.get("/aws/buckets", tags=["aws"])
def aws_buckets():
    state = s3_service.status()
    if not state["connected"]:
        raise HTTPException(503, f"AWS not connected: {state['message']}")
    return s3_service.list_buckets()
