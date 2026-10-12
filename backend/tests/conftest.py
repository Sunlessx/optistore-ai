"""Shared fixtures.

Environment variables are set *before* the application is imported so that
tests use a throw-away SQLite database, a temporary data directory and the
fast, deterministic lexical embedding backend. (The transformer model is
exercised separately in ``test_semantic_model.py`` when it is available.)
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="optistore-tests-"))
PROJECT_DIR = Path(__file__).resolve().parents[2]
SAMPLE_DIR = PROJECT_DIR / "sample_data"

os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{(_TMP / 'test.db').as_posix()}",
        "DATA_DIR": str(_TMP / "data"),
        "ALLOWED_SCAN_ROOTS": f"{SAMPLE_DIR};{_TMP / 'scan_roots'}",
        "EMBEDDING_BACKEND": "hashing",
        "API_KEY": "",
        "S3_WRITE_ENABLED": "false",
        "ALLOWED_S3_BUCKETS": "",
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
    }
)
(_TMP / "scan_roots").mkdir(parents=True, exist_ok=True)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def scan_root() -> Path:
    """A writable directory inside the allowed roots for tests that need custom files."""
    return _TMP / "scan_roots"


@pytest.fixture(scope="session")
def demo(client):
    """Register and scan the demo dataset once; returns ids by file name."""
    if not SAMPLE_DIR.exists():
        pytest.skip("sample_data missing: run scripts/generate_demo_data.py")
    response = client.post("/api/locations", json={"kind": "local", "path": str(SAMPLE_DIR), "name": "demo"})
    assert response.status_code == 201, response.text
    location_id = response.json()["id"]
    scan = client.post("/api/scans", json={"location_id": location_id})
    assert scan.status_code == 202
    job = client.get(f"/api/scans/{scan.json()['id']}").json()
    assert job["status"] == "completed", job
    files = client.get("/api/files", params={"location_id": location_id, "page_size": 500}).json()["items"]
    return {"location_id": location_id, "ids": {f["rel_path"]: f["id"] for f in files}, "files": files}


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    yield session
    session.close()
