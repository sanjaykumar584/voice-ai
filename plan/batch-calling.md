# Plan — Batch calling from a spreadsheet

> Status: **implemented** (2026-08-29). See `batch_caller.py`, `call_state.py`,
> `server.py` (`/calls`, `/recordings`), `bot.py` (outcome capture),
> `tests/test_batch_mapper.py`, `tests/test_outcome_store.py`.

## Goal

Feed the agent a spreadsheet of customers (`/home/sanjay/Downloads/callingv1 - Sheet1.csv`),
have it call each one (1 at a time), and write the **outcome + recording link**
back into the same spreadsheet.

## The input (found in Downloads)

`callingv1 - Sheet1.csv` — 176 customers + header. Columns:

| CSV column | → bot variable | Notes |
|---|---|---|
| `loanNo` | `account_number_last4` | take last 4 digits |
| `customerName` | `customer_name` | double spaces — strip |
| `pos` | `principal` | one row is `81144.58` (decimal) — parse float→int |
| `installmentAmount` | `emi` | |
| `agentName` | `agent_name` | persona name |
| `noOfEmisReceived` | `emis_received` | |
| `emiStartDate` | `first_due_date` | **DD/MM/YYYY → must convert to YYYY-MM-DD** |
| `tenor` | `tenor_months` | |
| `bank` | `company_name` | e.g. "HDB Finance" |
| `phoneNo` | → `to` | 10-digit Indian — needs `+91` prefix for Vobiz |

## Gaps in the current code

1. `log_outcome` only logs — nothing writes back to a store.
2. `server.py` deletes ended calls from `active_calls` — no call history to read results from.
3. No batch driver exists (nothing reads the CSV / places calls / writes results).
4. `VOBIZ_PHONE_NUMBER` still empty in `.env` — live calls blocked until a number is bought.

## Implementation plan

### 1. `server.py` — call history + outcome store
- Keep ended calls (mark `status: "ended"` instead of deleting); record `outcome`, `note`, `connected` per call (plus existing `recording_id`/`recording_url`).
- New `GET /calls` → list every call: phone, loanNo, outcome, note, recording URL, timestamps.
- New `GET /recordings/{file}` → serve MP3s from `recordings/` (path-traversal guarded) so the sheet gets a URL.
- `POST /start` already threads per-call `body` — the driver passes the CSV row as the body.

### 2. `bot.py` — outcomes reach the store
- `run_bot` accepts `app_resources` (call_id + shared outcomes dict) → passed to `PipelineTask(app_resources=…)`.
- `log_outcome` / `end_call` write `{status, note}` via `params.app_resources`.
- (Verified: `FunctionCallParams.app_resources` exists; `PipelineTask` accepts `app_resources`.)

### 3. `batch_caller.py` — the loop
```
load callingv1 - Sheet1.csv
  → for each row (1 call at a time):
      skip: no phone / already has an outcome (resume-safe) / outside 8AM–7PM IST
      map row → collections body (+91, ISO dates, int parsing, loanNo last-4)
      POST /start  → call_uuid
      poll GET /calls until this call ends (timeout → FAILED)
      read outcome + recording URL
      append result columns to the SAME CSV
```
- New columns appended: `outcome`, `outcome_note`, `recording`, `call_status`, `called_at`, `call_uuid`.
- In-place CSV update: timestamped backup (`callingv1_backup_<ts>.csv`) first, then atomic rewrite.
- Recording column = served URL: `{PUBLIC_URL}/recordings/<id>.mp3` (localhost fallback).
- Flags: `--dry-run` (print mapped bodies, no calls), `--limit N`, `--from <index>`, `--force` (ignore calling hours).
- End-of-run summary: counts of PTP / NO_PTP / NO_ANSWER / FAILED / etc.

### 4. Tests & verification
- `tests/test_batch_mapper.py` — CSV→body mapping (decimal `pos`, date conversion, +91, last-4, skip rules).
- `python batch_caller.py --dry-run` — all 176 rows map cleanly, zero calls.
- Sanity: `GET /calls` empty list; `GET /recordings/…` serves correctly.
- Live run (`--limit 2` with own test numbers first) — **blocked until a Vobiz number is bought**.

## Decisions made (user-confirmed)
- Results written to **the same CSV** (with backup).
- Recording column = **served URL** (`GET /recordings/<file>`).
- **1 call at a time**.
