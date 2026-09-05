"""Dialers for the batch runner: real (Vobiz) or mock (scripted, no dialing).

MOCK_CALLS=true selects the mock dialer — same code path everywhere else, which
is how the whole batch flow is testable without a Vobiz number.
"""

import asyncio
import os
from datetime import datetime, timezone

from loguru import logger

from app.calls import repo as db
from app.calls.registry import active_calls

# Scripted outcomes mock mode rotates through, so retries/export get variety.
MOCK_OUTCOMES = ["PTP", "NO_PTP", "NO_ANSWER", "DISPUTE", "HARDSHIP", "PTP"]

_mock_seq: int = 0  # rotates scripted outcomes across calls


def mock_enabled() -> bool:
    return os.getenv("MOCK_CALLS", "").strip().lower() in ("1", "true", "yes", "on")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------- mock dialer ------------------------------- #


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
    fields = {
        "status": "ended",
        "connected": True,
        "outcome": outcome,
        "outcome_note": "mock",
        "ended_at": _utcnow(),
    }
    if outcome == "NO_ANSWER":
        fields = {"status": "ended", "connected": False, "ended_at": _utcnow()}
    if fields.get("connected"):
        # Optionally exercise the real Storage path with a tiny dummy file.
        try:
            from app.storage import recordings as storage

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


# ------------------------------- real dialer ------------------------------- #


async def real_dialer(session, job: dict) -> str | None:
    """Place a real Vobiz call for a job; returns the calls.id or None."""
    from app.telephony.vobiz import make_call

    public_url = os.getenv("PUBLIC_URL", "").rstrip("/")
    if not public_url:
        raise RuntimeError("PUBLIC_URL is not set — Vobiz webhooks need it")

    call_id = db.create_call(job["id"], job["campaign_id"], job["phone"])
    try:
        result = await make_call(
            session,
            to_number=job["phone"],
            from_number=os.getenv("VOBIZ_PHONE_NUMBER", ""),
            answer_url=f"{public_url}/answer",
        )
    except Exception as e:
        logger.warning(f"[batch] Vobiz dial error for job {job['id']}: {e}")
        db.update_call(call_id, status="failed", error=str(e), ended_at=_utcnow())
        return None

    vobiz_uuid = result.get("request_uuid") or result.get("call_uuid") or ""
    if not vobiz_uuid or vobiz_uuid == "unknown":
        db.update_call(call_id, status="failed", error="no call_uuid from Vobiz", ended_at=_utcnow())
        return None

    db.update_call(call_id, status="ringing", vobiz_call_uuid=vobiz_uuid, started_at=_utcnow())
    # Pre-register for the WS handler / transfer logic (same key Vobiz sends back).
    active_calls[vobiz_uuid] = {
        "status": "initiated",
        "started_at": _utcnow().isoformat(),
        "transfer_requested": False,
        "websocket": None,
        "phone_number": job["phone"],
        "body": job["body"],
        "connected": False,
        "outcome": None,
        "outcome_note": None,
    }
    return call_id
