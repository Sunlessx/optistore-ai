"""AWS S3 integration against moto's in-memory S3 (no real AWS account needed)."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from app.core.config import get_settings
from app.services import s3_service

BUCKET = "optistore-test-bucket"


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_object(
            Bucket=BUCKET,
            Key="docs/s3_meeting.md",
            Body=b"# S3 notes\nThe S3 bucket stores quarterly archive exports for auditors.\n" * 5,
        )
        client.put_object(Bucket=BUCKET, Key="docs/archive.bin", Body=b"\x00" * 300_000, StorageClass="STANDARD_IA")
        client.put_object(Bucket=BUCKET, Key="cold/deep.txt", Body=b"frozen", StorageClass="DEEP_ARCHIVE")
        yield client


def test_status_reports_connection(s3):
    status = s3_service.status()
    assert status["connected"] is True and status["write_enabled"] is False
    assert "secret" not in str(status).lower()


def test_status_without_credentials(monkeypatch):
    monkeypatch.setattr(s3_service, "_session", lambda: type("S", (), {"get_credentials": lambda self: None})())
    status = s3_service.status()
    assert status["configured"] is False and status["connected"] is False


def test_list_and_scan_bucket(client, s3):
    assert {b["name"] for b in client.get("/api/aws/buckets").json()} == {BUCKET}
    loc = client.post("/api/locations", json={"kind": "s3", "bucket": BUCKET}).json()
    job = client.post("/api/scans", json={"location_id": loc["id"]}).json()
    assert client.get(f"/api/scans/{job['id']}").json()["status"] == "completed"
    files = {f["rel_path"]: f for f in client.get("/api/files", params={"location_id": loc["id"]}).json()["items"]}
    assert files["docs/s3_meeting.md"]["storage_class"] == "STANDARD" and files["docs/s3_meeting.md"]["sha256"]
    assert files["docs/s3_meeting.md"]["text_chars"] > 0
    assert files["docs/archive.bin"]["storage_class"] == "STANDARD_IA"
    assert files["cold/deep.txt"]["storage_class"] == "DEEP_ARCHIVE" and files["cold/deep.txt"]["sha256"] is None
    client.delete(f"/api/locations/{loc['id']}")


def test_bucket_allow_list(client, s3):
    settings = get_settings()
    settings.allowed_s3_buckets = ["some-other-bucket"]
    try:
        assert client.post("/api/locations", json={"kind": "s3", "bucket": BUCKET}).status_code == 403
        assert client.get("/api/aws/buckets").json() == []
    finally:
        settings.allowed_s3_buckets = []


def test_transition_validation_and_dry_run(s3):
    dry = s3_service.change_storage_class(BUCKET, "docs/s3_meeting.md", "GLACIER_IR", dry_run=True)
    assert dry["dry_run"] is True and dry["would_change"] == "STANDARD -> GLACIER_IR"
    assert s3.head_object(Bucket=BUCKET, Key="docs/s3_meeting.md").get("StorageClass", "STANDARD") == "STANDARD"
    with pytest.raises(ValueError, match="restored"):
        s3_service.validate_transition(BUCKET, "cold/deep.txt", "STANDARD")
    with pytest.raises(ValueError, match="already"):
        s3_service.validate_transition(BUCKET, "docs/archive.bin", "STANDARD_IA")
    with pytest.raises(ValueError, match="Unsupported"):
        s3_service.validate_transition(BUCKET, "docs/archive.bin", "REDUCED_REDUNDANCY")


def test_transition_requires_write_flag(s3):
    with pytest.raises(s3_service.S3PermissionError):
        s3_service.change_storage_class(BUCKET, "docs/s3_meeting.md", "STANDARD_IA", dry_run=False)


def test_transition_executes_and_verifies_when_enabled(s3):
    settings = get_settings()
    settings.s3_write_enabled = True
    try:
        result = s3_service.change_storage_class(BUCKET, "docs/archive.bin", "GLACIER_IR", dry_run=False)
    finally:
        settings.s3_write_enabled = False
    assert result["verified_class"] == "GLACIER_IR"
    assert s3.head_object(Bucket=BUCKET, Key="docs/archive.bin")["StorageClass"] == "GLACIER_IR"


def test_plan_s3_action_needs_acknowledgement_and_logs(client, s3, db):
    """End-to-end: cold object in S3 -> plan proposes transition -> approval gate -> executed + logged."""
    from app.db.models import AccessEvent, AccessPrediction, FileRecord, StorageLocation
    from app.services import orchestrator

    s3.put_object(Bucket=BUCKET, Key="big/cold.bin", Body=b"\x01" * (8 * 1024 * 1024))
    loc = StorageLocation(kind="s3", bucket=BUCKET, prefix="big/", name="cold")
    db.add(loc)
    db.commit()
    from app.db.models import ScanJob
    from app.services import scanner

    job = ScanJob(location_id=loc.id)
    db.add(job)
    db.commit()
    scanner.run_scan(job.id)
    record = db.query(FileRecord).filter(FileRecord.location_id == loc.id).one()

    plan = orchestrator.build_plan(db, "aggressive", loc.id)
    # Force a confident "cold" prediction to exercise the cloud path deterministically.
    db.query(AccessPrediction).filter(AccessPrediction.file_id == record.id).update(
        {"probability": 0.0, "reliability": "ok", "reason": "test"}
    )
    db.query(AccessEvent).filter(AccessEvent.file_id == record.id).delete()
    db.commit()
    from app.services import cost_engine

    rec = cost_engine.simulate(db, "aggressive", location_id=loc.id)["rows"][0]
    assert rec["recommended_class"] != "STANDARD"

    action = orchestrator.PlanAction(
        plan_id=plan.id,
        priority=99,
        action_type="s3_change_storage_class",
        title="t",
        reason="r",
        risk_level="high",
        target_file_ids=[record.id],
        params={"bucket": BUCKET, "key": "big/cold.bin", "target_class": rec["recommended_class"]},
    )
    db.add(action)
    db.commit()
    url = f"/api/plans/{plan.id}/actions/{action.id}"
    assert client.post(f"{url}/decision", json={"approve": True}).status_code == 409
    assert client.post(f"{url}/decision", json={"approve": True, "acknowledge_risks": True}).json()["status"] == "approved"
    failed = client.post(f"{url}/execute", json={"dry_run": False}).json()  # writes disabled
    assert failed["status"] == "failed" and "disabled" in failed["result"]["error"]
    logs = client.get("/api/operations", params={"operation": "execute_s3_change_storage_class"}).json()
    assert logs["items"][0]["status"] == "failure"
