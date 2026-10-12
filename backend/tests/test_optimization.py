"""Consolidation, compression, cost engine, forecasting and plan orchestration."""

from __future__ import annotations

import bz2
import gzip
import hashlib
import io
import lzma
from datetime import timedelta
from pathlib import Path

import pytest
import zstandard

from app.core.timeutil import now_utc
from app.db.models import StorageSnapshot
from app.services import cost_engine
from tests.conftest import SAMPLE_DIR

# --- Consolidation -----------------------------------------------------------------------------


def test_consolidation_merges_and_keeps_unique_facts(client, demo):
    ids = demo["ids"]
    body = {
        "file_ids": [ids["projects/project_alpha_brief.md"], ids["projects/project_alpha_status_update.txt"]],
        "title": "Alpha",
    }
    result = client.post("/api/consolidations", json=body).json()
    md = result["draft_markdown"]
    assert md.count("$50,000") == 1  # repeated fact stated once
    assert "Northwind Traders" in md  # unique fact from the second source kept
    assert "2019 archive" in md  # unique statement from the first source kept
    assert "[S1] [S2]" in md  # attribution to both sources
    report = result["report"]
    assert report["units_merged"] > 0 and report["conflict_count"] == 0
    assert report["preservation"]["all_detected_tokens_present"] is True
    assert result["original_bytes"] == sum(
        (SAMPLE_DIR / p).stat().st_size for p in ("projects/project_alpha_brief.md", "projects/project_alpha_status_update.txt")
    )


def test_consolidation_surfaces_conflicts_instead_of_choosing(client, demo):
    ids = demo["ids"]
    body = {
        "file_ids": [ids["projects/project_alpha_brief.md"], ids["projects/project_alpha_finance_note.md"]],
        "title": "Alpha budget",
    }
    result = client.post("/api/consolidations", json=body).json()
    assert "$50,000" in result["draft_markdown"] and "$65,000" in result["draft_markdown"]
    assert "Conflicts requiring review" in result["draft_markdown"]
    assert any(c.get("field") == "Budget" for c in result["report"]["conflicts"])


def test_consolidation_edit_revalidates_and_exports(client, demo):
    ids = demo["ids"]
    created = client.post(
        "/api/consolidations",
        json={"file_ids": [ids["projects/project_alpha_brief.md"], ids["projects/project_alpha_status_update.txt"]]},
    ).json()
    edited = client.put(f"/api/consolidations/{created['id']}", json={"markdown": "# Alpha\nBudget: $50,000\n"}).json()
    assert edited["status"] == "reviewed"
    assert edited["report"]["preservation"]["tokens_missing"] > 0  # removing facts is detected
    exported = client.post(f"/api/consolidations/{created['id']}/export", json={"format": "txt"}).json()
    path = Path(exported["export_path"])
    assert path.exists() and exported["output_bytes"] == path.stat().st_size
    download = client.get(f"/api/consolidations/{created['id']}/download")
    assert download.status_code == 200 and "Budget" in download.text
    assert client.post(f"/api/consolidations/{created['id']}/export", json={"format": "exe"}).status_code == 422


def test_consolidation_validation(client, demo):
    ids = demo["ids"]
    assert client.post("/api/consolidations", json={"file_ids": [ids["projects/project_alpha_brief.md"]]}).status_code == 422
    assert (
        client.post("/api/consolidations", json={"file_ids": [ids["projects/project_alpha_brief.md"], 999999]}).status_code == 404
    )
    no_text = client.post(
        "/api/consolidations", json={"file_ids": [ids["projects/project_alpha_brief.md"], ids["images/site_photo.jpg"]]}
    )
    assert no_text.status_code == 400


# --- Compression ------------------------------------------------------------------------------------


