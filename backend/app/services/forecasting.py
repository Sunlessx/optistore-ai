"""Storage growth forecasting.

Preferred data source: measured ``StorageSnapshot`` rows (one per completed
scan). When fewer than three snapshots spanning at least seven days exist,
no statistical forecast is made; instead the module returns

* a *reconstructed* history from file modification timestamps (an
  approximation: deleted files and later rewrites are invisible), and
* transparent baseline scenarios (flat, and user-chosen growth rates).
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
from sqlalchemy.orm import Session

from app.core.timeutil import as_utc, now_utc
from app.db.models import FileRecord, StorageSnapshot
from app.services import cost_engine, runtime_settings

GIB = 1024**3


def _monthly_cost(total_bytes: float, price: float) -> float:
    return total_bytes / GIB * price


def forecast(db: Session, location_id: int | None = None, months: int = 12, scenario_growth_pct: float = 5.0) -> dict:
    settings = runtime_settings.get_all(db)
    price = cost_engine.region_prices(settings["pricing_region"])["STANDARD"]["storage_gb_month"]
    snaps_q = (
        db.query(StorageSnapshot).filter(StorageSnapshot.location_id == location_id) if location_id else db.query(StorageSnapshot)
    )
    snaps = snaps_q.order_by(StorageSnapshot.taken_at).all()
    if not location_id:
        # Global view: latest snapshot per location summed by day is complex; use per-scan totals of the whole inventory.
        files = db.query(FileRecord).filter(FileRecord.status != "missing").all()
    else:
        files = db.query(FileRecord).filter(FileRecord.location_id == location_id, FileRecord.status != "missing").all()
    current_bytes = sum(f.size_bytes for f in files)
    now = now_utc()

    measured = [{"date": as_utc(s.taken_at).date().isoformat(), "bytes": s.total_bytes, "files": s.file_count} for s in snaps]
    span_days = (as_utc(snaps[-1].taken_at) - as_utc(snaps[0].taken_at)).days if len(snaps) >= 2 else 0

    # Reconstruction from modification times (cumulative bytes of current files by month).
    reconstructed = []
    dated = sorted((as_utc(f.modified_at), f.size_bytes) for f in files if f.modified_at)
    if dated:
        cumulative = 0
        by_month: dict[str, int] = {}
        for ts, size in dated:
            cumulative += size
            by_month[ts.strftime("%Y-%m")] = cumulative
        reconstructed = [{"month": m, "bytes": b} for m, b in sorted(by_month.items())][-24:]

    result = {
        "current_bytes": current_bytes,
        "current_files": len(files),
        "price_per_gb_month": price,
        "measured_history": measured,
        "reconstructed_history": reconstructed,
        "method": None,
        "projection": [],
        "scenarios": {},
        "assumptions": [],
    }
    if len(snaps) >= 3 and span_days >= 7:
        x = np.array([(as_utc(s.taken_at) - as_utc(snaps[0].taken_at)).total_seconds() / 86400 for s in snaps])
        y = np.array([s.total_bytes for s in snaps], dtype=float)
        slope, intercept = np.polyfit(x, y, 1)
        residual = y - (slope * x + intercept)
        sigma = float(residual.std(ddof=1)) if len(y) > 2 else 0.0
        last_x = x[-1]
        for m in range(1, months + 1):
            day = last_x + 30 * m
            value = max(slope * day + intercept, 0.0)
            result["projection"].append(
                {
                    "month": (now + timedelta(days=30 * m)).strftime("%Y-%m"),
                    "bytes": int(value),
                    "low": int(max(value - 2 * sigma, 0)),
                    "high": int(value + 2 * sigma),
                    "monthly_cost_usd": round(_monthly_cost(value, price), 4),
                }
            )
        result["method"] = "linear_regression_on_measured_snapshots"
        result["growth_bytes_per_day"] = round(float(slope), 2)
        result["assumptions"].append(
            f"Linear trend fitted to {len(snaps)} measured snapshots over {span_days} days; band = ±2σ of residuals."
        )
    else:
        result["method"] = "baseline_scenarios"
        result["assumptions"].append(
            f"Insufficient measured history ({len(snaps)} snapshots over {span_days} days; need ≥3 over ≥7 days). "
            "No statistical forecast is made; scenarios below are assumptions, not predictions."
        )
    for label, pct in (("flat", 0.0), (f"{scenario_growth_pct:g}%_monthly", scenario_growth_pct)):
        series = []
        for m in range(1, months + 1):
            value = current_bytes * (1 + pct / 100) ** m
            series.append(
                {
                    "month": (now + timedelta(days=30 * m)).strftime("%Y-%m"),
                    "bytes": int(value),
                    "monthly_cost_usd": round(_monthly_cost(value, price), 4),
                }
            )
        result["scenarios"][label] = series
    result["assumptions"].append(
        f"Costs use the S3 Standard list price ${price}/GB-month ({settings['pricing_region']}), approximate."
    )
    if reconstructed:
        result["assumptions"].append(
            "Reconstructed history uses file modification times of files that still exist; deleted or rewritten files are not visible."
        )
    return result
