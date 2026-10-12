"""Future-access prediction: features, chronological evaluation, persistence, inference.

Task: given everything known about a file at a cutoff time ``t``, predict
whether it will be accessed at least once in ``(t, t + horizon]``.

Leakage control
---------------
Training examples are snapshots taken at several cutoffs. Features only use
events strictly before the cutoff. The test snapshot is taken at the latest
cutoff, and every training cutoff satisfies ``cutoff + horizon <= test_cutoff``,
so no training label window overlaps the test period.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.ml.synthetic import CATEGORIES, SyntheticDataset

NUMERIC_FEATURES = [
    "days_since_last_access",
    "never_accessed",
    "accesses_7d",
    "accesses_30d",
    "accesses_prev_30_60d",
    "accesses_90d",
    "accesses_total",
    "access_rate_per_30d",
    "trend_30d",
    "days_since_creation",
    "log_size",
]
CATEGORICAL_FEATURES = ["category"]
MIN_HISTORY_EVENTS_FOR_OK = 3


def features_from_history(ago_days: np.ndarray, age_days: float, size_bytes: int, category: str) -> dict[str, Any]:
    """Build one feature row. ``ago_days`` = how many days before the cutoff each access happened (>0)."""
    ago = np.asarray(ago_days, dtype=float)
    age = max(float(age_days), 1.0)
    last = float(ago.min()) if ago.size else age
    a30 = int((ago <= 30).sum())
    prev = int(((ago > 30) & (ago <= 60)).sum())
    return {
        "days_since_last_access": min(last, age),
        "never_accessed": int(ago.size == 0),
        "accesses_7d": int((ago <= 7).sum()),
        "accesses_30d": a30,
        "accesses_prev_30_60d": prev,
        "accesses_90d": int((ago <= 90).sum()),
        "accesses_total": int(ago.size),
        "access_rate_per_30d": ago.size / (age / 30.0),
        "trend_30d": (a30 - prev) / (a30 + prev + 1),
        "days_since_creation": age,
        "log_size": float(np.log1p(max(size_bytes, 0))),
        "category": category if category in CATEGORIES else "other",
    }


def snapshot(ds: SyntheticDataset, cutoff: int, horizon: int) -> pd.DataFrame:
    """Feature/label table for all files existing at ``cutoff``."""
    rows = []
    grouped = {fid: np.sort(g.day.to_numpy()) for fid, g in ds.events.groupby("file_id")}
    for f in ds.files.itertuples(index=False):
        if f.created_day >= cutoff:
            continue
        days = grouped.get(f.file_id, np.array([], dtype=int))
        past = days[days < cutoff]
        future = days[(days >= cutoff) & (days < cutoff + horizon)]
        row = features_from_history(cutoff - past, cutoff - f.created_day, f.size_bytes, f.category)
        row["label"] = int(future.size > 0)
        row["cutoff"] = cutoff
        rows.append(row)
    return pd.DataFrame(rows)


def chronological_split(ds: SyntheticDataset, horizon: int, n_train_cutoffs: int = 6):
    test_cutoff = ds.history_days - horizon
    last_train = test_cutoff - horizon
    if last_train < 30:
        raise ValueError(
            f"History of {ds.history_days} days is too short for a {horizon}-day horizon "
            f"(need at least {2 * horizon + 30} days for a leakage-free chronological split)."
        )
    first_train = min(max(30, last_train - 30 * (n_train_cutoffs - 1)), last_train)
    train_cutoffs = sorted({int(c) for c in np.linspace(first_train, last_train, n_train_cutoffs)})
    train = pd.concat([snapshot(ds, c, horizon) for c in train_cutoffs], ignore_index=True)
    test = snapshot(ds, test_cutoff, horizon)
    return train, test, {"train_cutoffs": train_cutoffs, "test_cutoff": test_cutoff}


def _preprocessor() -> ColumnTransformer:
    return ColumnTransformer(
        [
            ("num", StandardScaler(), NUMERIC_FEATURES),
            ("cat", OneHotEncoder(handle_unknown="ignore", categories=[CATEGORIES]), CATEGORICAL_FEATURES),
        ]
    )


def build_models(seed: int) -> dict[str, Pipeline]:
    return {
        "logistic_regression": Pipeline(
            [("prep", _preprocessor()), ("clf", LogisticRegression(max_iter=2000, class_weight="balanced"))]
        ),
        "random_forest": Pipeline(
            [
                ("prep", _preprocessor()),
                (
                    "clf",
                    RandomForestClassifier(
                        n_estimators=200,
                        max_depth=10,
                        min_samples_leaf=5,
                        class_weight="balanced_subsample",
                        random_state=seed,
                        n_jobs=-1,
                    ),
                ),
            ]
        ),
    }


def _metrics(y_true: np.ndarray, proba: np.ndarray, threshold: float = 0.5) -> dict[str, float]:
    pred = (proba >= threshold).astype(int)
    both = len(np.unique(y_true)) > 1
    return {
        "roc_auc": round(float(roc_auc_score(y_true, proba)), 4) if both else None,
        "accuracy": round(float(accuracy_score(y_true, pred)), 4),
        "precision": round(float(precision_score(y_true, pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, pred, zero_division=0)), 4),
        "brier": round(float(brier_score_loss(y_true, np.clip(proba, 0, 1))), 4),
    }


def heuristic_scores(df: pd.DataFrame, horizon: int) -> np.ndarray:
    """Baseline: 'accessed within the last horizon days => will be accessed again'.

    Returned as a pseudo-probability so the same metrics can be computed.
    """
    recent = df["days_since_last_access"].to_numpy() <= horizon
    return np.where(recent & (df["never_accessed"].to_numpy() == 0), 0.9, 0.1)


@dataclass
class TrainingResult:
    metrics: dict[str, dict]
    selected_model: str
    pipeline: Pipeline
    params: dict


def train_and_evaluate(
    ds: SyntheticDataset | None = None, horizon: int = 30, seed: int = 42, train=None, test=None, split_info=None
) -> TrainingResult:
    if train is None:
        train, test, split_info = chronological_split(ds, horizon)
    if train["label"].nunique() < 2:
        raise ValueError("Training data contains a single class; cannot train a classifier.")
    X_train, y_train = train[NUMERIC_FEATURES + CATEGORICAL_FEATURES], train["label"].to_numpy()
    X_test, y_test = test[NUMERIC_FEATURES + CATEGORICAL_FEATURES], test["label"].to_numpy()
    metrics: dict[str, dict] = {
        "heuristic_baseline": _metrics(y_test, heuristic_scores(test, horizon)),
    }
    fitted: dict[str, Pipeline] = {}
    for name, model in build_models(seed).items():
        model.fit(X_train, y_train)
        fitted[name] = model
        metrics[name] = _metrics(y_test, model.predict_proba(X_test)[:, 1])
    selected = max(fitted, key=lambda n: metrics[n]["roc_auc"] or 0)
    params = {
        **(split_info or {}),
        "horizon_days": horizon,
        "seed": seed,
        "n_train": int(len(train)),
        "n_test": int(len(test)),
        "train_positive_rate": round(float(y_train.mean()), 4),
        "test_positive_rate": round(float(y_test.mean()), 4),
        "features": NUMERIC_FEATURES + CATEGORICAL_FEATURES,
    }
    if hasattr(fitted["random_forest"].named_steps["clf"], "feature_importances_"):
        names = fitted["random_forest"].named_steps["prep"].get_feature_names_out()
        importances = fitted["random_forest"].named_steps["clf"].feature_importances_
        params["rf_feature_importance"] = {
            n.split("__", 1)[1]: round(float(v), 4)
            for n, v in sorted(zip(names, importances, strict=True), key=lambda x: -x[1])[:10]
        }
    return TrainingResult(metrics=metrics, selected_model=selected, pipeline=fitted[selected], params=params)


def save_model(result: TrainingResult, directory: Path, evaluation_id: int, dataset_kind: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"access_model_{evaluation_id}.joblib"
    joblib.dump(
        {"pipeline": result.pipeline, "model_name": result.selected_model, "params": result.params, "dataset_kind": dataset_kind},
        path,
    )
    return path


def load_model(path: Path) -> dict:
    return joblib.load(path)


def predict_file(
    bundle: dict, access_times: list[datetime], created_at: datetime | None, size_bytes: int, category: str, now: datetime
) -> float:
    ago = np.array([max((now - t).total_seconds() / 86400.0, 0.0) for t in access_times])
    if created_at is not None:
        age = max((now - created_at).total_seconds() / 86400.0, 1.0)
    else:
        age = float(ago.max()) if ago.size else 1.0
    row = pd.DataFrame([features_from_history(ago, age, size_bytes, category)])
    return float(bundle["pipeline"].predict_proba(row[NUMERIC_FEATURES + CATEGORICAL_FEATURES])[0, 1])
