"""Database-facing orchestration of the access-prediction model."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutil import as_utc, now_utc
from app.db.models import AccessEvent, AccessPrediction, FileRecord, ModelEvaluation
from app.ml import access_model, synthetic
from app.services.audit import log_operation

SYNTHETIC_SOURCES = {"synthetic_demo"}
MIN_OBSERVED_FILES = 30
MIN_OBSERVED_EVENTS = 100


def train_synthetic(db: Session, n_files: int, horizon: int, seed: int) -> ModelEvaluation:
    ds = synthetic.generate(n_files=n_files, seed=seed)
    result = access_model.train_and_evaluate(ds, horizon=horizon, seed=seed)
    return _persist(db, result, "synthetic", horizon, {"n_files": n_files, "history_days": ds.history_days})


def observed_dataset(db: Session) -> synthetic.SyntheticDataset:
    """Build a dataset from *observed* (imported) access events only."""
    rows = db.query(AccessEvent.file_id, AccessEvent.occurred_at).filter(AccessEvent.source.notin_(SYNTHETIC_SOURCES)).all()
    if len(rows) < MIN_OBSERVED_EVENTS:
        raise ValueError(f"Only {len(rows)} observed access events; at least {MIN_OBSERVED_EVENTS} are required.")
    times = [as_utc(t) for _, t in rows]
    origin = min(times)
    file_ids = sorted({fid for fid, _ in rows})
    if len(file_ids) < MIN_OBSERVED_FILES:
        raise ValueError(f"Observed events cover {len(file_ids)} files; at least {MIN_OBSERVED_FILES} are required.")
    records = {f.id: f for f in db.query(FileRecord).filter(FileRecord.id.in_(file_ids))}
    first_event: dict[int, datetime] = {}
    for fid, t in rows:
        t = as_utc(t)
        first_event[fid] = min(first_event.get(fid, t), t)
    files = pd.DataFrame(
        {
            "file_id": file_ids,
            "category": [records[f].category for f in file_ids],
            "size_bytes": [records[f].size_bytes for f in file_ids],
            "created_day": [max(0, ((as_utc(records[f].created_at_fs) or first_event[f]) - origin).days) for f in file_ids],
        }
    )
    events = pd.DataFrame({"file_id": [f for f, _ in rows], "day": [(as_utc(t) - origin).days for _, t in rows]})
    history = int(events.day.max()) + 1
    return synthetic.SyntheticDataset(files=files, events=events, history_days=history, seed=0, kind="observed")


def train_observed(db: Session, horizon: int) -> ModelEvaluation:
    ds = observed_dataset(db)
    result = access_model.train_and_evaluate(ds, horizon=horizon)
    return _persist(db, result, "observed", horizon, {"history_days": ds.history_days, "files": len(ds.files)})


def _persist(db: Session, result, kind: str, horizon: int, extra: dict) -> ModelEvaluation:
    evaluation = ModelEvaluation(
        dataset_kind=kind,
        horizon_days=horizon,
        params={**result.params, **extra},
        metrics=result.metrics,
        selected_model=result.selected_model,
    )
    db.add(evaluation)
    db.flush()
    path = access_model.save_model(result, get_settings().models_dir, evaluation.id, kind)
    evaluation.artifact_path = str(path)
    db.commit()
    log_operation(
        db,
        "ml_train",
        "success",
        target=f"model_evaluation:{evaluation.id}",
        details={"dataset_kind": kind, "selected": result.selected_model, "metrics": result.metrics[result.selected_model]},
    )
    return evaluation


def latest_evaluation(db: Session) -> ModelEvaluation | None:
    """Prefer the newest model trained on observed data; fall back to the newest synthetic one."""
    for kind in ("observed", "synthetic"):
        ev = (
            db.query(ModelEvaluation)
            .filter(ModelEvaluation.dataset_kind == kind, ModelEvaluation.artifact_path.is_not(None))
            .order_by(ModelEvaluation.id.desc())
            .first()
        )
        if ev and ev.artifact_path and Path(ev.artifact_path).exists():
            return ev
    return None


def predict_files(db: Session, file_ids: list[int] | None = None) -> list[AccessPrediction]:
    evaluation = latest_evaluation(db)
    query = db.query(FileRecord).filter(FileRecord.status != "missing")
    if file_ids:
        query = query.filter(FileRecord.id.in_(file_ids))
    records = query.all()
    db.query(AccessPrediction).filter(AccessPrediction.file_id.in_([r.id for r in records])).delete(synchronize_session=False)
    bundle = access_model.load_model(Path(evaluation.artifact_path)) if evaluation else None
    horizon = evaluation.horizon_days if evaluation else 30
    now = now_utc()
    predictions = []
    events_by_file: dict[int, list[tuple[datetime, str]]] = {}
    for fid, ts, source in db.query(AccessEvent.file_id, AccessEvent.occurred_at, AccessEvent.source).filter(
        AccessEvent.file_id.in_([r.id for r in records])
    ):
        events_by_file.setdefault(fid, []).append((as_utc(ts), source))
    for record in records:
        events = events_by_file.get(record.id, [])
        pred = AccessPrediction(file_id=record.id, evaluation_id=evaluation.id if evaluation else None, horizon_days=horizon)
        if bundle is None:
            pred.reliability, pred.reason = "unavailable", "No trained model. Train one on the ML page."
        elif not events:
            pred.reliability = "unavailable"
            pred.reason = "No access history for this file (filesystem access times are not treated as reliable)."
        else:
            sources = {s for _, s in events}
            synthetic_history = bool(sources & SYNTHETIC_SOURCES)
            pred.history_source = "synthetic_demo" if synthetic_history else sorted(sources)[0]
            pred.probability = round(
                access_model.predict_file(
                    bundle,
                    [t for t, _ in events],
                    as_utc(record.created_at_fs) or as_utc(record.first_seen_at),
                    record.size_bytes,
                    record.category,
                    now,
                ),
                4,
            )
            reasons = []
            if evaluation.dataset_kind == "synthetic":
                reasons.append("model trained on synthetic data")
            if synthetic_history:
                reasons.append("access history is synthetic demo data")
            if len(events) < access_model.MIN_HISTORY_EVENTS_FOR_OK:
                reasons.append(f"only {len(events)} recorded accesses")
            pred.reliability = "low" if reasons else "ok"
            pred.reason = ("Illustrative only: " + "; ".join(reasons) + ".") if reasons else "Based on observed access history."
        predictions.append(pred)
    db.add_all(predictions)
    db.commit()
    return predictions


def generate_demo_events(db: Session, location_id: int, seed: int = 7, days: int = 365) -> int:
    """Attach clearly-labelled SYNTHETIC access histories to a location's files (demo only)."""
    records = db.query(FileRecord).filter(FileRecord.location_id == location_id, FileRecord.status != "missing").all()
    db.query(AccessEvent).filter(AccessEvent.file_id.in_([r.id for r in records]), AccessEvent.source == "synthetic_demo").delete(
        synchronize_session=False
    )
    rng = np.random.default_rng(seed)
    now = now_utc()
    count = 0
    for record in records:
        profile = rng.choice(["hot", "warm", "cold", "dormant"], p=[0.2, 0.3, 0.3, 0.2])
        rate = {"hot": 0.5, "warm": 0.08, "cold": 0.01, "dormant": 0.0}[profile]
        decay_start = {"hot": 0, "warm": 60, "cold": 200, "dormant": days}[profile]
        for day_ago in range(days):
            lam = rate if day_ago < decay_start + 90 else rate * 0.1
            if profile in ("cold", "dormant") and day_ago > 250:
                lam = 0.05
            for _ in range(rng.poisson(lam)):
                db.add(
                    AccessEvent(
                        file_id=record.id,
                        occurred_at=now - timedelta(days=day_ago, hours=float(rng.uniform(0, 23))),
                        source="synthetic_demo",
                    )
                )
                count += 1
    db.commit()
    log_operation(
        db,
        "generate_demo_access_events",
        "info",
        target=f"location:{location_id}",
        details={"events": count, "label": "SYNTHETIC demo data - not observed behaviour"},
    )
    return count


