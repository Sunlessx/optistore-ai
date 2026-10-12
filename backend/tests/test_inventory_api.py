"""Scanning, inventory, duplicates, similarity, comparison, search and security via the API."""

from __future__ import annotations

import hashlib

from app.core.config import get_settings
from tests.conftest import SAMPLE_DIR


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"


def test_scan_inventories_every_file(client, demo):
    on_disk = sorted(p.relative_to(SAMPLE_DIR).as_posix() for p in SAMPLE_DIR.rglob("*") if p.is_file())
    assert sorted(demo["ids"]) == on_disk
    by_path = {f["rel_path"]: f for f in demo["files"]}
    brief = by_path["projects/project_alpha_brief.md"]
    expected = hashlib.sha256((SAMPLE_DIR / "projects/project_alpha_brief.md").read_bytes()).hexdigest()
    assert brief["sha256"] == expected
    assert brief["category"] == "document" and brief["text_chars"] > 100
    assert brief["accessed_at_reliable"] is False  # filesystem atime is never trusted


def test_malformed_file_reported_not_fatal(client, demo):
    broken = {f["rel_path"]: f for f in demo["files"]}["broken/corrupted_scan.pdf"]
    assert broken["status"] == "error" and "signature" in broken["error"]


def test_source_files_unchanged_by_scan(demo):
    for path in SAMPLE_DIR.rglob("*"):
        if path.is_file():
            rel = path.relative_to(SAMPLE_DIR).as_posix()
            record = next(f for f in demo["files"] if f["rel_path"] == rel)
            if record["sha256"]:
                assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]


def test_file_pagination_filter_sort(client, demo):
    page = client.get("/api/files", params={"page_size": 5, "page": 1, "sort": "size", "order": "desc"}).json()
    assert len(page["items"]) == 5 and page["total"] >= len(demo["ids"])
    sizes = [f["size_bytes"] for f in page["items"]]
    assert sizes == sorted(sizes, reverse=True)
    images = client.get("/api/files", params={"category": "image"}).json()
    assert images["total"] == 3
    assert client.get("/api/files", params={"page_size": 10_000}).status_code == 422


def test_file_detail_and_stats(client, demo):
    fid = demo["ids"]["images/site_photo.jpg"]
    detail = client.get(f"/api/files/{fid}").json()
    assert detail["metadata_json"]["width"] == 1400
    assert client.get("/api/files/999999").status_code == 404
    stats = client.get("/api/files/stats", params={"location_id": demo["location_id"]}).json()
    assert stats["total_files"] == len(demo["ids"])
    assert stats["by_category"]["image"]["files"] == 3


def test_exact_duplicates(client, demo):
    groups = client.get("/api/duplicates", params={"location_id": demo["location_id"]}).json()
    path_sets = sorted(sorted(f["rel_path"] for f in g["files"]) for g in groups)
    assert path_sets == [
        ["backup/Q3 report (copy).pdf", "reports/quarterly_report_q3_2025.pdf"],
        ["backup/alpha_brief_old.md", "projects/project_alpha_brief.md"],
        ["backup/notes_oct_backup.md", "meetings/meeting_notes_2025-10-14_october.md"],
    ]
    for group in groups:
        assert group["redundant_bytes"] == group["size_bytes"] * (group["file_count"] - 1)
        assert group["suggested_keep_id"] in {f["id"] for f in group["files"]}
    summary = client.get("/api/duplicates/summary").json()
    assert summary["redundant_copies"] >= 3


def test_semantic_pairs_exclude_exact_duplicates(client, demo):
    pairs = client.get("/api/similarity", params={"min_score": 0.5}).json()
    assert pairs, "expected related Project Alpha documents"
    for pair in pairs:
        assert pair["file_a"]["sha256"] != pair["file_b"]["sha256"]
    names = {frozenset((p["file_a"]["name"], p["file_b"]["name"])) for p in pairs}
    assert frozenset(("project_alpha_brief.md", "project_alpha_status_update.txt")) in names
    assert not any("banana_bread_recipe.md" in n for n in names)


def test_compare_detects_conflict_and_unique_content(client, demo):
    a = demo["ids"]["projects/project_alpha_brief.md"]
    b = demo["ids"]["projects/project_alpha_finance_note.md"]
    result = client.get("/api/compare", params={"a": a, "b": b}).json()
    budget = [c for c in result["conflicts"] if c["kind"] == "field_value" and c["field"] == "Budget"]
    assert budget and budget[0]["value_a"] == "$50,000" and budget[0]["value_b"] == "$65,000"
    assert any("2019 archive" in u["text"] for u in result["unique_a"])
    assert any("CFO" in u["text"] for u in result["unique_b"])
    assert "does not prove" in result["disclaimer"]
    assert client.get("/api/compare", params={"a": a, "b": a}).status_code == 400


