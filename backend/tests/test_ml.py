"""Access-prediction model: data generation, leakage-free split, metrics, inference honesty."""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest

from app.ml import access_model, synthetic


@pytest.fixture(scope="module")
def dataset():
    return synthetic.generate(n_files=500, seed=11)


def test_generator_is_reproducible(dataset):
    again = synthetic.generate(n_files=500, seed=11)
    pd.testing.assert_frame_equal(dataset.events, again.events)
    assert dataset.kind == "synthetic"
    assert not synthetic.generate(n_files=500, seed=12).events.equals(dataset.events)


def test_features_use_only_past_events():
    row = access_model.features_from_history(
        np.array([1.0, 5.0, 40.0, 100.0]), age_days=200, size_bytes=1000, category="document"
    )
    assert row["days_since_last_access"] == 1.0
    assert row["accesses_7d"] == 2 and row["accesses_30d"] == 2 and row["accesses_prev_30_60d"] == 1
    assert row["accesses_total"] == 4 and row["never_accessed"] == 0
    never = access_model.features_from_history(np.array([]), age_days=50, size_bytes=10, category="unknown")
    assert never["never_accessed"] == 1 and never["days_since_last_access"] == 50 and never["category"] == "other"


def test_snapshot_labels_match_future_window(dataset):
    snap = access_model.snapshot(dataset, cutoff=300, horizon=30)
    fid = int(dataset.files[dataset.files.created_day < 300].file_id.iloc[0])
    days = dataset.events[dataset.events.file_id == fid].day.to_numpy()
    expected = int(((days >= 300) & (days < 330)).any())
    row = snap.reset_index(drop=True)
    idx = list(dataset.files[dataset.files.created_day < 300].file_id).index(fid)
    assert row.loc[idx, "label"] == expected


def test_chronological_split_has_no_label_leakage(dataset):
    train, test, info = access_model.chronological_split(dataset, horizon=30)
    assert all(c + 30 <= info["test_cutoff"] for c in info["train_cutoffs"])
    assert set(test.cutoff) == {info["test_cutoff"]}
    assert train.cutoff.max() < info["test_cutoff"]


def test_short_history_is_rejected():
    tiny = synthetic.generate(n_files=50, history_days=70, seed=1)
    with pytest.raises(ValueError, match="too short"):
        access_model.chronological_split(tiny, horizon=30)


def test_models_beat_or_match_heuristic(dataset):
    result = access_model.train_and_evaluate(dataset, horizon=30, seed=11)
    assert set(result.metrics) == {"heuristic_baseline", "logistic_regression", "random_forest"}
    best_auc = result.metrics[result.selected_model]["roc_auc"]
    assert best_auc > 0.75
    assert best_auc >= result.metrics["heuristic_baseline"]["roc_auc"] - 0.02
    for metrics in result.metrics.values():
        assert 0 <= metrics["brier"] <= 1 and 0 <= metrics["f1"] <= 1


def test_training_endpoint_and_predictions_are_honest(client, demo):
    # No access events yet -> predictions must be "unavailable", never invented.
    client.post("/api/ml/train", json={"dataset": "synthetic", "n_files": 300, "horizon_days": 30})
    target = demo["ids"]["legal/contract_renewal_terms.pdf"]
    pred = client.post("/api/ml/predict", json={"file_ids": [target]}).json()[0]
    if pred["reliability"] == "unavailable":
        assert pred["probability"] is None and "No access history" in pred["reason"]
    # Synthetic demo events -> prediction exists but is explicitly low reliability.
    created = client.post("/api/ml/demo-events", json={"location_id": demo["location_id"]}).json()
    assert created["source"] == "synthetic_demo" and "SYNTHETIC" in created["warning"]
    preds = client.post("/api/ml/predict", json={}).json()
    with_prob = [p for p in preds if p["probability"] is not None]
    assert with_prob and all(p["reliability"] == "low" for p in with_prob)
    assert all("synthetic" in p["reason"] for p in with_prob)
    evaluations = client.get("/api/ml/evaluations").json()
    assert evaluations[0]["dataset_kind"] == "synthetic"
    assert evaluations[0]["params"]["test_cutoff"] > max(evaluations[0]["params"]["train_cutoffs"])


def test_observed_training_requires_enough_data(client, demo):
    response = client.post("/api/ml/train", json={"dataset": "observed"})
    assert response.status_code == 400 and "observed" in response.json()["detail"]


def test_import_access_log_csv(client, demo):
    csv_text = "path,timestamp\nprojects/project_alpha_brief.md,2026-09-01T10:00:00Z\nmissing.txt,2026-09-01T10:00:00Z\nprojects/project_alpha_brief.md,not-a-date\n"
    response = client.post("/api/ml/access-events/import", files={"file": ("log.csv", io.BytesIO(csv_text.encode()), "text/csv")})
    assert response.json() == {"imported": 1, "unmatched_paths": 1, "invalid_rows": 1}
    bad = client.post("/api/ml/access-events/import", files={"file": ("log.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")})
    assert bad.status_code == 400
    status = client.get("/api/ml/status").json()
    assert status["access_events_by_source"]["imported_log"] >= 1
