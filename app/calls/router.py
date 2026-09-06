"""Call-state REST surface (in-memory registry view).

Recordings are NOT stored here — call rows carry Vobiz recording_id/url only.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.calls.registry import active_calls

router = APIRouter(tags=["calls"])

@router.get("/active-calls")
async def get_active_calls() -> JSONResponse:
    """List all currently active calls"""
    print("[ACTIVE CALLS] Fetching active calls list")

    # Create a serializable version of active_calls (excluding websocket objects)
    calls_info = {}
    for call_uuid, call_data in active_calls.items():
        calls_info[call_uuid] = {
            "status": call_data.get("status"),
            "started_at": call_data.get("started_at"),
            "path": call_data.get("path"),
            "recording_id": call_data.get("recording_id"),  # Include recording ID if available
            "recording_url": call_data.get("recording_url")  # Include recording URL if available
            # Exclude 'websocket' as it's not JSON serializable
        }

    return JSONResponse({
        "active_calls": list(active_calls.keys()),
        "count": len(active_calls),
        "calls": calls_info
    })


@router.get("/calls")
async def get_calls() -> JSONResponse:
    """List every call (history) with outcome + recording, for the batch caller."""
    calls = []
    for call_uuid, c in active_calls.items():
        calls.append(
            {
                "call_uuid": call_uuid,
                "phone_number": c.get("phone_number"),
                "loanNo": (c.get("body") or {}).get("loanNo"),
                "status": c.get("status"),
                "connected": c.get("connected"),
                "outcome": c.get("outcome"),
                "outcome_note": c.get("outcome_note"),
                "recording_id": c.get("recording_id"),
                "recording_url": c.get("recording_url"),
                "started_at": c.get("started_at"),
                "ended_at": c.get("ended_at"),
            }
        )
    return JSONResponse({"count": len(calls), "calls": calls})


