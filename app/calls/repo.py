"""Supabase (Postgres) access for the batch-calling flow.

Thin psycopg helpers over the schema in
``supabase/migrations/0001_batch_calling.sql``. Every function opens a short
connection (fine at <100 calls/day); swap for a pool when volume grows.

Design notes (see architecture/database.md):
- RLS is OFF for now; every row carries tenant_id ('default').
- call_jobs = one row per customer; calls = one row per dial attempt.
"""

import os
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

# Columns each table may be updated with via the generic _update helper.
_ALLOWED_UPDATE_COLUMNS = {
    "campaigns": {"name", "source_file", "status", "total_jobs"},
    "call_jobs": {
        "status",
        "blocklist_hit",
        "attempts",
        "max_attempts",
        "next_attempt_at",
        "last_outcome",
    },
    "calls": {
        "status",
        "connected",
        "outcome",
        "outcome_note",
        "error",
        "recording_key",
        "recording_served_url",
        "duration_secs",
        "cost_estimate",
        "started_at",
        "ended_at",
        "vobiz_call_uuid",
    },
}

# Outcomes that need human attention (escrow to the escalations table).
ESCALATION_OUTCOMES = {"HARDSHIP", "DECEASED", "SURRENDER", "HOSTILE", "DISPUTE"}


def is_configured() -> bool:
    return bool(os.getenv("DATABASE_URL", "").strip())


@contextmanager
def _conn():
    """Yield a connection (dict rows); commit on success, rollback on error."""
    if not is_configured():
        raise RuntimeError("DATABASE_URL is not set — cannot reach Supabase Postgres")
    conn = psycopg.connect(os.getenv("DATABASE_URL"), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _update(table: str, id_col: str, id_val: Any, fields: dict) -> None:
    """Update allowed columns of one row (table/id are internal constants)."""
    sets = {k: v for k, v in fields.items() if k in _ALLOWED_UPDATE_COLUMNS[table]}
    if not sets:
        return
    extra = ", updated_at = now()" if table in ("campaigns", "call_jobs") else ""
    cols = ", ".join(f"{k} = %s" for k in sets)
    with _conn() as conn:
        conn.execute(
            f"UPDATE {table} SET {cols}{extra} WHERE {id_col} = %s",
            [*sets.values(), id_val],
        )


# ------------------------------- campaigns ------------------------------- #


def create_campaign(name: str, source_file: str | None = None, total_jobs: int = 0) -> str:
    with _conn() as conn:
        row = conn.execute(
            "INSERT INTO campaigns (name, source_file, total_jobs, status) "
            "VALUES (%s, %s, %s, 'draft') RETURNING id",
            (name, source_file, total_jobs),
        ).fetchone()
        return row["id"]


def get_campaign(campaign_id: str) -> dict | None:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM campaigns WHERE id = %s", (campaign_id,)
        ).fetchone()


def list_campaigns() -> list[dict]:
    with _conn() as conn:
        return conn.execute(
            "SELECT id, name, source_file, status, total_jobs, created_at "
            "FROM campaigns ORDER BY created_at DESC"
        ).fetchall()


def delete_campaign(campaign_id: str) -> None:
    """Delete a campaign and its audit rows (jobs/calls cascade). Tests + admin."""
    with _conn() as conn:
        conn.execute(
            "DELETE FROM audit_log WHERE entity_type = 'campaign' AND entity_id = %s",
            (str(campaign_id),),
        )
        conn.execute("DELETE FROM campaigns WHERE id = %s", (campaign_id,))


def update_campaign(campaign_id: str, **fields) -> None:
    _update("campaigns", "id", campaign_id, fields)


# -------------------------------- call_jobs ------------------------------- #


def insert_job(
    campaign_id: str,
    *,
    loan_no: str | None,
    customer_name: str | None,
    phone: str,
    agent_name: str | None,
    body: dict,
    blocklisted: bool = False,
) -> str:
    with _conn() as conn:
        row = conn.execute(
            "INSERT INTO call_jobs (campaign_id, body, loan_no, customer_name, phone, "
            "agent_name, status, blocklist_hit) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                campaign_id,
                psycopg.types.json.Jsonb(body),
                loan_no,
                customer_name,
                phone,
                agent_name,
                "blocked" if blocklisted else "pending",
                blocklisted,
            ),
        ).fetchone()
        return row["id"]


def get_job(job_id: str) -> dict | None:
    with _conn() as conn:
        return conn.execute("SELECT * FROM call_jobs WHERE id = %s", (job_id,)).fetchone()


