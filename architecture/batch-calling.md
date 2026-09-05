# Batch Calling — Spreadsheet → Calls → Results

> Developer guide for the `/batch/*` API and the Supabase-backed batch flow.
> Read [`architecture.md`](./architecture.md) and
> [`database.md`](./database.md) (schema) first.

---

## 1. What it is (and why)

A collections team has a **spreadsheet of customers** and wants:

1. a call placed to **every row**,
2. the **outcome** of each call written back (PTP / NO_PTP / HARDSHIP / …),
3. the **recording** reachable per row.

Batch calling is the HTTP API that does this. It is triggered by **uploading
the CSV** — state lives in **Supabase Postgres**, the CSV is import/export
only, and (for testing) `MOCK_CALLS=true` simulates calls without dialing.

---

## 2. The end-to-end flow

```
curl -F file=@sheet.csv ──► POST /batch/import ──► campaigns + call_jobs (blocklist applied)
curl -X POST /batch/{id}/run ──► background worker (one call at a time)
      │  picks next due job ──► creates a `calls` row ──► dials (real or MOCK_CALLS)
      │                                                 │
      │        real: Vobiz → WS → bot → log_outcome ────┤ writes outcome to the DB
      │        mock: scripted outcome after ~1.5s ──────┘
      ▼
  finalize: complete / reschedule (NO_ANSWER → next day, ≤ max_attempts)
            + escalation rows for HARDSHIP/DECEASED/SURRENDER/HOSTILE/DISPUTE
      ▼
curl /batch/{id} ──► progress          curl /batch/{id}/export ──► results CSV
```

---

## 3. Files & responsibilities

| File | Role |
|---|---|
| `batch_api.py` | The HTTP router (`/batch/*`); real dialer (Vobiz) or mock |
| `batch_runner.py` | Import CSV → jobs; the run loop; finalize/retry; export; mock dialer |
| `db.py` | psycopg access to Supabase Postgres (campaigns/jobs/calls/… helpers) |
| `storage.py` | Upload MP3s to the `recordings` bucket + short-lived signed URLs |
| `vobiz_api.py` | The Vobiz REST "place a call" helper |
| `server.py` | Hosts the API + the call/WebSocket lifecycle (writes connect/end to the DB) |
| `bot.py` | `log_outcome` writes the outcome (and escalations) to the DB |
| `call_state.py` | **In-memory only** for live WebSocket lookups — the DB is the source of truth |
| `tests/test_batch_db.py`, `tests/test_batch_api.py` | DB/runner + HTTP integration tests (skip without Supabase) |

### Why separate `jobs` from `calls`?

`call_jobs` = one row per **customer** (the intent + retry state);
`calls` = one row per **dial attempt**. A retry is a new `calls` row — the job
just counts attempts. Schema details: [`database.md`](./database.md).

---

## 4. The API

| Endpoint | Purpose |
|---|---|
| `POST /batch/import` | Multipart `.csv` upload → campaign + jobs; returns `{campaign_id, imported, blocked, skipped_no_phone}` |
| `POST /batch/{campaign_id}/run` | Starts the background worker (202-style `{"status":"started"}`); `?dry_run=true` counts due jobs instead |
| `GET /batch/{campaign_id}` | Progress: campaign status, job-status counts, outcome breakdown |
| `GET /batch/{campaign_id}/export` | Downloads the results CSV |
| `GET /batch` | List campaigns |

Worker rules: dials one at a time, re-checks the **blocklist** right before
dialing, waits up to `BATCH_CALL_TIMEOUT`, marks `NO_ANSWER` for a **next-day
retry** (`BATCH_RETRY_MINUTES_NO_ANSWER`) up to `max_attempts` (default 3),
retries dial errors in 10 minutes, and finishes the campaign when no job is due.

## 5. CSV mapping (unchanged from the CLI era)

Same columns as `batch_caller.py`: `phoneNo` → `+91…`, `emiStartDate` → ISO,
decimal `pos` → int, `loanNo` last-4 → `account_number_last4`, plus the other
script variables. The full mapped object is stored in `call_jobs.body` (jsonb)
so a call can be replayed/audited.

## 6. Mock mode (testing without Vobiz)

`MOCK_CALLS=true` in `.env` makes the worker **not dial**:

- every dial creates a `calls` row and ends it ~`MOCK_CALL_DURATION`s later,
- outcomes rotate `PTP → NO_PTP → NO_ANSWER → DISPUTE → HARDSHIP → …` (so
  retries and escalations are exercised), and
- connected calls upload a tiny dummy recording to Storage (if configured) so
  signed URLs flow through the export.

The whole import → run → export loop is testable with zero calls.

## 7. Running it

```bash
# 1. Local Supabase up (Postgres + Storage), tables migrated, .env filled:
#    DATABASE_URL, SUPABASE_API_URL, SUPABASE_SECRET_KEY (supabase status)
# 2. Start the server:
.venv/bin/python server.py

# 3. Trigger a batch (mock or real per MOCK_CALLS / VOBIZ creds):
curl -F "file=@/home/sanjay/Downloads/callingv1 - Sheet1.csv" \
     http://localhost:7860/batch/import
curl -X POST http://localhost:7860/batch/<campaign_id>/run
curl    http://localhost:7860/batch/<campaign_id>
curl -o results.csv http://localhost:7860/batch/<campaign_id>/export
```

Results CSV columns: `loanNo, customerName, phone, outcome, outcome_note,
recording (signed URL), call_status, attempts, called_at, call_uuid`.

> A legacy CSV-only CLI (`batch_caller.py --csv …`, in-memory flow) still exists
> for the older single-server behavior; new work should use the API + DB.

## 8. Edge cases & notes

- **Resume**: the worker only claims `pending`/`scheduled` jobs; a crashed run
  simply continues on restart (`claim_next_due_job` is atomic + `SKIP LOCKED`).
- **One campaign runs at a time** (in-process guard); volume is <100/day.
- **Recordings**: MP3s → `recordings` bucket; the CSV gets a **signed URL** that
  expires (`RECORDING_SIGNED_URL_TTL`). Regenerate from `calls.recording_key`.
- **State**: DB is truth; `call_state` resets with the process (WS lookups).
- **Outcome fidelity**: connected-but-no-`log_outcome` calls are marked
  `NO_OUTCOME` — a prompt/eval problem to chase via the evals suite.
- **Calling hours** are not enforced by this layer yet (client guard in the
  legacy CLI) — a production hook: refuse `/batch/*/run` outside 8–19 IST.

## 9. Testing

```bash
.venv/bin/python -m pytest tests/test_batch_db.py tests/test_batch_api.py -q
```

Both skip automatically when local Supabase isn't reachable. What can't be
tested without a Vobiz number: the real dial + WebSocket lifecycle (the mock
covers everything around it).
