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

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import Response

from app.batch import dialer as dialer_mod
from app.batch import runner as batch_runner
from app.calls import repo as db

router = APIRouter(prefix="/batch", tags=["batch"])


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

    dialer = (
        dialer_mod.mock_dialer
        if dialer_mod.mock_enabled()
        else lambda job: dialer_mod.real_dialer(request.app.state.session, job)
    )
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
