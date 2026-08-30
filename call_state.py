"""Shared in-process call state between server.py and bot.py.

Both run in the same process: server.py owns the call registry and records
outcomes; bot.py's tools (log_outcome) write outcomes into it. A tiny
dependency-free module avoids an import cycle.
"""

# call_uuid -> dict:
#   status, started_at, ended_at, phone_number, body, connected,
#   outcome, outcome_note, recording_id, recording_url, recording_served_url,
#   transfer_requested, websocket, path
active_calls: dict[str, dict] = {}