def test_semantic_search(client, demo):
    expectations = {
        "Find the contract renewal conditions": "contract_renewal_terms.pdf",
        "Show me documents related to customer complaints": "customer_complaints_q3.docx",
        "Locate the October meeting notes": "meeting_notes_2025-10-14_october.md",
    }
    for query, expected in expectations.items():
        results = client.get("/api/search", params={"q": query}).json()["results"]
        top_names = [r["name"] for r in results[:2]]
        assert expected in top_names, (query, top_names)
        assert results[0]["excerpt"]
    assert client.get("/api/search", params={"q": "x"}).status_code == 422


def test_reindex(client, demo):
    body = client.post("/api/search/reindex").json()
    assert body["indexed_files"] == 0  # unchanged content is not re-embedded
    assert client.post("/api/search/reindex", params={"force": True}).json()["indexed_files"] > 5


def test_location_validation(client, demo):
    assert client.post("/api/locations", json={"kind": "local", "path": "C:/Windows"}).status_code == 403
    assert client.post("/api/locations", json={"kind": "local", "path": str(SAMPLE_DIR / ".." / "backend")}).status_code == 403
    assert client.post("/api/locations", json={"kind": "local", "path": str(SAMPLE_DIR)}).status_code == 409
    assert client.post("/api/locations", json={"kind": "local"}).status_code == 422
    assert client.post("/api/locations", json={"kind": "s3", "bucket": "Bad_Bucket!"}).status_code == 422


def test_browse_only_inside_roots(client):
    roots = client.get("/api/locations/browse").json()
    assert roots["directories"]
    inside = client.get("/api/locations/browse", params={"path": str(SAMPLE_DIR)}).json()
    assert "projects" in {d["name"] for d in inside["directories"]}
    assert client.get("/api/locations/browse", params={"path": "C:/"}).status_code == 403


def test_scan_cancellation_and_conflict(client, scan_root):
    folder = scan_root / "cancel_me"
    folder.mkdir(exist_ok=True)
    for i in range(30):
        (folder / f"f{i}.txt").write_text(f"file {i} " * 30, encoding="utf-8")
    loc = client.post("/api/locations", json={"kind": "local", "path": str(folder)}).json()
    from app.db.models import ScanJob
    from app.db.session import SessionLocal
    from app.services import scanner

    with SessionLocal() as session:
        job = ScanJob(location_id=loc["id"], cancel_requested=True)
        session.add(job)
        session.commit()
        job_id = job.id
    scanner.run_scan(job_id)
    assert client.get(f"/api/scans/{job_id}").json()["status"] == "cancelled"
    assert client.post(f"/api/scans/{job_id}/cancel").status_code == 409
    client.delete(f"/api/locations/{loc['id']}")


def test_api_key_enforced_when_configured(client):
    settings = get_settings()
    settings.api_key = "s3cret"
    try:
        assert client.get("/api/files").status_code == 401
        assert client.get("/api/files", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get("/api/files", headers={"X-API-Key": "s3cret"}).status_code == 200
        assert client.get("/api/health").status_code == 200
    finally:
        settings.api_key = None


def test_security_headers_and_cors(client):
    response = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    evil = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in evil.headers


def test_settings_validation(client):
    assert client.put("/api/settings", json={"values": {"similarity_threshold": 5}}).status_code == 400
    assert client.put("/api/settings", json={"values": {"unknown": 1}}).status_code == 400
    assert client.put("/api/settings", json={"values": {"pricing_region": "mars-1"}}).status_code == 400
    ok = client.put("/api/settings", json={"values": {"cost_horizon_months": 12}})
    assert ok.status_code == 200
    assert client.get("/api/settings").json()["deployment"]["s3_write_enabled"] is False


def test_operations_log_records_scan(client, demo):
    ops = client.get("/api/operations", params={"operation": "scan"}).json()
    assert ops["total"] >= 1 and ops["items"][0]["status"] in {"success", "info"}


def test_dashboard_uses_real_data(client, demo):
    dash = client.get("/api/dashboard").json()
    assert dash["has_data"] is True
    assert dash["total_files"] >= len(demo["ids"])
    assert dash["duplicates"]["redundant_bytes"] > 0
