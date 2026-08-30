# Batch Calling — Spreadsheet → Calls → Results

> Developer guide for `batch_caller.py` and the outcome-capture plumbing.
> Read [`architecture.md`](./architecture.md) first if you're new to the project.

---

## 1. What it is (and why)

The agent makes **one** call per `POST /start`. A collections team doesn't fire
calls one by one — they have a **spreadsheet of customers** (`callingv1 - Sheet1.csv`
in Downloads: loan number, name, phone, EMI, due dates…) and want:

1. a call placed to **every row**,
2. the **outcome** of each call (PTP / NO_PTP / HARDSHIP / DISPUTE / …) written
   **back into the sheet**,
3. the **recording** reachable per row.

Batch calling is the glue that does that: it reads the CSV, drives the existing
phone-mode server one call at a time, and writes the results back.

---

## 2. The end-to-end flow

```
batch_caller.py                  server.py (the bot host)            Vobiz / bot
───────────────                  ───────────────────────             ──────────
load callingv1.csv
  for each row:
    map row → collections body ──► POST /start ──────────────────────► Vobiz dials
                                      │  (call record created in call_state)
    poll GET /calls ◄───────────────  │                              caller answers
                                      │                              bot runs script
                                      ▼                              log_outcome tool
                                 call_state[call]["outcome"] ◄─────────────────┘
                                      │  (recording URL added by /recording-ready)
    call ends → read outcome + recording
    write columns back to the CSV (backup first, atomic rewrite)
```

Two moving pieces make the outcome loop work:

- **`log_outcome` (bot side)** — when the LLM decides the call's result, the
  tool writes `{outcome, outcome_note}` into the shared registry keyed by
  `call_id`.
- **`GET /calls` (server side)** — exposes every call record (status, outcome,
  recording URL) so the batch caller can read the result and continue.

---

## 3. Files & responsibilities

| File | Role |
|---|---|
| `batch_caller.py` | The driver: CSV → calls → results back into the CSV |
| `server.py` | Hosts `/start`, `/calls`, `/recordings`; keeps call history |
| `call_state.py` | The shared in-process registry (`active_calls`) — **the memory between bot and server** |
| `bot.py` | Passes `app_resources={"call_id": …}`; `log_outcome` writes outcomes into `call_state` |
| `tests/test_batch_mapper.py` | Unit tests for the CSV→body mapping + result derivation |
| `tests/test_outcome_store.py` | Unit tests that `log_outcome` writes to the registry |

### Why `call_state.py` exists

`server.py` and `bot.py` run in the **same process** but importing each other
would be a circular import. `call_state.py` is a tiny dependency-free module
both import — `server.py` reads it for `/calls`, `bot.py` writes to it from
`log_outcome`.

### How the call_id reaches the tool

```
server.py: /start → call_uuid stored in call_state
      │
      ▼
bot():   run_bot(app_resources={"call_id": call_uuid})
      │
      ▼
PipelineTask(app_resources=…) → LLM service → FunctionCallParams.app_resources
      │
      ▼
log_outcome: call_state[call_id]["outcome"] = status
```

