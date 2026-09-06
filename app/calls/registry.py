"""In-memory call registry — a LIVE-WEBSOCKET CACHE ONLY.

The Supabase Postgres DB (app/calls/repo.py) is the source of truth for call
history, outcomes, and the batch flow. This dict exists so the WebSocket
lifecycle (which receives Vobiz's uuid out-of-band) can track live sockets and
resolve them to the DB key.

call_uuid -> dict with keys: status, started_at, ended_at, phone_number, body,
connected, outcome, outcome_note, recording_id, recording_url,
recording_url, transfer_requested, websocket, path
"""

active_calls: dict[str, dict] = {}
