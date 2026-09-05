"""HTTP API to trigger batch calling (see architecture/batch-calling.md).

Endpoints:
  POST /batch/import                       upload CSV -> campaign + jobs
  POST /batch/{campaign_id}/run            start the background worker
  GET  /batch/{campaign_id}                progress + outcome breakdown
  GET  /batch/{campaign_id}/export         download the results CSV
  GET  /batch                              list campaigns

The dial step is real (Vobiz) unless MOCK_CALLS=true (scripted outcomes, no
dialing) — everything else is the same code path.
"""

import asyncio
import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import Response
from loguru import logger

import batch_runner
import db
from call_state import active_calls

router = APIRouter(prefix="/batch", tags=["batch"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _mock_enabled() -> bool:
    return os.getenv("MOCK_CALLS", "").strip().lower() in ("1", "true", "yes", "on")


def _make_dialer(session) -> batch_runner.Dialer:
    if _mock_enabled():
        return batch_runner.mock_dialer
    return lambda job: _real_dialer(session, job)


async def _real_dialer(session, job: dict) -> str | None:
    """Place a real Vobiz call for a job; returns the calls.id or None."""
    from vobiz_api import make_call

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


@router.post("/import")
async def import_campaign(file: UploadFile = File(...)):
    """Upload a spreadsheet CSV -> campaign + jobs (blocklist applied)."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Expected a .csv file")
    data = await file.read()
    try:
        return batch_runner.import_csv_bytes(data, name=os.path.splitext(file.filename)[0])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Import failed: {e}")


@router.post("/{campaign_id}/run")
async def run_campaign(campaign_id: str, request: Request, dry_run: bool = False):
    """Start the background worker for a campaign (returns immediately)."""
    if batch_runner.campaign_status(campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if dry_run:
        return {"status": "dry_run", "would_dial": db.count_due_jobs(campaign_id)}

    dialer = _make_dialer(request.app.state.session)
    asyncio.create_task(batch_runner.run_campaign(campaign_id, dialer))
    return {"status": "started", "campaign_id": campaign_id}


@router.get("")
async def list_batch_campaigns():
    return {"campaigns": batch_runner.list_campaigns()}


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: str):
    status = batch_runner.campaign_status(campaign_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return status


@router.get("/{campaign_id}/export")
async def export_campaign(campaign_id: str):
    if batch_runner.campaign_status(campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    csv_bytes = batch_runner.export_campaign_csv(campaign_id)
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="campaign_{campaign_id}.csv"'},
    )