def test_lossless_compression_measured_and_verified(client, demo):
    results = client.post(f"/api/compression/files/{demo['ids']['data/sales_2025.csv']}").json()
    original = (SAMPLE_DIR / "data/sales_2025.csv").read_bytes()
    measured = [r for r in results if r["status"] == "measured"]
    assert {r["method"] for r in measured} >= {"gzip-9", "zstd-19", "bzip2-9", "xz-6", "zip-deflate-9"}
    best = next(r for r in results if r["is_best"])
    assert best["lossless"] and best["percent_saved"] > 70
    assert best["original_bytes"] == len(original)
    assert best["quality_metric"].get("round_trip_verified") is True
    # Independently confirm the reported sizes are real.
    sizes = {
        "gzip-9": len(gzip.compress(original, 9, mtime=0)),
        "bzip2-9": len(bz2.compress(original, 9)),
        "xz-6": len(lzma.compress(original, preset=6)),
        "zstd-19": len(zstandard.ZstdCompressor(level=19).compress(original)),
    }
    for r in measured:
        if r["method"] in sizes:
            assert r["output_bytes"] == sizes[r["method"]]


def test_image_compression_labels_lossy(client, demo):
    png = client.post(f"/api/compression/files/{demo['ids']['images/architecture_diagram.png']}").json()
    lossless = next(r for r in png if r["method"] == "webp-lossless")
    assert lossless["lossless"] and lossless["quality_metric"]["pixel_identical"] is True
    jpg = client.post(f"/api/compression/files/{demo['ids']['images/site_photo.jpg']}").json()
    assert all(not r["lossless"] for r in jpg)
    assert all(r["quality_metric"]["psnr_db"] > 20 for r in jpg if r["status"] == "measured")


def test_already_compressed_and_video_not_benchmarked(client, demo):
    zipped = client.post(f"/api/compression/files/{demo['ids']['archives/old_assets_2021.zip']}").json()
    assert zipped[0]["status"] == "skipped" and not zipped[0]["is_best"]
    video = client.post(f"/api/compression/files/{demo['ids']['media/demo_clip_metadata_only.mp4']}").json()
    # Metadata-only container: an estimate without ffmpeg, a decode error with it. Never a fake measurement.
    assert video[0]["output_bytes"] is None and not video[0]["is_best"]
    assert video[0]["status"] in {"recommended", "error"}


def test_compression_never_touches_original(client, demo):
    path = SAMPLE_DIR / "data/inventory.json"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    client.post(f"/api/compression/files/{demo['ids']['data/inventory.json']}")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


# --- Cost engine -----------------------------------------------------------------------------------------

PRICES = cost_engine.region_prices("us-east-1")
MB = 1024 * 1024


def _rec(size, reads, strategy, reliability="ok", probability=0.01, current="STANDARD", months=12):
    return cost_engine.recommend(size, current, reads, reliability, probability, cost_engine.STRATEGIES[strategy], PRICES, months)


def test_cold_large_object_goes_to_archive_only_when_aggressive():
    assert _rec(500 * MB, 0.0, "aggressive")["recommended_class"] == "DEEP_ARCHIVE"
    assert _rec(500 * MB, 0.0, "balanced")["recommended_class"] == "GLACIER_IR"
    assert _rec(500 * MB, 0.0, "conservative")["recommended_class"] in {"STANDARD_IA", "INTELLIGENT_TIERING"}


def test_hot_object_stays_in_standard():
    rec = _rec(50 * MB, 30.0, "aggressive", probability=0.99)
    assert rec["recommended_class"] == "STANDARD" and rec["saving"] == 0


def test_retrieval_costs_can_outweigh_storage_savings():
    cold_storage = cost_engine.class_cost("GLACIER_IR", PRICES, 100 * MB, 20.0, 12, transition=True)
    standard = cost_engine.class_cost("STANDARD", PRICES, 100 * MB, 20.0, 12, transition=False)
    assert cold_storage["storage"] < standard["storage"]
    assert cold_storage["total"] > standard["total"]


def test_minimum_billable_size_and_duration():
    small = cost_engine.class_cost("STANDARD_IA", PRICES, 10 * 1024, 0, 12, transition=False)
    assert small["storage"] == pytest.approx(128 * 1024 / 1024**3 * 0.0125 * 12)
    short = cost_engine.class_cost("DEEP_ARCHIVE", PRICES, 100 * MB, 0, 1, transition=False)
    assert short["billed_months"] == 6  # 180-day minimum
    assert _rec(20 * 1024, 0.0, "aggressive")["recommended_class"] == "STANDARD"


