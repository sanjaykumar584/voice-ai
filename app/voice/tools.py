"""The agent's tools (function calling): outcome logging and graceful end."""

from loguru import logger
from pipecat.frames.frames import EndWorkerFrame
from pipecat.services.llm_service import FunctionCallParams

from app.calls.registry import active_calls


async def log_outcome(params: FunctionCallParams, status: str, note: str = ""):
    """Record the outcome of a collections call.

    Args:
        status: One of "PTP", "NO_PTP", "NO_ARREARS", "DISPUTE", "HARDSHIP",
            "DECEASED", "SURRENDER", "HOSTILE", "WRONG_NUMBER".
        note: Optional note. For PTP, echo the customer's own stated amount and date.
    """
    logger.info(f"[OUTCOME] call outcome: {status} — {note}")

    # In-memory registry (live WebSocket cache; legacy REST surface).
    call_id = (params.app_resources or {}).get("call_id")
    if call_id and call_id in active_calls:
        active_calls[call_id]["outcome"] = status
        active_calls[call_id]["outcome_note"] = note

    # Supabase: write to the calls row (batch-driven calls keyed by the Vobiz
    # uuid) and escalate compliance-flagged outcomes. No-op when unconfigured
    # or when the call has no DB row (browser/eval/single /start calls).
    try:
        from app.calls import repo as _repo

        if _repo.is_configured() and call_id:
            row = _repo.get_call_by_vobiz_uuid(call_id)
            if row:
                _repo.update_call(row["id"], outcome=status, outcome_note=note)
                if status in _repo.ESCALATION_OUTCOMES:
                    _repo.insert_escalation(
                        call_id=row["id"], job_id=row["job_id"], flag=status, note=note
                    )
    except Exception as e:
        logger.warning(f"[OUTCOME] DB write skipped: {e}")

    await params.result_callback({"recorded": True, "status": status})


async def end_call(params: FunctionCallParams):
    """End the call once the user has said goodbye and the outcome is recorded."""
    await params.result_callback({"success": True})
    await params.llm.push_frame(EndWorkerFrame())
