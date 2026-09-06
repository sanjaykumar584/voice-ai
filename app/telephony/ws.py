"""Vobiz WebSocket media-stream lifecycle."""

import base64
import json
from datetime import datetime

from fastapi import APIRouter, Query, WebSocket

from app.calls.registry import active_calls

router = APIRouter(tags=["telephony-ws"])

async def handle_vobiz_websocket(
    websocket: WebSocket,
    path: str,
    body: str = None,
    serviceHost: str = None,
):
    """Common handler for Vobiz WebSocket connections on any path."""
    print("[DEBUG] ========================================")
    print(f"[DEBUG] WebSocket connection attempt on path: {path}")
    print(f"[DEBUG] Client: {websocket.client}")
    print(f"[DEBUG] Headers: {dict(websocket.headers)}")
    print(f"[DEBUG] Query params - body: {body}, serviceHost: {serviceHost}")
    print("[DEBUG] ========================================")

    try:
        await websocket.accept()
        print("[SUCCESS] WebSocket connection accepted for outbound call")
    except Exception as e:
        print(f"[ERROR] Failed to accept WebSocket connection: {e}")
        raise

    # Decode body parameter if provided
    body_data = {}
    if body:
        try:
            # Base64 decode the JSON (it was base64-encoded in the answer endpoint)
            decoded_json = base64.b64decode(body).decode("utf-8")
            body_data = json.loads(decoded_json)
            print(f"Decoded body data: {body_data}")
        except Exception as e:
            print(f"Error decoding body parameter: {e}")
    else:
        print("No body parameter received")

    call_uuid = None

    try:
        # Import the bot function from the bot module
        from app.voice.transports import bot
        from pipecat.runner.types import WebSocketRunnerArguments

        print("[DEBUG] Starting bot initialization...")

        # Do NOT call parse_telephony_websocket(websocket) here — it consumes
        # the initial handshake messages and leaves the socket "empty" for
        # the Pipecat transport. bot.py uses parse_vobiz_start() instead,
        # which captures the negotiated mediaFormat AND the stream/call IDs.
        call_uuid = (
            websocket.query_params.get("call_uuid")
            or websocket.query_params.get("call_id")
        )
        stream_id = None

        if call_uuid:
            # Update or create entry in active_calls with WebSocket reference
            if call_uuid in active_calls:
                # Update existing entry (from /start pre-registration)
                active_calls[call_uuid]["status"] = "active"
                active_calls[call_uuid]["websocket"] = websocket
                active_calls[call_uuid]["path"] = path
                active_calls[call_uuid]["connected"] = True
                print(f"[CALL] ✅ Updated existing call {call_uuid} with WebSocket")
            else:
                # Create new entry
                active_calls[call_uuid] = {
                    "status": "active",
                    "started_at": datetime.now().isoformat(),
                    "path": path,
                    "websocket": websocket,
                    "transfer_requested": False,
                    "connected": True,
                    "outcome": None,
                    "outcome_note": None,
                }
                print(f"[CALL] ✅ Created new call entry for {call_uuid}")

            # Persist connect to Supabase (batch-driven calls have a DB row keyed
            # by the same uuid). No-op when the DB isn't configured.
            try:
                from app.calls import repo as _db
                if _db.is_configured():
                    _db.update_call_by_vobiz_uuid(call_uuid, status="active", connected=True)
            except Exception as e:
                print(f"[CALL] DB update on connect skipped: {e}")

            print(f"[CALL] Active calls count: {len(active_calls)}")
        else:
            print("[CALL] ⚠️  No call UUID found in URL query params")

        # Create runner arguments and run the bot
        runner_args = WebSocketRunnerArguments(websocket=websocket)
        runner_args.handle_sigint = False

        print("[DEBUG] Calling bot function...")
        # We pass call_id if we have it, but we let stream_id be None so bot/transport can find it from the stream.
        # body_data (decoded from the base64 `body` query param) primes the conversation with reminder details.
        await bot(runner_args, call_id=call_uuid, stream_id=stream_id, body_data=body_data)

        print("[DEBUG] Bot function completed")

    except Exception as e:
        print(f"[ERROR] Error in WebSocket endpoint: {e}")
        import traceback
        print(f"[ERROR] Traceback:\n{traceback.format_exc()}")
        try:
            await websocket.close()
        except:
            pass
    finally:
        # Keep the call record when the WebSocket closes (history for /calls).
        # BUT: don't end it if the call is being transferred.
        if call_uuid and call_uuid in active_calls:
            call_status = active_calls[call_uuid].get("status", "active")
            if call_status == "transferring":
                print(f"[CALL] 🔄 Call {call_uuid} is being transferred - keeping in active_calls")
                # Remove websocket reference but keep call record for transfer
                active_calls[call_uuid]["websocket"] = None
            else:
                active_calls[call_uuid]["status"] = "ended"
                active_calls[call_uuid]["ended_at"] = datetime.now().isoformat()
                active_calls[call_uuid]["websocket"] = None
                print(f"[CALL] ✅ Call {call_uuid} ended (kept in history)")

        # Persist end to Supabase so the batch worker can finalize the job.
        # The WS query params may lack the uuid, so fall back to matching the
        # live registry entry by websocket object.
        try:
            from app.calls import repo as _db
            if _db.is_configured():
                resolved = call_uuid
                if not (resolved and resolved in active_calls):
                    resolved = next(
                        (k for k, v in active_calls.items() if v.get("websocket") is websocket),
                        None,
                    )
                if resolved:
                    entry = active_calls.get(resolved) or {}
                    # Don't finalize transfers in the DB (call continues on another leg).
                    if entry.get("status") != "transferring":
                        _db.update_call_by_vobiz_uuid(
                            resolved,
                            status="ended",
                            ended_at=datetime.now(),
                            connected=entry.get("connected", True),
                        )
        except Exception as e:
            print(f"[CALL] DB update on close skipped: {e}")


# Register WebSocket endpoints for common paths Vobiz might use
@router.websocket("/ws")
async def websocket_ws(
    websocket: WebSocket,
    body: str = Query(None),
    serviceHost: str = Query(None),
):
    """Handle WebSocket connection at /ws path."""
    await handle_vobiz_websocket(websocket, "/ws", body, serviceHost)


@router.websocket("/")
async def websocket_root(
    websocket: WebSocket,
    body: str = Query(None),
    serviceHost: str = Query(None),
):
    """Handle WebSocket connection at root path."""
    await handle_vobiz_websocket(websocket, "/", body, serviceHost)


@router.websocket("/voice/ws")
async def websocket_voice_ws(
    websocket: WebSocket,
    body: str = Query(None),
    serviceHost: str = Query(None),
):
    """Handle WebSocket connection at /voice/ws path to match user XML."""
    await handle_vobiz_websocket(websocket, "/voice/ws", body, serviceHost)


@router.websocket("/stream")
async def websocket_stream(
    websocket: WebSocket,
    body: str = Query(None),
    serviceHost: str = Query(None),
):
    """Handle WebSocket connection at /stream path."""
    await handle_vobiz_websocket(websocket, "/stream", body, serviceHost)


