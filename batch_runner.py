"""Batch runner — import a spreadsheet, dial its jobs, finalize outcomes.

Orchestration only: the actual "place a call" step is injected as a dialer
(server-side real dialer, or a mock), so the whole flow is testable without
Vobiz. State lives in Supabase Postgres via db.py.
"""

import asyncio
import csv
import io
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from loguru import logger

import db
from batch_caller import normalize_phone, row_to_body

# Dialer: async (job: dict) -> calls.id | None (None = failed to place).
Dialer = Callable[[dict], Awaitable[str | None]]

POLL_INTERVAL = float(os.getenv("BATCH_POLL_INTERVAL", "2.0"))
CALL_TIMEOUT = float(os.getenv("BATCH_CALL_TIMEOUT", "600"))
RETRY_MINUTES_NO_ANSWER = int(os.getenv("BATCH_RETRY_MINUTES_NO_ANSWER", "1440"))
RETRY_MINUTES_DIAL_ERROR = int(os.getenv("BATCH_RETRY_MINUTES_DIAL_ERROR", "10"))

# Scripted outcomes mock mode rotates through, so retries/export get variety.
MOCK_OUTCOMES = ["PTP", "NO_PTP", "NO_ANSWER", "DISPUTE", "HARDSHIP", "PTP"]

_running_campaigns: set[str] = set()
_mock_seq: int = 0  # rotates scripted outcomes across calls (tests retries/escalations)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------- import --------------------------------- #


def import_csv_bytes(data: bytes, name: str | None = None) -> dict:
    """Parse a CSV upload into a campaign + call_jobs (blocklist applied)."""
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    campaign_id = db.create_campaign(name or "campaign", source_file=name, total_jobs=len(rows))

    imported = blocked = no_phone = 0
    for row in rows:
        phone = normalize_phone(row.get("phoneNo"))
        if not phone:
            no_phone += 1
            continue
        bl = db.is_blocklisted(phone)
        body = row_to_body(row)
        db.insert_job(
            campaign_id,
            loan_no=str(row.get("loanNo") or "").strip() or None,
            customer_name=str(row.get("customerName") or "").strip() or None,
            phone=phone,
            agent_name=str(row.get("agentName") or "").strip() or None,
            body=body,
            blocklisted=bl,
        )
        imported += 1
        if bl:
            blocked += 1

    db.update_campaign(campaign_id, total_jobs=imported, status="draft")
    db.audit(
        actor="api",
        action="campaign_imported",
        entity_type="campaign",
        entity_id=campaign_id,
        details={"rows": len(rows), "imported": imported, "blocked": blocked, "no_phone": no_phone},
    )
    return {
        "campaign_id": campaign_id,
        "name": name or "campaign",
        "imported": imported,
        "blocked": blocked,
        "skipped_no_phone": no_phone,
    }


# ------------------------------ run loop --------------------------------- #


async def run_campaign(campaign_id: str, dialer: Dialer, *, dry_run: bool = False) -> dict:
    """Dial every due job of a campaign (one at a time) until none remain."""
    if campaign_id in _running_campaigns:
        return {"status": "already_running"}
    if db.get_campaign(campaign_id) is None:
        return {"status": "not_found"}

    if dry_run:
        return {"status": "dry_run", "would_dial": db.count_due_jobs(campaign_id)}

    _running_campaigns.add(campaign_id)
    db.update_campaign(campaign_id, status="running")
    try:
        while True:
            job = db.claim_next_due_job(campaign_id)
            if not job:
                break
            logger.info(f"[batch] processing job {job['id']} ({job['phone']})")
            await process_job(job, dialer)
            await asyncio.sleep(0.25)
        db.update_campaign(campaign_id, status="done")
        logger.info(f"[batch] campaign {campaign_id} done")
        return {"status": "done"}
    finally:
        _running_campaigns.discard(campaign_id)


async def process_job(job: dict, dialer: Dialer) -> None:
    """Dial one job, wait for the call to finish, then finalize (retry/complete)."""
    # Re-check the blocklist right before dialing (opt-outs land at any time).
    if db.is_blocklisted(job["phone"]):
        db.update_job(job["id"], status="blocked", blocklist_hit=True)
        return

    call_id = None
    error = ""
    try:
        call_id = await dialer(job)
    except Exception as e:  # dialer raised (missing creds, Vobiz error...)
        error = str(e)
        logger.warning(f"[batch] dial failed for job {job['id']}: {error}")

    if call_id is None:
        # The dial never happened — retry later, then give up after max_attempts.
        db.audit(actor="system", action="call_failed", entity_type="job", entity_id=job["id"],
                 details={"error": error or "dial failed"})
        _retry_or_complete(job, "FAILED", RETRY_MINUTES_DIAL_ERROR)
        return

    # Wait for the call lifecycle (WS closed / mock ended / timeout).
    deadline = time.monotonic() + CALL_TIMEOUT
    while True:
        call = db.get_call(call_id)
        if call and call["status"] in ("ended", "failed", "timeout"):
            break
        if time.monotonic() >= deadline:
            db.update_call(call_id, status="timeout", ended_at=_utcnow())
            break
        await asyncio.sleep(POLL_INTERVAL)

    call = db.get_call(call_id) or {}
    await finalize(job, call)


