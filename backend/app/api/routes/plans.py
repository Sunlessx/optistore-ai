"""Optimisation plans, operation history, settings and dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_or_404
from app.core.config import get_settings
from app.db.models import (
    AccessPrediction,
    Consolidation,
    FileRecord,
    OperationLog,
    OptimizationPlan,
    PlanAction,
    ScanJob,
    SimilarityPair,
    StorageLocation,
    StorageRecommendation,
)
from app.schemas.api import (
    DecisionRequest,
    ExecuteRequest,
    OperationOut,
    Page,
    PlanActionOut,
    PlanCreate,
    PlanListItem,
    PlanOut,
    SettingsUpdate,
)
from app.services import compression, cost_engine, duplicates, orchestrator, runtime_settings
from app.services.embeddings import embedder_status
from app.services.tools import tool_status
from app.services.vector_index import active_backend

router = APIRouter()


@router.post("/plans", response_model=PlanOut, status_code=201, tags=["plans"])
def create_plan(body: PlanCreate, db: Session = Depends(get_db)):
    return orchestrator.build_plan(db, body.strategy, body.location_id)


@router.get("/plans", response_model=list[PlanListItem], tags=["plans"])
def list_plans(db: Session = Depends(get_db)):
    return db.query(OptimizationPlan).order_by(OptimizationPlan.id.desc()).limit(50).all()


@router.get("/plans/{plan_id}", response_model=PlanOut, tags=["plans"])
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    return get_or_404(db, OptimizationPlan, plan_id)


def _action(db: Session, plan_id: int, action_id: int) -> PlanAction:
    action = get_or_404(db, PlanAction, action_id)
    if action.plan_id != plan_id:
        raise HTTPException(404, "Action does not belong to this plan.")
    return action


@router.post("/plans/{plan_id}/actions/{action_id}/decision", response_model=PlanActionOut, tags=["plans"])
def decide(plan_id: int, action_id: int, body: DecisionRequest, db: Session = Depends(get_db)):
    return orchestrator.decide(db, _action(db, plan_id, action_id), body.approve, body.acknowledge_risks)


@router.post("/plans/{plan_id}/actions/{action_id}/execute", response_model=PlanActionOut, tags=["plans"])
def execute_one(plan_id: int, action_id: int, body: ExecuteRequest, db: Session = Depends(get_db)):
    return orchestrator.execute_action(db, _action(db, plan_id, action_id), body.dry_run)


@router.post("/plans/{plan_id}/execute", response_model=PlanOut, tags=["plans"])
def execute_plan(plan_id: int, body: ExecuteRequest, db: Session = Depends(get_db)):
    """Execute every *approved* executable action (or the given subset)."""
    plan = get_or_404(db, OptimizationPlan, plan_id)
    for action in list(plan.actions):
        if body.action_ids and action.id not in body.action_ids:
            continue
        if action.status == "approved" and action.action_type in orchestrator.EXECUTABLE_TYPES:
            orchestrator.execute_action(db, action, body.dry_run)
    db.refresh(plan)
    return plan


@router.get("/operations", response_model=Page[OperationOut], tags=["history"])
def operations(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    operation: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(OperationLog)
    if operation:
        query = query.filter(OperationLog.operation == operation)
    if status:
        query = query.filter(OperationLog.status == status)
    total = query.count()
    items = query.order_by(OperationLog.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return Page[OperationOut](items=items, total=total, page=page, page_size=page_size)


@router.get("/settings", tags=["settings"])
def get_app_settings(db: Session = Depends(get_db)):
    cfg = get_settings()
    return {
        "values": runtime_settings.get_all(db),
        "defaults": runtime_settings.DEFAULTS,
        "bounds": runtime_settings.BOUNDS,
        "deployment": {
            "environment": cfg.environment,
            "database": cfg.database_url.split("://", 1)[0],
            "allowed_scan_roots": [str(p) for p in cfg.resolved_scan_roots()],
            "max_file_size_mb": cfg.max_file_size_mb,
            "embedding_backend": cfg.embedding_backend,
            "embedding_model": cfg.embedding_model,
            "s3_write_enabled": cfg.s3_write_enabled,
            "api_key_required": bool(cfg.api_key),
            "pricing_regions": list(cost_engine.pricing_document()["regions"]),
        },
        "embedding": embedder_status(),
        "tools": tool_status(),
        "vector_backend": active_backend(),
    }


@router.put("/settings", tags=["settings"])
def update_app_settings(body: SettingsUpdate, db: Session = Depends(get_db)):
    if "pricing_region" in body.values:
        cost_engine.region_prices(body.values["pricing_region"])
    values = runtime_settings.update(db, body.values)
    from app.services.audit import log_operation

    log_operation(db, "settings_updated", "success", details=body.values)
    return {"values": values}


@router.get("/dashboard", tags=["dashboard"])
def dashboard(db: Session = Depends(get_db)):
    active = db.query(FileRecord).filter(FileRecord.status != "missing")
    total_files = active.count()
    total_bytes = int(active.with_entities(func.coalesce(func.sum(FileRecord.size_bytes), 0)).scalar())
    by_category = {
        c: {"files": int(n), "bytes": int(b or 0)}
        for c, n, b in active.with_entities(FileRecord.category, func.count(), func.sum(FileRecord.size_bytes)).group_by(
            FileRecord.category
        )
    }
    threshold = float(runtime_settings.get(db, "similarity_threshold"))
    strategy = runtime_settings.get(db, "default_strategy")
    recs = db.query(StorageRecommendation).filter(StorageRecommendation.strategy == strategy).all()
    std_price = cost_engine.region_prices(runtime_settings.get(db, "pricing_region"))["STANDARD"]["storage_gb_month"]
    latest_plan = db.query(OptimizationPlan).order_by(OptimizationPlan.id.desc()).first()
    pred_counts = dict(db.query(AccessPrediction.reliability, func.count()).group_by(AccessPrediction.reliability).all())
    return {
        "has_data": total_files > 0,
        "locations": db.query(StorageLocation).count(),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "by_category": by_category,
        "by_status": dict(active.with_entities(FileRecord.status, func.count()).group_by(FileRecord.status).all()),
        "duplicates": duplicates.duplicate_summary(db),
        "similar_pairs": db.query(SimilarityPair).filter(SimilarityPair.score >= threshold).count(),
        "similarity_threshold": threshold,
        "compression": compression.summary(db),
        "consolidations": db.query(Consolidation).count(),
        "estimated_monthly_storage_cost_usd": round(total_bytes / 1024**3 * std_price, 6),
        "cost_basis": f"All files priced at S3 Standard ${std_price}/GB-month (approximate list price).",
        "storage_class_simulation": {
            "strategy": strategy,
            "objects": len(recs),
            "current_cost": round(sum(r.current_cost for r in recs), 6),
            "projected_cost": round(sum(r.projected_cost for r in recs), 6),
            "horizon_months": recs[0].horizon_months if recs else None,
        },
        "predictions": {k: int(v) for k, v in pred_counts.items()},
        "latest_plan": {
            "id": latest_plan.id,
            "strategy": latest_plan.strategy,
            "status": latest_plan.status,
            "actions": len(latest_plan.actions),
            "created_at": latest_plan.created_at.isoformat(),
        }
        if latest_plan
        else None,
        "recent_scans": [
            {
                "id": s.id,
                "location_id": s.location_id,
                "status": s.status,
                "processed": s.processed_files,
                "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            }
            for s in db.query(ScanJob).order_by(ScanJob.id.desc()).limit(5)
        ],
        "embedding": embedder_status(),
    }