def claim_next_due_job(campaign_id: str) -> dict | None:
    """Atomically claim the oldest due job of a campaign (returns it as 'running')."""
    with _conn() as conn:
        return conn.execute(
            """
            UPDATE call_jobs SET status = 'running', updated_at = now()
            WHERE id = (
                SELECT id FROM call_jobs
                WHERE campaign_id = %s
                  AND status IN ('pending', 'scheduled')
                  AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                ORDER BY created_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            (campaign_id,),
        ).fetchone()


def update_job(job_id: str, **fields) -> None:
    _update("call_jobs", "id", job_id, fields)


def count_due_jobs(campaign_id: str) -> int:
    """Jobs that are currently dialable (pending/scheduled and due)."""
    with _conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM call_jobs
            WHERE campaign_id = %s
              AND status IN ('pending', 'scheduled')
              AND (next_attempt_at IS NULL OR next_attempt_at <= now())
            """,
            (campaign_id,),
        ).fetchone()
        return row["n"]


def job_stats(campaign_id: str) -> dict:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM call_jobs "
            "WHERE campaign_id = %s GROUP BY status",
            (campaign_id,),
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}


def outcome_stats(campaign_id: str) -> dict:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT c.outcome, COUNT(*) AS n FROM calls c "
            "JOIN call_jobs j ON j.id = c.job_id "
            "WHERE j.campaign_id = %s AND c.outcome IS NOT NULL GROUP BY c.outcome",
            (campaign_id,),
        ).fetchall()
        return {r["outcome"]: r["n"] for r in rows}


# ---------------------------------- calls --------------------------------- #


def create_call(job_id: str, campaign_id: str, phone: str) -> str:
    with _conn() as conn:
        row = conn.execute(
            "INSERT INTO calls (job_id, campaign_id, phone, status) "
            "VALUES (%s, %s, %s, 'initiated') RETURNING id",
            (job_id, campaign_id, phone),
        ).fetchone()
        return row["id"]


def get_call(call_id: str) -> dict | None:
    with _conn() as conn:
        return conn.execute("SELECT * FROM calls WHERE id = %s", (call_id,)).fetchone()


def get_call_by_vobiz_uuid(vobiz_call_uuid: str) -> dict | None:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM calls WHERE vobiz_call_uuid = %s ORDER BY created_at DESC LIMIT 1",
            (vobiz_call_uuid,),
        ).fetchone()


def latest_call_for_job(job_id: str) -> dict | None:
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM calls WHERE job_id = %s ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()


def update_call(call_id: str, **fields) -> None:
    _update("calls", "id", call_id, fields)


def update_call_by_vobiz_uuid(vobiz_call_uuid: str, **fields) -> None:
    sets = {k: v for k, v in fields.items() if k in _ALLOWED_UPDATE_COLUMNS["calls"]}
    if not sets:
        return
    cols = ", ".join(f"{k} = %s" for k in sets)
    with _conn() as conn:
        conn.execute(
            f"UPDATE calls SET {cols} WHERE vobiz_call_uuid = %s",
            [*sets.values(), vobiz_call_uuid],
        )


# -------------------------------- blocklist ------------------------------- #


def is_blocklisted(phone: str) -> bool:
    with _conn() as conn:
        return (
            conn.execute(
                "SELECT 1 FROM blocklist WHERE phone = %s LIMIT 1", (phone,)
            ).fetchone()
            is not None
        )


def add_blocklist(phone: str, reason: str = "opt-out") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO blocklist (phone, reason) VALUES (%s, %s) "
            "ON CONFLICT (tenant_id, phone) DO NOTHING",
            (phone, reason),
        )


# ------------------------------- escalations ------------------------------ #


def insert_escalation(*, call_id: str | None, job_id: str | None, flag: str, note: str = "") -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO escalations (call_id, job_id, flag, note) VALUES (%s, %s, %s, %s)",
            (call_id, job_id, flag, note),
        )


# -------------------------------- audit log ------------------------------- #


def audit(*, actor: str, action: str, entity_type: str | None = None, entity_id: str | None = None, details: dict | None = None) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (actor, action, entity_type, entity_id, details) "
            "VALUES (%s, %s, %s, %s, %s)",
            (actor, action, entity_type, entity_id, psycopg.types.json.Jsonb(details) if details else None),
        )


# --------------------------------- export --------------------------------- #


def campaign_rows_for_export(campaign_id: str) -> list[dict]:
    """Jobs joined with their latest call (the export/report shape)."""
    with _conn() as conn:
        return conn.execute(
            """
            SELECT j.loan_no, j.customer_name, j.phone, j.agent_name,
                   j.status AS job_status, j.blocklist_hit, j.attempts,
                   j.last_outcome, j.created_at AS job_created_at,
                   c.outcome, c.outcome_note, c.recording_served_url,
                   c.started_at, c.ended_at, c.vobiz_call_uuid
            FROM call_jobs j
            LEFT JOIN LATERAL (
                SELECT * FROM calls WHERE job_id = j.id
                ORDER BY created_at DESC LIMIT 1
            ) c ON TRUE
            WHERE j.campaign_id = %s
            ORDER BY j.created_at
            """,
            (campaign_id,),
        ).fetchall()
