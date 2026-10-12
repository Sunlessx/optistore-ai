"""Optimisation orchestration: combine all analyses into a prioritised, explainable plan.

Only the explicitly defined action types below can ever be executed; there is
no free-form command execution. Every executable action must first be
approved, cloud and lossy actions additionally require the caller to
acknowledge the listed risks, and every decision/execution is audited.

Action types
------------
review_duplicate_group      manual  – user removes redundant copies themselves (never auto-deleted)
create_consolidation_draft  local   – creates a draft record; originals untouched
create_compressed_copy      local   – writes a compressed copy to the outputs directory
s3_change_storage_class     cloud   – in-place storage-class change (S3_WRITE_ENABLED required)
storage_class_simulation    info    – simulated placement for local files (nothing to execute)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.timeutil import now_utc
from app.db.models import (
    CompressionResult,
    DuplicateGroup,
    FileRecord,
    OptimizationPlan,
    PlanAction,
    SimilarityPair,
    StorageLocation,
)
from app.services import compression, consolidation, cost_engine, ml_service, runtime_settings, s3_service, semantic
from app.services.audit import log_operation

GIB = 1024**3
EXECUTABLE_TYPES = {"create_consolidation_draft", "create_compressed_copy", "s3_change_storage_class"}
ACK_REQUIRED_TYPES = {"s3_change_storage_class"}
MAX_AUTO_COMPRESSION_BENCHMARKS = 100
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


class ActionError(ValueError):
    pass


def _std_price(db: Session) -> float:
    region = runtime_settings.get(db, "pricing_region")
    return cost_engine.region_prices(region)["STANDARD"]["storage_gb_month"]


def build_plan(db: Session, strategy: str, location_id: int | None = None) -> OptimizationPlan:
    if strategy not in cost_engine.STRATEGIES:
        raise ValueError(f"Unknown strategy '{strategy}'.")
    settings = runtime_settings.get_all(db)
    price = _std_price(db)
    files_q = db.query(FileRecord).filter(FileRecord.status != "missing")
    if location_id:
        files_q = files_q.filter(FileRecord.location_id == location_id)
    files = files_q.all()
    if not files:
        raise ValueError("No analysed files. Run a scan first.")
    file_ids = {f.id for f in files}
    by_id = {f.id: f for f in files}
    locations = {loc.id: loc for loc in db.query(StorageLocation).all()}
    actions: list[PlanAction] = []

    # 1. Exact duplicates ----------------------------------------------------------------------
    dup_bytes = 0
    for group in db.query(DuplicateGroup).all():
        members = [f for f in group.files if f.id in file_ids]
        if len(members) < 2:
            continue
        redundant = group.size_bytes * (len(members) - 1)
        dup_bytes += redundant
        keep = sorted(members, key=lambda f: (len(f.rel_path), f.rel_path))[0]
        actions.append(
            PlanAction(
                action_type="review_duplicate_group",
                title=f"Review {len(members)} identical copies of {keep.name}",
                reason="Files have identical SHA-256 content hashes and sizes.",
                evidence={"sha256": group.sha256, "paths": [m.rel_path for m in members], "suggested_keep": keep.rel_path},
                target_file_ids=[m.id for m in members],
                expected_bytes_saved=redundant,
                expected_monthly_savings_usd=redundant / GIB * price,
                risk_level="medium",
                risks=[
                    "Other systems or links may reference a specific copy's path.",
                    "OptiStore never deletes files; removal is manual.",
                ],
                reversible=True,
                reversibility_note="Nothing is changed by OptiStore; approval only records that the group was reviewed.",
                executable=False,
            )
        )

    # 2. Semantic consolidation candidates ------------------------------------------------------
    threshold = float(settings["similarity_threshold"])
    pairs = [p for p in db.query(SimilarityPair).all() if p.file_a_id in file_ids and p.file_b_id in file_ids]
    for group in semantic.similarity_groups(pairs, threshold):
        members, seen_hashes = [], set()
        for fid in sorted(group):
            record = by_id[fid]
            if record.sha256 in seen_hashes:
                continue  # exact copies are handled by duplicate review, not consolidation
            seen_hashes.add(record.sha256)
            members.append(record)
        if len(members) < 2 or any(not (m.text_content or "").strip() for m in members):
            continue
        scores = [p.score for p in pairs if p.file_a_id in group and p.file_b_id in group]
        sizes = [m.size_bytes for m in members]
        potential = sum(sizes) - max(sizes)
        actions.append(
            PlanAction(
                action_type="create_consolidation_draft",
                title=f"Draft a consolidated document from {len(members)} related documents",
                reason=f"Documents are semantically similar (max cosine {max(scores):.2f} ≥ threshold {threshold}).",
                evidence={
                    "paths": [m.rel_path for m in members],
                    "pair_scores": [round(s, 3) for s in scores],
                    "note": "Similarity does not prove factual equivalence; the draft lists conflicts for review.",
                },
                target_file_ids=[m.id for m in members],
                params={"title": f"Consolidated: {members[0].name.rsplit('.', 1)[0]}"},
                expected_bytes_saved=potential,
                expected_monthly_savings_usd=potential / GIB * price,
                risk_level="medium",
                risks=[
                    "Potential saving assumes the sources would later be replaced by the reviewed draft.",
                    "Unique or conflicting statements must be checked by a person.",
                ],
                reversible=True,
                reversibility_note="Creates a new draft only; source documents are never modified.",
            )
        )

    # 3. Compression (measured on the actual files) ---------------------------------------------
    benchmarked = {
        fid for (fid,) in db.query(CompressionResult.file_id).filter(CompressionResult.file_id.in_(file_ids)).distinct()
    }
    candidates = sorted(
        (
            f
            for f in files
            if f.id not in benchmarked and f.status == "analyzed" and f.category in {"document", "data", "image", "video"}
        ),
        key=lambda f: -f.size_bytes,
    )[:MAX_AUTO_COMPRESSION_BENCHMARKS]
    for record in candidates:
        compression.analyze_file(db, record)
    best_results = (
        db.query(CompressionResult).filter(CompressionResult.is_best.is_(True), CompressionResult.file_id.in_(file_ids)).all()
    )
    for result in best_results:
        record = by_id[result.file_id]
        saved = result.original_bytes - (result.output_bytes or result.original_bytes)
        lossy = not result.lossless
        actions.append(
            PlanAction(
                action_type="create_compressed_copy",
                title=f"{'Lossy' if lossy else 'Lossless'} {result.method} copy of {record.name}",
                reason=f"Measured {result.percent_saved:.1f}% reduction ({result.original_bytes} → {result.output_bytes} bytes) in {result.duration_ms} ms.",
                evidence={
                    "method": result.method,
                    "lossless": result.lossless,
                    "quality": result.quality_note,
                    "metric": result.quality_metric,
                },
                target_file_ids=[record.id],
                params={"compression_result_id": result.id},
                expected_bytes_saved=saved,
                expected_monthly_savings_usd=saved / GIB * price,
                risk_level="medium" if lossy else "low",
                risks=(["Lossy: image quality is reduced and cannot be restored from the copy."] if lossy else [])
                + ["The copy is written to the outputs folder; replacing the original is a manual decision."],
                reversible=True,
                reversibility_note=result.restore_method or "",
            )
        )

    # 4. Access predictions + storage-class recommendations -------------------------------------
    predictions = ml_service.predict_files(db, list(file_ids))
    sim = cost_engine.simulate(db, strategy, location_id=location_id)
    s3_rows = [r for r in sim["rows"] if r["changed"] and not r["simulated_current_class"]]
    local_rows = [r for r in sim["rows"] if r["simulated_current_class"]]
    for row in s3_rows:
        record = by_id.get(row["file_id"])
        if record is None:
            continue
        location = locations[record.location_id]
        months = sim["horizon_months"]
        actions.append(
            PlanAction(
                action_type="s3_change_storage_class",
                title=f"Move s3://{location.bucket}/{record.rel_path} {row['current_class']} → {row['recommended_class']}",
                reason=f"Estimated {months}-month cost ${row['current_cost']:.4f} → ${row['projected_cost']:.4f} ({row['basis']}).",
                evidence={k: row[k] for k in ("basis", "reliability", "options", "breakeven_months", "assumed_reads_per_month")},
                target_file_ids=[record.id],
                params={"bucket": location.bucket, "key": record.rel_path, "target_class": row["recommended_class"]},
                expected_monthly_savings_usd=row["saving"] / months,
                estimated_cost_usd=next((o["transition"] for o in row["options"] if o["class"] == row["recommended_class"]), 0.0),
                risk_level="high" if row["recommended_class"] in ("GLACIER", "DEEP_ARCHIVE") else "medium",
                risks=row["risks"] or ["Retrieval characteristics change."],
                reversible=row["recommended_class"] not in ("GLACIER", "DEEP_ARCHIVE"),
                reversibility_note="Can be changed back with another copy (minimum-duration charges still apply)."
                if row["recommended_class"] not in ("GLACIER", "DEEP_ARCHIVE")
                else "Requires a restore (hours) before the class can be changed back; minimum-duration charges apply.",
            )
        )
    if local_rows:
        changed = [r for r in local_rows if r["changed"]]
        saving = sum(r["saving"] for r in local_rows)
        actions.append(
            PlanAction(
                action_type="storage_class_simulation",
                title=f"Simulated S3 placement for {len(local_rows)} local files ({len(changed)} would change class)",
                reason=f"If uploaded to S3 Standard, the '{strategy}' strategy estimates ${saving:.4f} saved over {sim['horizon_months']} months.",
                evidence={"by_recommended_class": sim["by_recommended_class"], "assumptions": sim["assumptions"]},
                target_file_ids=[r["file_id"] for r in changed][:500],
                expected_monthly_savings_usd=saving / sim["horizon_months"],
                risk_level="low",
                risks=["Simulation only: local files are not in S3."],
                reversible=True,
                reversibility_note="Informational; nothing is executed.",
                executable=False,
            )
        )

    # Prioritise: higher expected monthly saving and bytes first, lower risk breaks ties.
    for action in actions:  # column defaults are only applied on flush
        action.expected_bytes_saved = action.expected_bytes_saved or 0
        action.expected_monthly_savings_usd = action.expected_monthly_savings_usd or 0.0
        action.estimated_cost_usd = action.estimated_cost_usd or 0.0
    actions.sort(key=lambda a: (-a.expected_monthly_savings_usd, RISK_ORDER[a.risk_level], -a.expected_bytes_saved))
    for priority, action in enumerate(actions, start=1):
        action.priority = priority

    reliability_counts: dict[str, int] = {}
    for p in predictions:
        reliability_counts[p.reliability] = reliability_counts.get(p.reliability, 0) + 1
    plan = OptimizationPlan(
        location_id=location_id,
        strategy=strategy,
        summary={
            "inventory": {"files": len(files), "bytes": sum(f.size_bytes for f in files), "by_category": _by_category(files)},
            "exact_duplicates": {
                "groups": sum(1 for a in actions if a.action_type == "review_duplicate_group"),
                "redundant_bytes": dup_bytes,
            },
            "similarity": {
                "pairs_above_threshold": sum(1 for p in pairs if p.score >= threshold),
                "threshold": threshold,
                "consolidation_candidates": sum(1 for a in actions if a.action_type == "create_consolidation_draft"),
            },
            "compression": {
                "recommendations": len(best_results),
                "measured_bytes_saved": sum(a.expected_bytes_saved for a in actions if a.action_type == "create_compressed_copy"),
            },
            "predictions": reliability_counts,
            "storage_classes": {"totals": sim["totals"], "horizon_months": sim["horizon_months"], "region": sim["region"]},
            "savings": {
                "measured_compression_bytes": sum(
                    a.expected_bytes_saved for a in actions if a.action_type == "create_compressed_copy"
                ),
                "verified_duplicate_bytes": dup_bytes,
                "potential_consolidation_bytes": sum(
                    a.expected_bytes_saved for a in actions if a.action_type == "create_consolidation_draft"
                ),
                "estimated_storage_class_saving_usd": sim["totals"]["saving"],
            },
            "generated_at": now_utc().isoformat(),
        },
        actions=actions,
    )
    db.add(plan)
    db.commit()
    log_operation(
        db, "plan_created", "success", target=f"plan:{plan.id}", details={"strategy": strategy, "actions": len(actions)}
    )
    return plan


def _by_category(files: list[FileRecord]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in files:
        entry = out.setdefault(f.category, {"files": 0, "bytes": 0})
        entry["files"] += 1
        entry["bytes"] += f.size_bytes
    return out


def decide(db: Session, action: PlanAction, approve: bool, acknowledge_risks: bool = False) -> PlanAction:
    if action.status in ("executed",):
        raise ActionError("Action has already been executed.")
    if approve:
        needs_ack = action.action_type in ACK_REQUIRED_TYPES or (
            action.action_type == "create_compressed_copy" and not action.evidence.get("lossless", True)
        )
        if needs_ack and not acknowledge_risks:
            raise ActionError("This action changes cloud state or is lossy: set acknowledge_risks=true to approve.")
        action.status = "approved" if action.executable else "acknowledged"
    else:
        action.status = "rejected"
    action.decided_at = now_utc()
    db.commit()
    log_operation(
        db,
        "plan_action_" + ("approved" if approve else "rejected"),
        "info",
        target=f"plan:{action.plan_id}/action:{action.id}",
        details={"type": action.action_type, "title": action.title},
    )
    return action


def execute_action(db: Session, action: PlanAction, dry_run: bool) -> PlanAction:
    if action.action_type not in EXECUTABLE_TYPES:
        raise ActionError("This action type is informational or manual and cannot be executed.")
    if action.status != "approved":
        raise ActionError(f"Action must be approved before execution (current status: {action.status}).")
    target = f"plan:{action.plan_id}/action:{action.id}"
    try:
        if action.action_type == "create_compressed_copy":
            result = db.get(CompressionResult, action.params["compression_result_id"])
            if result is None:
                raise ActionError("Compression measurement no longer exists; regenerate the plan.")
            if dry_run:
                outcome = {
                    "dry_run": True,
                    "would_write": f"{result.method} copy of file {result.file_id} to outputs/compression/",
                }
            else:
                path = compression.write_output_copy(db, result)
                outcome = {
                    "output_path": str(path),
                    "measured_output_bytes": result.output_bytes,
                    "measured_bytes_saved": result.original_bytes - result.output_bytes,
                }
        elif action.action_type == "create_consolidation_draft":
            if dry_run:
                outcome = {"dry_run": True, "would_create": f"consolidation draft from files {action.target_file_ids}"}
            else:
                draft = consolidation.create(db, action.target_file_ids, action.params.get("title", "Consolidated document"))
                outcome = {
                    "consolidation_id": draft.id,
                    "draft_bytes": draft.output_bytes,
                    "source_bytes": draft.original_bytes,
                    "conflicts": draft.report.get("conflict_count", 0),
                }
        else:  # s3_change_storage_class
            p = action.params
            outcome = s3_service.change_storage_class(p["bucket"], p["key"], p["target_class"], dry_run=dry_run)
            if not dry_run:
                record = db.get(FileRecord, action.target_file_ids[0])
                if record:
                    record.storage_class = p["target_class"]
                outcome["savings_note"] = (
                    "Storage-class change verified; cost savings remain estimates until visible in AWS billing."
                )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        action = db.get(PlanAction, action.id)
        if not dry_run:
            action.status = "failed"
        action.result = {"error": f"{exc.__class__.__name__}: {exc}", "dry_run": dry_run}
        db.commit()
        log_operation(db, f"execute_{action.action_type}", "failure", target=target, details=action.result, dry_run=dry_run)
        _refresh_plan_status(db, action.plan)
        return action
    action.result = outcome
    if not dry_run:
        action.status = "executed"
        action.executed_at = now_utc()
    db.commit()
    log_operation(
        db, f"execute_{action.action_type}", "dry_run" if dry_run else "success", target=target, details=outcome, dry_run=dry_run
    )
    _refresh_plan_status(db, action.plan)
    return action


def _refresh_plan_status(db: Session, plan: OptimizationPlan) -> None:
    executable = [a for a in plan.actions if a.executable and a.status not in ("rejected",)]
    done = [a for a in executable if a.status == "executed"]
    if executable and len(done) == len(executable):
        plan.status = "executed"
    elif done:
        plan.status = "partially_executed"
    db.commit()


def _ts(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
