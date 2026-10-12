"""OCR, ffmpeg video, FAISS vector index and S3 access-log ingestion."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import boto3
import numpy as np
import pytest
from moto import mock_aws

from app.core.config import get_settings
from app.services import access_logs, tools
from app.services.vector_index import VectorIndex
from tests.conftest import SAMPLE_DIR

needs_tesseract = pytest.mark.skipif(tools.tesseract_path() is None, reason="Tesseract not installed")
needs_ffmpeg = pytest.mark.skipif(tools.ffmpeg_path() is None or tools.ffprobe_path() is None, reason="ffmpeg not installed")
REAL_VIDEO = "media/site_walkthrough.mp4"


def test_health_reports_tools_and_vector_backend(client):
    body = client.get("/api/health").json()
    assert set(body["tools"]) == {"tesseract", "ffmpeg", "ffprobe"}
    assert body["vector_backend"] in {"faiss", "numpy"}


# --- OCR --------------------------------------------------------------------------------------------


@needs_tesseract
def test_scanned_pdf_is_read_with_ocr(client, demo):
    detail = client.get(f"/api/files/{demo['ids']['legal/scanned_nda_addendum.pdf']}").json()
    assert detail["metadata_json"].get("ocr") is True
    assert "Casablanca" in detail["text_preview"] and "three years" in detail["text_preview"]
    results = client.get("/api/search", params={"q": "non-disclosure confidentiality period"}).json()["results"]
    assert "scanned_nda_addendum.pdf" in [r["name"] for r in results[:2]]


def test_scanned_pdf_without_ocr_reports_warning(monkeypatch):
    from app.services import extractors

    monkeypatch.setattr(extractors, "_tesseract", lambda: None)
    result = extractors.extract_text_from_bytes(".pdf", (SAMPLE_DIR / "legal/scanned_nda_addendum.pdf").read_bytes(), 10_000)
    assert not (result.text or "").strip()
    assert any("OCR is unavailable" in w for w in result.warnings)


# --- Video --------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_ffprobe_metadata(client, demo):
    meta = client.get(f"/api/files/{demo['ids'][REAL_VIDEO]}").json()["metadata_json"]
    assert meta["probe"] == "ffprobe" and meta["width"] == 1280 and meta["height"] == 720
    assert meta["video_codec"] == "h264" and meta["duration_seconds"] == pytest.approx(6.0, abs=0.1)


@needs_ffmpeg
def test_video_transcode_is_measured_and_lossy(client, demo, db):
    results = client.post(f"/api/compression/files/{demo['ids'][REAL_VIDEO]}").json()
    measured = results[0]
    assert measured["status"] == "measured" and measured["lossless"] is False
    assert measured["output_bytes"] < measured["original_bytes"]
    assert measured["quality_metric"]["psnr_db"] > 30
    assert measured["is_best"] is True

    from app.db.models import CompressionResult
    from app.services import compression

    row = db.get(CompressionResult, measured["id"])
    path = compression.write_output_copy(db, row)
    assert path.exists() and path.suffix == ".mp4" and get_settings().outputs_dir in path.parents
    assert (SAMPLE_DIR / REAL_VIDEO).stat().st_size == measured["original_bytes"]  # original untouched


@needs_ffmpeg
def test_undecodable_video_reports_error(client, demo):
    results = client.post(f"/api/compression/files/{demo['ids']['media/demo_clip_metadata_only.mp4']}").json()
    assert results[0]["status"] == "error" and not results[0]["is_best"]


def test_video_estimate_when_ffmpeg_missing(client, demo, monkeypatch):
    monkeypatch.setattr(tools, "ffmpeg_path", lambda: None)
    results = client.post(f"/api/compression/files/{demo['ids']['media/demo_clip_metadata_only.mp4']}").json()
    assert results[0]["status"] == "recommended" and "Install ffmpeg" in results[0]["quality_note"]


# --- Vector index ------------------------------------------------------------------------------------------


def test_faiss_and_numpy_backends_agree(monkeypatch):
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(300, 32)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    queries = vectors[:5]
    settings = get_settings()
    original = settings.vector_backend
    try:
        settings.vector_backend = "numpy"
        np_index = VectorIndex(vectors)
        settings.vector_backend = "auto"
        auto_index = VectorIndex(vectors)
    finally:
        settings.vector_backend = original
    s1, i1 = np_index.search(queries, 10)
    s2, i2 = auto_index.search(queries, 10)
    assert np_index.backend == "numpy"
    assert (i1[:, 0] == np.arange(5)).all()  # each vector is its own nearest neighbour
    np.testing.assert_array_equal(i1, i2)
    np.testing.assert_allclose(s1, s2, atol=1e-5)
    np.testing.assert_allclose(auto_index.scores_all(queries[0]), vectors @ queries[0], atol=1e-5)


def test_empty_index():
    index = VectorIndex(np.zeros((0, 8), np.float32))
    scores, idx = index.search(np.ones(8, np.float32), 5)
    assert scores.shape == (1, 0) and idx.shape == (1, 0)


# --- S3 server access logs ---------------------------------------------------------------------------------

BUCKET = "optistore-logged-bucket"


def _log(key: str, op: str = "REST.GET.OBJECT", status: str = "200", when: str = "06/Oct/2026:10:15:00 +0000") -> str:
    return (
        f"79a59df900b949e55d96a1e698fbaced {BUCKET} [{when}] 192.0.2.3 "
        f"arn:aws:iam::123456789012:user/alice 3E57427F3EXAMPLE {op} {key} "
        f'"GET /{BUCKET}/{key} HTTP/1.1" {status} - 2662992 3462992 70 10 "-" "S3Console/0.4" - '
        "s9lzHYrFp76ZVxRcpX9+5cjAnEH2ROuNkd2BHfIa6UkFVdtjf5mKR3/eTPFvsiP/XV/VLi31234= SigV4 "
        "ECDHE-RSA-AES128-GCM-SHA256 AuthHeader awsexamplebucket1.s3.us-west-1.amazonaws.com TLSV1.2 - -"
    )


def test_parse_access_log_lines():
    read = access_logs.parse_line(_log("reports/Q3%20report.pdf"))
    assert read.bucket == BUCKET and read.key == "reports/Q3 report.pdf"
    assert read.occurred_at == datetime(2026, 10, 6, 10, 15, tzinfo=UTC)
    assert access_logs.parse_line(_log("reports/a.pdf", op="REST.PUT.OBJECT")) is None
    assert access_logs.parse_line(_log("reports/a.pdf", status="404")) is None
    assert access_logs.parse_line("garbage line") is None


@pytest.fixture
def logged_bucket(client):
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        s3.create_bucket(Bucket="optistore-logs")
        for key in ("reports/q3.pdf", "reports/q4.pdf"):
            s3.put_object(Bucket=BUCKET, Key=key, Body=f"%PDF-1.4 {key}".encode())
        loc = client.post("/api/locations", json={"kind": "s3", "bucket": BUCKET}).json()
        job = client.post("/api/scans", json={"location_id": loc["id"]}).json()
        assert client.get(f"/api/scans/{job['id']}").json()["status"] == "completed"
        yield s3
        client.delete(f"/api/locations/{loc['id']}")


def test_import_access_log_upload_dedupes(client, logged_bucket):
    text = "\n".join(
        [
            _log("reports/q3.pdf"),
            _log("reports/q3.pdf", when="07/Oct/2026:09:00:00 +0000"),
            _log("reports/q4.pdf", op="REST.HEAD.OBJECT"),
            _log("reports/unknown.pdf"),
        ]
    )
    files = {"file": ("access.log", text.encode(), "text/plain")}
    first = client.post("/api/ml/access-events/import-s3-log", files=files).json()
    assert first == {"imported": 2, "unmatched_objects": 1, "duplicates_skipped": 0, "non_read_or_invalid_lines": 1}
    again = client.post("/api/ml/access-events/import-s3-log", files=files).json()
    assert again["imported"] == 0 and again["duplicates_skipped"] == 2
    assert client.get("/api/ml/status").json()["access_events_by_source"]["s3_access_log"] >= 2


def test_fetch_access_logs_from_log_bucket(client, logged_bucket):
    logged_bucket.put_object(
        Bucket="optistore-logs",
        Key="logs/2026-10-08-00-00-00-AAAA",
        Body=_log("reports/q4.pdf", when="08/Oct/2026:12:00:00 +0000").encode(),
    )
    result = client.post("/api/ml/access-events/fetch-s3-logs", json={"log_bucket": "optistore-logs", "prefix": "logs/"}).json()
    assert result["log_objects_read"] == 1 and result["imported"] == 1
    ops = client.get("/api/operations", params={"operation": "fetch_s3_access_logs"}).json()
    assert ops["total"] >= 1


def test_observed_predictions_marked_reliable_only_with_observed_model(client, logged_bucket, db):
    from app.db.models import AccessPrediction, FileRecord

    files = db.query(FileRecord).filter(FileRecord.rel_path.in_(["reports/q3.pdf"])).all()
    client.post("/api/ml/train", json={"dataset": "synthetic", "n_files": 300})
    client.post("/api/ml/predict", json={"file_ids": [files[0].id]})
    pred = db.query(AccessPrediction).filter(AccessPrediction.file_id == files[0].id).one()
    if pred.probability is not None:
        assert pred.reliability == "low" and "synthetic" in pred.reason  # synthetic model => never "ok"
    assert Path(get_settings().models_dir).exists()