(Verified in the framework: `PipelineTask(app_resources=…)` is threaded into
every tool handler's `params.app_resources`.)

---

## 4. The CSV mapping (row → collections body)

| CSV column | Body key | Transformation |
|---|---|---|
| `phoneNo` | (not in body) | `normalize_phone`: 10-digit → `+91…`; keeps `+`/`00` prefixes |
| `loanNo` | `account_number_last4` | last 4 characters |
| `customerName` | `customer_name` | stripped (keeps internal double spaces) |
| `bank` | `company_name` | |
| `agentName` | `agent_name` | the bot persona |
| `pos` | `principal` | `int(float(...))` — handles `81144.58` |
| `installmentAmount` | `emi` | `int(float(...))` |
| `emiStartDate` | `first_due_date` | `DD/MM/YYYY` → `YYYY-MM-DD` (the bot's math needs ISO) |
| `tenor` | `tenor_months` | `int(float(...))` |
| `noOfEmisReceived` | `emis_received` | `int(float(...))` |
| `loanNo` (again) | `loanNo` | echoed through for `/calls` correlation + review |

All mapping lives in `row_to_body()` (unit-tested).

---

## 5. Result columns written back

Appended to the **same CSV** after each call (columns added if missing):

| Column | Meaning |
|---|---|
| `outcome` | `PTP` / `NO_PTP` / `DISPUTE` / `HARDSHIP` / `DECEASED` / `SURRENDER` / `HOSTILE` / `WRONG_NUMBER` / `NO_OUTCOME` (from the bot's `log_outcome`) |
| `outcome_note` | The tool's note (e.g. the customer's stated PTP amount + date) |
| `recording` | Served URL: `{PUBLIC_URL}/recordings/<id>.mp3` (localhost fallback) |
| `call_status` | `ENDED` / `NO_ANSWER` / `FAILED` / `TIMEOUT` |
| `called_at` | IST timestamp when the call finished |
| `call_uuid` | Vobiz call UUID (correlates with `/calls`) |

### How `call_status` is derived (`derive_result`)

| `/calls` record | call_status | outcome |
|---|---|---|
| `status: ended` + `outcome` set | `ENDED` | the outcome |
| `status: ended`, connected, no outcome | `ENDED` | `NO_OUTCOME` (answered, never logged — review) |
| `status: ended`, **not** connected | `NO_ANSWER` | — |
| `status: failed` | `FAILED` | — |
| poll timed out | `TIMEOUT` | — |

---

## 6. Running it

Prereq: `server.py` up (phone mode) + a Vobiz number in `VOBIZ_PHONE_NUMBER`.

```bash
# preview — prints every mapped call, places nothing
.venv/bin/python batch_caller.py --dry-run

# place 2 calls then stop (sanity check first)
.venv/bin/python batch_caller.py --limit 2

# full run — 1 call at a time, results written back as it goes
.venv/bin/python batch_caller.py
```

Flags:

| Flag | Default | Purpose |
|---|---|---|
| `--csv` | `BATCH_INPUT_CSV` env → built-in Downloads path | Input CSV |
| `--server` | `http://localhost:7860` | Bot server base URL |
| `--from-number` | `VOBIZ_PHONE_NUMBER` env | Caller-ID |
| `--dry-run` | off | Print mapped calls, place none |
| `--limit N` | none | Stop after N calls |
| `--from N` | 0 | Start at 0-based row index |
| `--force` | off | Allow calls outside 8:00–19:00 IST |
| `--delay` | 2.0 s | Pause between calls |
| `--timeout` | 600 s | Max wait for one call to finish |
| `--poll-interval` | 5.0 s | `/calls` poll cadence |

---

## 7. Safety & edge cases (important)

- **Resume-safe**: rows with a non-empty `outcome` are skipped. Kill it mid-run,
  restart — it continues where it left off.
- **Backup**: a timestamped copy of the CSV is made before the first write;
  every write is atomic (write `.tmp` → `os.replace`).
- **Calling hours**: outside 8:00–19:00 IST it refuses (unless `--force`).
  Compliance for outbound India calls (TRAI/DLT) is the operator's
  responsibility — the bot never calls on its own.
- **No phone** → row skipped (counted in the summary).
- **Start failure / no `call_uuid`** → row marked `FAILED` with the reason.
- **Recording may lag the hangup** — `/recording-ready` fires shortly after;
  the `recording` column is only filled once the server has the file.
- **State is in-memory**: `call_state` resets when `server.py` restarts. If you
  restart the server mid-batch, `POST /start` still works, but previously
  finished calls vanish from `/calls` (their CSV rows are already written —
  that's why the sheet is the source of truth).
- **176 rows ≈ hours** at ~2 min/call, one at a time. Concurrency is
  intentionally 1 (telephony-safe); raise later if Vobiz concurrency allows.

---

## 8. Server endpoints the batch caller relies on

| Endpoint | Purpose |
|---|---|
| `POST /start` | Places the call; body `{phone_number, body, from_number}`; returns `call_uuid` |
| `GET /calls` | History: `{count, calls: [{call_uuid, phone_number, loanNo, status, connected, outcome, outcome_note, recording_id, recording_url, recording_served_url, started_at, ended_at}]}` |
| `GET /recordings/{file}` | Serves the MP3 (path-traversal guarded) |

---

## 9. Testing the batch layer

```bash
.venv/bin/python -m pytest tests/test_batch_mapper.py tests/test_outcome_store.py -q
```

- Mapper: int/date/phone parsing, `row_to_body`, `derive_result` (all branches),
  IST timezone.
- Outcome store: `log_outcome` writes into `call_state` only when a matching
  `call_id` is present; safe otherwise.

**What can't be unit-tested**: the live Vobiz round trip. Dry-run + a
`--limit 2` call to your own phone is the real verification (needs a Vobiz
number — still missing in `.env`).

---

## 10. Known limitations / next steps

- Blocked on a **Vobiz number** for a live end-to-end run.
- `call_state` is in-memory → swap to Redis/DB for production so history
  survives restarts and multi-worker hosts.
- No retry-on-`NO_ANSWER` policy yet (e.g. auto re-call next day) — the sheet's
  `outcome` column is the hook for that.
- `NO_OUTCOME` (answered but nothing logged) is a signal to tune the prompt or
  check the call — the evals suite (`architecture/evals.md`) is the place to
  guard against that class of bug.