async def finalize(job: dict, call: dict) -> None:
    """Apply the call's outcome to its job: complete, escalate, or retry."""
    attempts = (job.get("attempts") or 0) + 1
    outcome = (call.get("outcome") or "").strip()
    status = call.get("status")

    if outcome:
        db.update_job(job["id"], attempts=attempts, last_outcome=outcome, status="completed")
        if outcome in db.ESCALATION_OUTCOMES:
            db.insert_escalation(
                call_id=call.get("id"),
                job_id=job["id"],
                flag=outcome,
                note=call.get("outcome_note") or "",
            )
    elif status == "timeout":
        _retry_or_complete(job, "TIMEOUT", RETRY_MINUTES_DIAL_ERROR)
    elif call.get("connected"):
        # Answered but the bot never logged an outcome — surface for review.
        db.update_job(job["id"], attempts=attempts, last_outcome="NO_OUTCOME", status="completed")
    else:
        # Ended without a connection = no answer.
        _retry_or_complete(job, "NO_ANSWER", RETRY_MINUTES_NO_ANSWER)

    db.audit(
        actor="system",
        action="job_finalized",
        entity_type="job",
        entity_id=job["id"],
        details={"attempts": attempts, "outcome": outcome or job.get("last_outcome"),
                 "call_status": status},
    )


def _retry_or_complete(job: dict, label: str, retry_minutes: int) -> None:
    attempts = (job.get("attempts") or 0) + 1
    max_attempts = job.get("max_attempts") or 3
    if attempts < max_attempts:
        db.update_job(
            job["id"],
            attempts=attempts,
            last_outcome=label,
            status="scheduled",
            next_attempt_at=_utcnow() + timedelta(minutes=retry_minutes),
        )
    else:
        db.update_job(job["id"], attempts=attempts, last_outcome=label, status="completed")


# ------------------------------ mock dialer ------------------------------ #


async def mock_dialer(job: dict) -> str:
    """Simulate a call: create the calls row, end it shortly with a scripted outcome."""
    call_id = db.create_call(job["id"], job["campaign_id"], job["phone"])
    asyncio.create_task(_mock_end_call(call_id, job))
    return call_id


async def _mock_end_call(call_id: str, job: dict) -> None:
    await asyncio.sleep(float(os.getenv("MOCK_CALL_DURATION", "1.5")))
    global _mock_seq
    _mock_seq += 1
    outcome = MOCK_OUTCOMES[(_mock_seq - 1) % len(MOCK_OUTCOMES)]
    fields = {"status": "ended", "connected": True, "outcome": outcome,
              "outcome_note": "mock", "ended_at": _utcnow()}
    if outcome == "NO_ANSWER":
        fields = {"status": "ended", "connected": False, "ended_at": _utcnow()}
    if fields.get("connected"):
        # Optionally exercise the real Storage path with a tiny dummy file.
        try:
            import storage
            if storage.is_configured():
                key = f"{call_id}.mp3"  # upload_bytes prefixes the bucket
                storage.upload_bytes(key, b"mock-audio")
                url = storage.signed_url(key)
                if url:
                    fields["recording_key"] = key
                    fields["recording_served_url"] = url
        except Exception as e:
            logger.warning(f"[batch] mock recording upload skipped: {e}")
    db.update_call(call_id, **fields)


# -------------------------------- status --------------------------------- #


def campaign_status(campaign_id: str) -> dict | None:
    campaign = db.get_campaign(campaign_id)
    if campaign is None:
        return None
    return {
        "campaign_id": campaign_id,
        "name": campaign["name"],
        "status": campaign["status"],
        "total_jobs": campaign["total_jobs"],
        "jobs": db.job_stats(campaign_id),
        "outcomes": db.outcome_stats(campaign_id),
    }


def list_campaigns() -> list[dict]:
    return db.list_campaigns()


# -------------------------------- export --------------------------------- #


def export_campaign_csv(campaign_id: str) -> bytes:
    """Results CSV for a campaign (the sheet's outcome columns)."""
    cols = [
        "loanNo", "customerName", "phone", "outcome", "outcome_note",
        "recording", "call_status", "attempts", "called_at", "call_uuid",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in db.campaign_rows_for_export(campaign_id):
        writer.writerow(
            {
                "loanNo": r.get("loan_no") or "",
                "customerName": r.get("customer_name") or "",
                "phone": r.get("phone") or "",
                "outcome": (r.get("outcome") or r.get("last_outcome") or ""),
                "outcome_note": r.get("outcome_note") or "",
                "recording": r.get("recording_served_url") or "",
                "call_status": r.get("job_status") or "",
                "attempts": r.get("attempts") or 0,
                "called_at": _iso(r.get("ended_at") or r.get("started_at")),
                "call_uuid": r.get("vobiz_call_uuid") or "",
            }
        )
    return buf.getvalue().encode("utf-8")


def _iso(value) -> str:
    if value is None:
        return ""
    return value.isoformat(timespec="seconds") if hasattr(value, "isoformat") else str(value)