def import_events_csv(db: Session, content: bytes, location_id: int | None = None) -> dict:
    """Import observed accesses from CSV with columns ``path,timestamp`` (ISO 8601).

    ``path`` is matched against file relative paths (or S3 keys).
    """
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not {"path", "timestamp"} <= {f.strip().lower() for f in reader.fieldnames}:
        raise ValueError("CSV must have 'path' and 'timestamp' columns.")
    query = db.query(FileRecord)
    if location_id:
        query = query.filter(FileRecord.location_id == location_id)
    by_path = {r.rel_path: r.id for r in query}
    imported, unmatched, invalid = 0, 0, 0
    for row in reader:
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        fid = by_path.get(row["path"].replace("\\", "/"))
        if fid is None:
            unmatched += 1
            continue
        try:
            ts = as_utc(datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")))
        except ValueError:
            invalid += 1
            continue
        db.add(AccessEvent(file_id=fid, occurred_at=ts, source="imported_log"))
        imported += 1
    db.commit()
    log_operation(
        db, "import_access_events", "success", details={"imported": imported, "unmatched": unmatched, "invalid": invalid}
    )
    return {"imported": imported, "unmatched_paths": unmatched, "invalid_rows": invalid}


def event_stats(db: Session) -> dict:
    rows = db.query(AccessEvent.source).all()
    stats: dict[str, int] = {}
    for (source,) in rows:
        stats[source] = stats.get(source, 0) + 1
    return stats
