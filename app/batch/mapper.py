"""CSV row ↔ collections-body mapping + time helpers for batch calling.

These functions were originally part of the legacy `batch_caller` CLI and are
shared by the import step and the legacy CLI.
"""

import os
from datetime import datetime, time as dtime, timedelta, timezone

DEFAULT_CSV = "/home/sanjay/Downloads/callingv1 - Sheet1.csv"
DEFAULT_SERVER = "http://localhost:7860"

RESULT_COLUMNS = ["outcome", "outcome_note", "recording", "call_status", "called_at", "call_uuid"]

CALLING_START = dtime(8, 0)
CALLING_END = dtime(19, 0)

IST = timezone(timedelta(hours=5, minutes=30))


def default_csv() -> str:
    """Input CSV: --csv flag wins, else BATCH_INPUT_CSV from .env, else DEFAULT_CSV."""
    return os.getenv("BATCH_INPUT_CSV", "").strip() or DEFAULT_CSV


def ist_now() -> datetime:
    """Current time in IST (UTC+5:30)."""
    return datetime.now(IST)


def within_calling_hours(now: datetime) -> bool:
    return CALLING_START <= now.time() <= CALLING_END


def to_int(value) -> int:
    """Robust int parse: handles '81144.58', '', None."""
    if value in (None, ""):
        return 0
    return int(float(str(value).strip()))


def to_iso(dd_mm_yyyy) -> str:
    """DD/MM/YYYY -> YYYY-MM-DD. Passes already-ISO through; empty stays empty."""
    s = str(dd_mm_yyyy or "").strip()
    if not s:
        return ""
    if "/" in s:
        d, m, y = [p.strip() for p in s.split("/")]
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    return s


def normalize_phone(phone) -> str:
    """10-digit Indian number -> +91.... Keeps existing +/00 prefixes."""
    s = str(phone or "").strip().replace(" ", "").replace("-", "")
    if s.startswith("+"):
        return s
    if s.startswith("00"):
        return "+" + s[2:]
    if len(s) == 10 and s.isdigit():
        return "+91" + s
    return s


def row_to_body(row: dict) -> dict:
    """Map a CSV row to the collections body the bot expects."""
    return {
        "agent_name": str(row.get("agentName", "") or "").strip(),
        "company_name": str(row.get("bank", "") or "").strip(),
        "customer_name": str(row.get("customerName", "") or "").strip(),
        "account_number_last4": str(row.get("loanNo", "") or "").strip()[-4:],
        "principal": to_int(row.get("pos")),
        "emi": to_int(row.get("installmentAmount")),
        "first_due_date": to_iso(row.get("emiStartDate")),
        "tenor_months": to_int(row.get("tenor")),
        "emis_received": to_int(row.get("noOfEmisReceived")),
        "loanNo": str(row.get("loanNo", "") or "").strip(),
    }


def derive_result(rec: dict) -> tuple[str, str, str, str]:
    """Map a call record to (call_status, outcome, outcome_note, recording)."""
    status = rec.get("status")
    if status == "timeout":
        return "TIMEOUT", "", "", ""
    if status == "failed":
        return "FAILED", "", "", ""
    outcome = (rec.get("outcome") or "").strip()
    note = (rec.get("outcome_note") or "").strip()
    recording = (rec.get("recording_url") or "").strip()
    if outcome:
        return "ENDED", outcome, note, recording
    if rec.get("connected"):
        return "ENDED", "NO_OUTCOME", "", recording
    return "NO_ANSWER", "", "", recording