def test_unknown_access_only_considers_intelligent_tiering():
    rec = _rec(500 * MB, None, "aggressive", reliability="unavailable", probability=None)
    assert rec["recommended_class"] in {"STANDARD", "INTELLIGENT_TIERING"}
    assert any("unknown" in r for r in rec["risks"])


def test_conservative_ignores_low_reliability_predictions():
    rec = _rec(500 * MB, 0.0, "conservative", reliability="low")
    assert rec["recommended_class"] in {"STANDARD", "INTELLIGENT_TIERING"}


def test_simulation_and_what_if_endpoints(client, demo):
    sim = client.post("/api/costs/simulate", json={"strategy": "balanced", "location_id": demo["location_id"]}).json()
    assert len(sim["rows"]) == len(demo["ids"]) and sim["savings_kind"] == "estimated"
    assert all(r["simulated_current_class"] for r in sim["rows"])
    assert "approximate" in sim["assumptions"]["pricing_note"].lower() or "APPROXIMATE" in sim["assumptions"]["pricing_note"]
    whatif = client.post(
        "/api/costs/what-if",
        json={"object_size_mb": 100, "object_count": 1000, "reads_per_object_per_month": 0, "strategy": "aggressive"},
    ).json()
    assert whatif["recommendation"]["class"] == "DEEP_ARCHIVE" and whatif["recommendation"]["saving"] > 0
    compare = client.post("/api/costs/compare", json={}).json()
    assert set(compare) == {"conservative", "balanced", "aggressive"}
    assert client.post("/api/costs/simulate", json={"strategy": "yolo"}).status_code == 422


# --- Forecasting --------------------------------------------------------------------------------------


def test_forecast_baseline_then_regression(client, demo, db):
    loc = demo["location_id"]
    baseline = client.get("/api/forecast", params={"location_id": loc}).json()
    assert baseline["method"] == "baseline_scenarios" and baseline["projection"] == []
    assert "flat" in baseline["scenarios"] and baseline["reconstructed_history"]
    now = now_utc()
    for days_ago, size in ((60, 3_000_000), (30, 3_500_000), (10, 3_900_000)):
        db.add(StorageSnapshot(location_id=loc, taken_at=now - timedelta(days=days_ago), total_bytes=size, file_count=20))
    db.commit()
    fitted = client.get("/api/forecast", params={"location_id": loc, "months": 6}).json()
    assert fitted["method"] == "linear_regression_on_measured_snapshots"
    assert len(fitted["projection"]) == 6 and fitted["growth_bytes_per_day"] > 0
    assert fitted["projection"][-1]["bytes"] > fitted["projection"][0]["bytes"]


# --- Plans --------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def plan(client, demo):
    response = client.post("/api/plans", json={"strategy": "balanced", "location_id": demo["location_id"]})
    assert response.status_code == 201, response.text
    return response.json()


def test_plan_contains_explained_prioritised_actions(plan):
    types = {a["action_type"] for a in plan["actions"]}
    assert {"review_duplicate_group", "create_compressed_copy", "create_consolidation_draft", "storage_class_simulation"} <= types
    for action in plan["actions"]:
        assert action["reason"] and action["risk_level"] in {"low", "medium", "high"} and action["reversibility_note"] is not None
        assert action["status"] == "proposed"
    savings = [a["expected_monthly_savings_usd"] for a in plan["actions"]]
    assert savings == sorted(savings, reverse=True)
    assert plan["summary"]["savings"]["verified_duplicate_bytes"] > 0


def test_consolidation_group_excludes_exact_copies(plan, demo):
    for action in plan["actions"]:
        if action["action_type"] == "create_consolidation_draft":
            assert not {demo["ids"]["backup/alpha_brief_old.md"], demo["ids"]["projects/project_alpha_brief.md"]} <= set(
                action["target_file_ids"]
            )


