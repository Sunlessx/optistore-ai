"""Cost-aware S3 storage-class recommendations.

For each object the engine estimates the *total* cost over a horizon for
every storage class allowed by the chosen strategy:

    storage (incl. minimum billable size, overhead, minimum duration)
  + request charges for expected reads
  + retrieval charges (per GB and per restore request)
  + one-time transition request charge (when changing class)
  + monitoring fees (Intelligent-Tiering)

and recommends the cheapest *acceptable* class. A cheaper storage price does
not imply a cheaper total: frequent reads, small objects and minimum storage
durations can make an archival class more expensive. All figures are
estimates based on documented, approximate list prices.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timeutil import as_utc, now_utc
from app.db.models import AccessEvent, AccessPrediction, FileRecord, StorageLocation, StorageRecommendation
from app.services import runtime_settings

GIB = 1024**3
DEFAULT_PRICING_FILE = Path(__file__).resolve().parents[1] / "pricing" / "s3_pricing.json"


@dataclass(frozen=True)
class Strategy:
    name: str
    description: str
    allowed_classes: tuple[str, ...]
    min_saving_ratio: float  # required relative saving before recommending a change
    accepted_reliability: tuple[str, ...]  # prediction reliabilities trusted for archival moves
    max_access_probability_for_archive: float


STRATEGIES: dict[str, Strategy] = {
    "conservative": Strategy(
        "conservative",
        "Millisecond access only (Standard, Standard-IA, Intelligent-Tiering); changes require ≥25% estimated saving "
        "and reliable predictions; otherwise only Intelligent-Tiering is considered.",
        ("STANDARD", "STANDARD_IA", "INTELLIGENT_TIERING"),
        0.25,
        ("ok",),
        0.2,
    ),
    "balanced": Strategy(
        "balanced",
        "Adds Glacier Instant Retrieval (still millisecond access, higher retrieval fees); ≥10% saving required; "
        "low-reliability predictions are used but flagged.",
        ("STANDARD", "STANDARD_IA", "INTELLIGENT_TIERING", "GLACIER_IR"),
        0.10,
        ("ok", "low"),
        0.35,
    ),
    "aggressive": Strategy(
        "aggressive",
        "Adds Glacier Flexible Retrieval and Deep Archive (hours to restore, 90/180-day minimum durations); "
        "≥5% saving required. Only for data you can wait hours for and will retain ≥ the minimum duration.",
        ("STANDARD", "STANDARD_IA", "INTELLIGENT_TIERING", "GLACIER_IR", "GLACIER", "DEEP_ARCHIVE"),
        0.05,
        ("ok", "low"),
        0.5,
    ),
}


@lru_cache
def _load_pricing(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pricing_document() -> dict:
    settings = get_settings()
    return _load_pricing(str(settings.pricing_file or DEFAULT_PRICING_FILE))


def region_prices(region: str) -> dict[str, dict]:
    doc = pricing_document()
    if region not in doc["regions"]:
        raise ValueError(f"No pricing assumptions for region '{region}'. Available: {list(doc['regions'])}")
    return doc["regions"][region]


def class_cost(cls: str, prices: dict, size_bytes: int, reads_per_month: float, months: int, transition: bool) -> dict:
    """Estimated USD cost of keeping one object in ``cls`` for ``months``."""
    p = prices[cls]
    billable = max(size_bytes, p["min_billable_bytes"]) + p["overhead_bytes"]
    billed_months = max(months, p["min_storage_days"] / 30.0)
    if cls == "INTELLIGENT_TIERING":
        tiers = p["tiers"]
        monitored = size_bytes >= p["monitoring_min_object_bytes"]
        storage = 0.0
        for month in range(months):
            if monitored and reads_per_month < 0.1 and month >= 3:
                rate = tiers["archive_instant"]
            elif monitored and reads_per_month < 1.0 and month >= 1:
                rate = tiers["infrequent"]
            else:
                rate = tiers["frequent"]
            storage += size_bytes / GIB * rate
        monitoring = (p["monitoring_per_1000_objects_month"] / 1000 * months) if monitored else 0.0
    else:
        storage = billable / GIB * p["storage_gb_month"] * billed_months
        monitoring = 0.0
    reads = reads_per_month * months
    requests = reads * p["get_per_1000"] / 1000
    retrieval = reads * (size_bytes / GIB * p["retrieval_per_gb"] + p["retrieval_request_per_1000"] / 1000)
    transition_cost = p["transition_per_1000"] / 1000 if transition else 0.0
    total = storage + monitoring + requests + retrieval + transition_cost
    return {
        "class": cls,
        "storage": storage,
        "monitoring": monitoring,
        "requests": requests,
        "retrieval": retrieval,
        "transition": transition_cost,
        "total": total,
        "billed_months": billed_months,
        "latency": p["first_byte_latency"],
        "requires_restore": p["requires_restore"],
    }


def expected_reads(
    prediction: AccessPrediction | None, recent_accesses_90d: int | None, reads_per_accessed_month: float
) -> tuple[float | None, str]:
    """Expected reads/month and the basis used."""
    if recent_accesses_90d is not None and prediction is not None and prediction.reliability == "ok":
        return recent_accesses_90d / 3.0, "observed access rate (last 90 days)"
    if prediction is not None and prediction.probability is not None:
        rate = prediction.probability * reads_per_accessed_month
        if recent_accesses_90d:
            rate = max(rate, recent_accesses_90d / 3.0)
        return rate, f"predicted access probability {prediction.probability:.2f} × {reads_per_accessed_month} reads"
    return None, "no access prediction available"


def recommend(
    size_bytes: int,
    current_class: str,
    reads_per_month: float | None,
    reliability: str,
    probability: float | None,
    strategy: Strategy,
    prices: dict,
    months: int,
) -> dict:
    current_class = current_class if current_class in prices else "STANDARD"
    assumed_reads = reads_per_month if reads_per_month is not None else 1.0
    current = class_cost(current_class, prices, size_bytes, assumed_reads, months, transition=False)
    risks: list[str] = []
    candidates = list(strategy.allowed_classes)
    trusted = reads_per_month is not None and reliability in strategy.accepted_reliability
    if not trusted:
        # Without trustworthy access information only classes without retrieval fees are considered.
        candidates = [c for c in candidates if c in ("STANDARD", "INTELLIGENT_TIERING", current_class)]
        risks.append("Access pattern unknown or not trusted by this strategy: only Intelligent-Tiering is considered.")
    elif probability is not None and probability > strategy.max_access_probability_for_archive:
        candidates = [c for c in candidates if not prices[c]["requires_restore"] and c != "GLACIER_IR"] or [current_class]
    options = [class_cost(c, prices, size_bytes, assumed_reads, months, transition=(c != current_class)) for c in candidates]
    if current_class not in candidates:
        options.append(current)
    best = min(options, key=lambda o: o["total"])
    saving = current["total"] - best["total"]
    if best["class"] != current_class and (current["total"] <= 0 or saving / current["total"] < strategy.min_saving_ratio):
        best = current
        saving = 0.0
    target = prices[best["class"]]
    if best["class"] != current_class:
        if target["min_storage_days"]:
            risks.append(
                f"Minimum storage duration {target['min_storage_days']} days: deleting or moving earlier is still billed."
            )
        if size_bytes < target["min_billable_bytes"]:
            risks.append(f"Object is smaller than the {target['min_billable_bytes'] // 1024} KB minimum billable size.")
        if target["requires_restore"]:
            risks.append(f"Objects must be restored before reading ({target['first_byte_latency']}).")
        if target["retrieval_per_gb"]:
            risks.append(f"Retrieval fee ${target['retrieval_per_gb']}/GB applies to every read.")
        if reliability == "low":
            risks.append("Based on a low-reliability (illustrative) access prediction.")
    breakeven = None
    if best["class"] != current_class:
        monthly_current = (current["total"]) / months
        monthly_new = (best["total"] - best["transition"]) / months
        if monthly_current > monthly_new:
            breakeven = round(best["transition"] / (monthly_current - monthly_new), 2)
    return {
        "current_class": current_class,
        "recommended_class": best["class"],
        "current_cost": current["total"],
        "projected_cost": best["total"],
        "saving": saving,
        "breakeven_months": breakeven,
        "assumed_reads_per_month": assumed_reads,
        "options": options,
        "risks": risks,
    }


def simulate(
    db: Session,
    strategy_name: str,
    location_id: int | None = None,
    months: int | None = None,
    region: str | None = None,
    persist: bool = True,
) -> dict:
    if strategy_name not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{strategy_name}'.")
    strategy = STRATEGIES[strategy_name]
    settings = runtime_settings.get_all(db)
    months = months or int(settings["cost_horizon_months"])
    region = region or settings["pricing_region"]
    prices = region_prices(region)
    reads_per_hit = float(settings["reads_per_accessed_month"])

    query = db.query(FileRecord).filter(FileRecord.status != "missing")
    if location_id:
        query = query.filter(FileRecord.location_id == location_id)
    records = query.all()
    kinds = dict(db.query(StorageLocation.id, StorageLocation.kind).all())
    predictions = {p.file_id: p for p in db.query(AccessPrediction).filter(AccessPrediction.file_id.in_([r.id for r in records]))}
    cutoff = now_utc()
    recent: dict[int, int] = {}
    for fid, ts in db.query(AccessEvent.file_id, AccessEvent.occurred_at).filter(
        AccessEvent.file_id.in_([r.id for r in records])
    ):
        if (cutoff - as_utc(ts)).days <= 90:
            recent[fid] = recent.get(fid, 0) + 1

    if persist:
        db.query(StorageRecommendation).filter(StorageRecommendation.strategy == strategy_name).filter(
            StorageRecommendation.file_id.in_([r.id for r in records])
        ).delete(synchronize_session=False)
    rows = []
    totals = {"current_cost": 0.0, "projected_cost": 0.0, "saving": 0.0, "objects_changed": 0}
    by_class: dict[str, dict] = {}
    for record in records:
        pred = predictions.get(record.id)
        has_events = record.id in recent or (pred is not None and pred.reliability != "unavailable")
        reads, basis = expected_reads(pred, recent.get(record.id, 0) if has_events else None, reads_per_hit)
        reliability = pred.reliability if pred else "unavailable"
        current_class = record.storage_class or "STANDARD"
        rec = recommend(
            record.size_bytes, current_class, reads, reliability, pred.probability if pred else None, strategy, prices, months
        )
        simulated = kinds.get(record.location_id) != "s3"
        details = {
            "basis": basis,
            "reliability": reliability,
            "simulated_current_class": simulated,
            "breakeven_months": rec["breakeven_months"],
            "assumed_reads_per_month": round(rec["assumed_reads_per_month"], 3),
            "risks": rec["risks"],
            "options": [{k: (round(v, 6) if isinstance(v, float) else v) for k, v in o.items()} for o in rec["options"]],
        }
        if persist:
            db.add(
                StorageRecommendation(
                    file_id=record.id,
                    strategy=strategy_name,
                    current_class=rec["current_class"],
                    recommended_class=rec["recommended_class"],
                    current_cost=rec["current_cost"],
                    projected_cost=rec["projected_cost"],
                    horizon_months=months,
                    details=details,
                )
            )
        totals["current_cost"] += rec["current_cost"]
        totals["projected_cost"] += rec["projected_cost"]
        totals["saving"] += rec["saving"]
        changed = rec["recommended_class"] != rec["current_class"]
        totals["objects_changed"] += int(changed)
        bucket = by_class.setdefault(rec["recommended_class"], {"objects": 0, "bytes": 0})
        bucket["objects"] += 1
        bucket["bytes"] += record.size_bytes
        rows.append(
            {
                "file_id": record.id,
                "name": record.name,
                "path": record.rel_path,
                "size_bytes": record.size_bytes,
                "current_class": rec["current_class"],
                "recommended_class": rec["recommended_class"],
                "current_cost": round(rec["current_cost"], 6),
                "projected_cost": round(rec["projected_cost"], 6),
                "saving": round(rec["saving"], 6),
                "changed": changed,
                **details,
            }
        )
    if persist:
        db.commit()
    rows.sort(key=lambda r: -r["saving"])
    doc = pricing_document()
    return {
        "strategy": strategy_name,
        "strategy_description": strategy.description,
        "horizon_months": months,
        "region": region,
        "totals": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in totals.items()},
        "by_recommended_class": by_class,
        "rows": rows,
        "assumptions": {
            "pricing_source": doc["source"],
            "pricing_as_of": doc["as_of"],
            "pricing_note": doc["_comment"],
            "reads_per_accessed_month": reads_per_hit,
            "unknown_access_assumption": "1 read/month when no access prediction exists",
            "local_files": "Local files are costed as if stored in S3 Standard (simulation only).",
            "excluded": "Data transfer, taxes, free tier, volume discounts, PUT costs of initial upload.",
        },
        "savings_kind": "estimated",
    }
