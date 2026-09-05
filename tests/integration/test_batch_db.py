"""DB + batch-runner tests against local Supabase (skip when not running)."""

import asyncio
import os
import uuid
from pathlib import Path

import psycopg
import pytest

import app.batch.dialer as dialer
import app.batch.runner as batch_runner
from app.calls import repo as db

pytestmark = pytest.mark.db

FIXTURE = Path(__file__).resolve().parent.parent / "data" / "calling_small.csv"


@pytest.fixture()
def campaign_id():
    cid = db.create_campaign(f"test {uuid.uuid4().hex[:8]}", source_file="fixture.csv", total_jobs=0)
    yield cid
    db.delete_campaign(cid)


def _disable_storage(monkeypatch):
    """Keep runner tests deterministic: no real Storage uploads."""
    monkeypatch.delenv("SUPABASE_API_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.setenv("MOCK_CALL_DURATION", "0.05")


def test_db_crud_roundtrip(campaign_id):
    job_id = db.insert_job(
        campaign_id,
        loan_no="10000001",
        customer_name="ALPHA TEST",
        phone="+917299159380",
        agent_name="Gayathri",
        body={"emi": 11183},
    )
    call_id = db.create_call(job_id, campaign_id, "+917299159380")
    db.update_call(call_id, status="ended", connected=True, outcome="PTP", outcome_note="today")
    call = db.get_call(call_id)
    assert call["outcome"] == "PTP"
    assert call["job_id"] == job_id
    assert db.job_stats(campaign_id) == {"pending": 1}
    rows = db.campaign_rows_for_export(campaign_id)
    assert len(rows) == 1 and rows[0]["outcome"] == "PTP"


def test_import_csv_bytes():
    result = batch_runner.import_csv_bytes(FIXTURE.read_bytes(), name=f"unit-{uuid.uuid4().hex[:6]}")
    try:
        assert result["imported"] == 6
        assert result["blocked"] == 0
        assert result["skipped_no_phone"] == 0
        rows = db.campaign_rows_for_export(result["campaign_id"])
        assert len(rows) == 6
        assert rows[0]["phone"] == "+917299159380"
        assert rows[0]["loan_no"] == "10000001"
        with psycopg.connect(os.getenv("DATABASE_URL")) as conn:
            body_row = conn.execute(
                "SELECT body FROM call_jobs WHERE campaign_id = %s ORDER BY created_at LIMIT 1",
                (result["campaign_id"],),
            ).fetchone()
        body = body_row[0]
        assert body["principal"] == 160509
        assert body["first_due_date"] == "2025-07-01"
        assert body["account_number_last4"] == "0001"
    finally:
        db.delete_campaign(result["campaign_id"])


def test_mock_run_full_campaign(monkeypatch):
    _disable_storage(monkeypatch)
    result = batch_runner.import_csv_bytes(FIXTURE.read_bytes(), name=f"run-{uuid.uuid4().hex[:6]}")
    try:
        assert result["imported"] == 6
        asyncio.run(batch_runner.run_campaign(result["campaign_id"], dialer.mock_dialer))
        status = batch_runner.campaign_status(result["campaign_id"])
        assert status["status"] == "done"
        # 6 scripted outcomes -> 5 completed + exactly 1 NO_ANSWER scheduled for retry
        assert status["jobs"].get("completed") == 5
        assert status["jobs"].get("scheduled") == 1
        allowed = {"PTP", "NO_PTP", "NO_ANSWER", "DISPUTE", "HARDSHIP"}
        assert set(status["outcomes"]) <= allowed
        export = batch_runner.export_campaign_csv(result["campaign_id"]).decode()
        assert export.count("\n") == 7  # header + 6 rows
    finally:
        db.delete_campaign(result["campaign_id"])


def test_no_answer_retry_exhausts_after_max(monkeypatch, campaign_id):
    _disable_storage(monkeypatch)
    job_id = db.insert_job(
        campaign_id, loan_no="X", customer_name="RETRY", phone="+919999990001",
        agent_name="A", body={"emi": 1},
    )
    db.update_job(job_id, max_attempts=1)
    # Force the mock's next outcome to NO_ANSWER (index 2 in MOCK_OUTCOMES).
    monkeypatch.setattr(dialer, "_mock_seq", 2)
    asyncio.run(batch_runner.run_campaign(campaign_id, dialer.mock_dialer))
    job = db.get_job(job_id)
    assert job["status"] == "completed"
    assert job["attempts"] == 1
    assert job["last_outcome"] == "NO_ANSWER"


def test_dial_error_schedules_retry(campaign_id):
    async def failing_dialer(job):
        raise RuntimeError("creds missing")

    job_id = db.insert_job(
        campaign_id, loan_no="X", customer_name="ERR", phone="+919999990002",
        agent_name="A", body={"emi": 1},
    )
    asyncio.run(batch_runner.process_job(db.get_job(job_id), failing_dialer))
    job = db.get_job(job_id)
    assert job["status"] == "scheduled"
    assert job["attempts"] == 1
    assert job["last_outcome"] == "FAILED"


def test_blocklist_blocks_at_import(campaign_id):
    db.add_blocklist("+917299159380", reason="ndnc")
    try:
        result = batch_runner.import_csv_bytes(FIXTURE.read_bytes(), name=f"bl-{uuid.uuid4().hex[:6]}")
        try:
            assert result["blocked"] == 1
            assert db.job_stats(result["campaign_id"]).get("blocked") == 1
        finally:
            db.delete_campaign(result["campaign_id"])
    finally:
        with psycopg.connect(os.getenv("DATABASE_URL")) as conn:
            conn.execute("DELETE FROM blocklist WHERE phone = %s", ("+917299159380",))


def test_escalation_rows_created(campaign_id):
    job_id = db.insert_job(
        campaign_id, loan_no="X", customer_name="ESC", phone="+919999990003",
        agent_name="A", body={"emi": 1},
    )
    call_id = db.create_call(job_id, campaign_id, "+919999990003")
    db.update_call(call_id, outcome="HARDSHIP", status="ended", connected=True)
    asyncio.run(batch_runner.finalize(db.get_job(job_id), db.get_call(call_id)))
    assert db.get_job(job_id)["status"] == "completed"
    assert db.get_job(job_id)["last_outcome"] == "HARDSHIP"
    with psycopg.connect(os.getenv("DATABASE_URL")) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM escalations WHERE job_id = %s", (job_id,)
        ).fetchone()[0]
    assert n == 1