def test_unapproved_and_manual_actions_cannot_execute(client, plan):
    pid = plan["id"]
    compress = next(a for a in plan["actions"] if a["action_type"] == "create_compressed_copy")
    assert client.post(f"/api/plans/{pid}/actions/{compress['id']}/execute", json={"dry_run": False}).status_code == 409
    manual = next(a for a in plan["actions"] if a["action_type"] == "review_duplicate_group")
    decided = client.post(f"/api/plans/{pid}/actions/{manual['id']}/decision", json={"approve": True}).json()
    assert decided["status"] == "acknowledged"
    assert client.post(f"/api/plans/{pid}/actions/{manual['id']}/execute", json={"dry_run": False}).status_code == 409


def test_lossy_action_requires_risk_acknowledgement(client, plan):
    lossy = [a for a in plan["actions"] if a["action_type"] == "create_compressed_copy" and not a["evidence"]["lossless"]]
    if not lossy:
        pytest.skip("no lossy recommendation in this dataset")
    url = f"/api/plans/{plan['id']}/actions/{lossy[0]['id']}/decision"
    assert client.post(url, json={"approve": True}).status_code == 409
    assert client.post(url, json={"approve": True, "acknowledge_risks": True}).json()["status"] == "approved"


def test_approved_compression_executes_dry_run_then_for_real(client, plan, demo):
    pid = plan["id"]
    action = next(a for a in plan["actions"] if a["action_type"] == "create_compressed_copy" and a["evidence"]["lossless"])
    target_path = next(f["rel_path"] for f in demo["files"] if f["id"] == action["target_file_ids"][0])
    before = hashlib.sha256((SAMPLE_DIR / target_path).read_bytes()).hexdigest()
    client.post(f"/api/plans/{pid}/actions/{action['id']}/decision", json={"approve": True})
    dry = client.post(f"/api/plans/{pid}/actions/{action['id']}/execute", json={"dry_run": True}).json()
    assert dry["status"] == "approved" and dry["result"]["dry_run"] is True
    done = client.post(f"/api/plans/{pid}/actions/{action['id']}/execute", json={"dry_run": False}).json()
    assert done["status"] == "executed"
    output = Path(done["result"]["output_path"])
    assert output.exists() and output.stat().st_size == done["result"]["measured_output_bytes"]
    assert hashlib.sha256((SAMPLE_DIR / target_path).read_bytes()).hexdigest() == before
    # Output decompresses back to the original bytes.
    data = output.read_bytes()
    decoders = {
        ".gz": gzip.decompress,
        ".bz2": bz2.decompress,
        ".xz": lzma.decompress,
        ".zst": lambda b: zstandard.ZstdDecompressor().decompress(b),
    }
    if output.suffix in decoders:
        assert decoders[output.suffix](data) == (SAMPLE_DIR / target_path).read_bytes()
    elif output.suffix == ".webp":
        from PIL import Image

        assert Image.open(io.BytesIO(data)).size == Image.open(SAMPLE_DIR / target_path).size
    assert client.post(f"/api/plans/{pid}/actions/{action['id']}/execute", json={"dry_run": False}).status_code == 409


def test_consolidation_action_creates_draft(client, plan):
    action = next(a for a in plan["actions"] if a["action_type"] == "create_consolidation_draft")
    client.post(f"/api/plans/{plan['id']}/actions/{action['id']}/decision", json={"approve": True})
    executed = client.post(f"/api/plans/{plan['id']}/execute", json={"dry_run": False, "action_ids": [action["id"]]}).json()
    done = next(a for a in executed["actions"] if a["id"] == action["id"])
    assert done["status"] == "executed" and done["result"]["consolidation_id"]
    assert executed["status"] == "partially_executed"
    history = client.get("/api/operations", params={"operation": "execute_create_consolidation_draft"}).json()
    assert history["total"] >= 1


def test_plan_requires_inventory(client):
    assert client.post("/api/plans", json={"strategy": "balanced", "location_id": 999999}).status_code == 400
