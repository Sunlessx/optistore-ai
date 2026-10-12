"""User-adjustable analysis settings persisted in the database.

These differ from :mod:`app.core.config` (deployment configuration) in that
they can be changed at runtime from the Settings page.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AppSetting

DEFAULTS: dict[str, Any] = {
    # Document-level cosine similarity above which two documents are shown as candidates.
    "similarity_threshold": 0.75,
    # Chunk-level similarity used to compute section coverage.
    "chunk_match_threshold": 0.80,
    # Sentence-level similarity above which two sentences are considered "shared".
    "sentence_match_threshold": 0.85,
    "min_document_chars": 80,
    "default_strategy": "balanced",
    "cost_horizon_months": 12,
    "prediction_horizon_days": 30,
    "pricing_region": "us-east-1",
    # Expected number of reads per month when an object is predicted "accessed".
    "reads_per_accessed_month": 2.0,
    "compression_min_saving_percent": 5.0,
}

BOUNDS: dict[str, tuple[float, float]] = {
    "similarity_threshold": (0.3, 0.99),
    "chunk_match_threshold": (0.3, 0.99),
    "sentence_match_threshold": (0.3, 0.99),
    "min_document_chars": (0, 10_000),
    "cost_horizon_months": (1, 60),
    "prediction_horizon_days": (7, 180),
    "reads_per_accessed_month": (0.1, 1000),
    "compression_min_saving_percent": (0, 90),
}

STRATEGIES = ("conservative", "balanced", "aggressive")


def get_all(db: Session) -> dict[str, Any]:
    values = dict(DEFAULTS)
    for row in db.query(AppSetting).all():
        if row.key in DEFAULTS:
            values[row.key] = row.value.get("v")
    return values


def get(db: Session, key: str) -> Any:
    return get_all(db)[key]


def validate(updates: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in updates.items():
        if key not in DEFAULTS:
            raise ValueError(f"Unknown setting '{key}'.")
        if key in BOUNDS:
            lo, hi = BOUNDS[key]
            try:
                num = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"'{key}' must be a number.") from exc
            if not lo <= num <= hi:
                raise ValueError(f"'{key}' must be between {lo} and {hi}.")
            value = int(num) if isinstance(DEFAULTS[key], int) else num
        elif key == "default_strategy" and value not in STRATEGIES:
            raise ValueError(f"'default_strategy' must be one of {STRATEGIES}.")
        elif key == "pricing_region" and not isinstance(value, str):
            raise ValueError("'pricing_region' must be a string.")
        clean[key] = value
    return clean


def update(db: Session, updates: dict[str, Any]) -> dict[str, Any]:
    clean = validate(updates)
    for key, value in clean.items():
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value={"v": value}))
        else:
            row.value = {"v": value}
    db.commit()
    return get_all(db)
