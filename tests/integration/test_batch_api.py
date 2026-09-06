"""HTTP API integration test for /batch/* — mock mode, local Supabase."""

import csv
import io
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.calls import repo as db
from app.main import create_app

pytestmark = pytest.mark.db

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "calling_small.csv"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("MOCK_CALLS", "true")
    monkeypatch.setenv("MOCK_CALL_DURATION", "0.05")
    with TestClient(create_app()) as c:
        yield c


def _wait_done(client, campaign_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/batch/{campaign_id}")
        assert r.status_code == 200
        if r.json()["status"] == "done":
            return r.json()
        time.sleep(0.15)
    raise AssertionError("campaign did not finish in time")


def test_api_import_run_status_export(client):
    resp = client.post(
        "/batch/import",
        files={"file": ("calling_small.csv", FIXTURE.read_bytes(), "text/csv")},
    )
    assert resp.status_code == 200
    body = resp.json()
    campaign_id = body["campaign_id"]
    try:
        assert body["imported"] == 6

        r = client.post(f"/batch/{campaign_id}/run")
        assert r.status_code == 200
        assert r.json()["status"] == "started"

        status = _wait_done(client, campaign_id)
        assert status["jobs"].get("completed") == 5
        assert status["jobs"].get("scheduled") == 1

        r = client.get(f"/batch/{campaign_id}/export")
        assert r.status_code == 200
        rows = list(csv.DictReader(io.StringIO(r.text)))
        assert len(rows) == 6
        assert rows[0]["outcome"] in {"PTP", "NO_PTP", "NO_ANSWER", "DISPUTE", "HARDSHIP"}
        # only connected calls carry a recording; NO_ANSWER has none
        for row in rows:
            if row["outcome"] == "NO_ANSWER":
                assert row["recording"] == ""
    finally:
        db.delete_campaign(campaign_id)


def test_api_import_bad_file(client):
    resp = client.post(
        "/batch/import",
        files={"file": ("notes.txt", b"not a csv", "text/plain")},
    )
    assert resp.status_code == 400


def test_api_run_unknown_campaign(client):
    assert client.post(f"/batch/{uuid.uuid4()}/run").status_code == 404
    assert client.get(f"/batch/{uuid.uuid4()}").status_code == 404
    assert client.get(f"/batch/{uuid.uuid4()}/export").status_code == 404


def test_api_dry_run_counts_due(client):
    resp = client.post(
        "/batch/import",
        files={"file": ("calling_small.csv", FIXTURE.read_bytes(), "text/csv")},
    )
    campaign_id = resp.json()["campaign_id"]
    try:
        r = client.post(f"/batch/{campaign_id}/run?dry_run=true")
        assert r.status_code == 200
        assert r.json() == {"status": "dry_run", "would_dial": 6}
        # nothing ran
        status = client.get(f"/batch/{campaign_id}").json()
        assert status["jobs"].get("pending") == 6
    finally:
        db.delete_campaign(campaign_id)
