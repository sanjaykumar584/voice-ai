# Outbound EMI Collections Voice Agent (Pipecat + Vobiz + Sarvam/DeepSeek)

An **outbound voice agent** that calls customers about overdue EMIs, works a
strict collections script in **colloquial Tamil / Tanglish**, secures a dated
payment commitment, logs the outcome, and records the call.

Built on:

- **[Pipecat](https://pipecat.ai)** — the real-time voice pipeline (STT → LLM → TTS)
- **[Vobiz](https://vobiz.ai)** — telephony: places the call, streams bidirectional
  audio over WebSocket, records it
- **[Sarvam AI](https://sarvam.ai)** — STT (`saaras:v3`) and TTS (`bulbul:v3-beta`),
  Tamil-first
- **DeepSeek** — the conversation LLM (`deepseek-chat`, swappable)

---

## Quick start (browser test — no phone number needed)

```bash
# 1. Install (one-time)
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt

# 2. Configure
cp env.example .env
#   → set SARVAM_API_KEY (STT + TTS)
#   → set DEEPSEEK_API_KEY (LLM) and keep LLM_PROVIDER=deepseek

# 3. Run — browser voice test at http://localhost:7860
.venv/bin/python -m app.bot -t webrtc
```

Open **http://localhost:7860**, click start, and talk to the bot. The mock
customer (from `DEV_REMINDER_BODY`) is Kumar with ₹89,464 overdue. Watch the
`[METRICS]` lines in the terminal for per-turn latency.

---

## Run modes

| Mode | Command | What it does |
|---|---|---|
| **Browser dev** | `.venv/bin/python -m app.bot -t webrtc` | Talk to the bot at `http://localhost:7860` — no Vobiz, no phone |
| **Phone (Vobiz)** | `.venv/bin/python -m app.server` + ngrok | Make real outbound calls (needs a Vobiz number) |
| **Batch calling** | `POST /batch/*` API | Upload a CSV, worker dials it, results back (Supabase) |
| **Behavioral evals** | `.venv/bin/python -m app.bot -t eval` + `pipecat eval run …` | Headless scripted conversation tests |
| **Unit tests** | `.venv/bin/python -m pytest tests/ -q` | Fast logic tests (no keys, no network) |

### Phone mode (real calls)

1. Buy a number on [Vobiz](https://vobiz.ai) and set `VOBIZ_PHONE_NUMBER` in `.env`
   (plus `VOBIZ_AUTH_ID` / `VOBIZ_AUTH_TOKEN`).
2. Start the server and expose it:

   ```bash
   .venv/bin/python -m app.server     # terminal 1 — FastAPI on :7860
   ngrok http 7860                # terminal 2 — public tunnel
   # copy the https:// URL into PUBLIC_URL in .env, restart server.py
   ```

3. Place a call:

   ```bash
   curl -X POST http://localhost:7860/start \
     -H "Content-Type: application/json" \
     -d '{
       "phone_number": "+91…",
       "body": {
         "agent_name": "Meena",
         "company_name": "ABC Finance",
         "customer_name": "Kumar",
         "account_number_last4": "1234",
         "principal": 371987,
         "emi": 11183,
         "first_due_date": "2025-07-01",
         "tenor_months": 36,
         "emis_received": 6
       }
     }'
   ```

   Live call state: `curl http://localhost:7860/active-calls`
   Full history (outcome + recording): `curl http://localhost:7860/calls`
   Recordings land in `recordings/` and are served at `/recordings/<file>`.

> `.venv/bin/python -m app.bot` and `.venv/bin/python -m app.server` both use port
> **7860** — run one at a time.

### Batch calling (spreadsheet → calls → results, via API + Supabase)

State lives in **Supabase Postgres** (tables in `supabase/migrations/`); the
CSV is import/export only. Setup:

```bash
supabase start    # local Supabase (Postgres; tables in supabase/migrations/)
supabase status   # → copy DATABASE_URL into .env
```

Then, with `server.py` running, trigger a batch over HTTP:

```bash
# 1. Upload the spreadsheet → campaign + jobs created (blocklist applied)
curl -F "file=@callingv1 - Sheet1.csv" http://localhost:7860/batch/import
# → {"campaign_id": "...", "imported": 176, "blocked": 0}

# 2. Fire the calls (background worker, one at a time)
curl -X POST http://localhost:7860/batch/<campaign_id>/run

# 3. Watch progress
curl http://localhost:7860/batch/<campaign_id>

# 4. Download the results CSV (outcome, note, signed recording URL…)
curl -o results.csv http://localhost:7860/batch/<campaign_id>/export
```

- Retries: `NO_ANSWER` auto-reschedules next day up to `max_attempts`; dial
  errors retry in 10 min.
- Escalation outcomes (HARDSHIP/DECEASED/SURRENDER/HOSTILE/DISPUTE) land in the
  `escalations` table.
- Recordings **stay on Vobiz** — the DB/CSV carry `recording_id` + the Vobiz
  `recording_url` (fetch any MP3 with `scripts/download_recording.py`).
- `MOCK_CALLS=true` in `.env` simulates calls (no dialing) — test the whole
  flow locally without a Vobiz number.
- Details: [`architecture/batch-calling.md`](architecture/batch-calling.md) +
  [`architecture/database.md`](architecture/database.md).

A legacy CSV-only CLI (`scripts/batch_caller_cli.py --csv …`, in-memory flow) also exists.

---

## Testing

### Unit tests (fast, no keys)

```bash
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q
```

### Behavioral evals (real bot, real APIs)

```bash
# Terminal 1 — the bot in eval mode (headless, waits on ws://localhost:7860)
.venv/bin/python -m app.bot -t eval --runner-body evals/eval_body.json

# Terminal 2 — drive a scenario
# (PYTHONPATH so the DeepSeek judge factory is importable)
PYTHONPATH=evals .venv/bin/pipecat eval run evals/collections_greeting.yaml -v

# …or run the whole suite (fresh bot per scenario)
PYTHONPATH=evals .venv/bin/pipecat eval suite evals/suite.yaml
```

Requires `SARVAM_API_KEY` + `DEEPSEEK_API_KEY`. The judge (for `eval:` criteria)
is DeepSeek `deepseek-reasoner`, reusing your key. See
[`architecture/evals.md`](architecture/evals.md) for how it works and the full
scenario list.

---

## Configuration

Everything is a `.env` knob — explained with plain-language effect and tuning
ranges in **[`CONFIG.md`](CONFIG.md)**: LLM provider/model/effort, STT VAD
sensitivity, TTS buffer, turn timeout, wire format, logging.

Key ones:

| Var | Purpose |
|---|---|
| `SARVAM_API_KEY` | STT + TTS (required) |
| `LLM_PROVIDER` | `deepseek` (default) · `sarvam` · `openai` |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | DeepSeek LLM (default `deepseek-chat`) |
| `VOBIZ_AUTH_ID` / `VOBIZ_AUTH_TOKEN` / `VOBIZ_PHONE_NUMBER` | Telephony (phone mode) |
| `PUBLIC_URL` | ngrok URL for Vobiz webhooks + served recording URLs (phone mode) |
| `DEV_REMINDER_BODY` | Mock customer for the browser test |
| `LOG_LEVEL` | `INFO` (+`[METRICS]`) · `DEBUG` (transcripts/turn frames) |
| `DATABASE_URL` / `SUPABASE_API_URL` / `SUPABASE_SECRET_KEY` | Supabase Postgres + Storage (batch calling) |
| `MOCK_CALLS` | `true` = simulate calls (no dialing) for local testing |

---

## Project structure

```
├── bot.py / server.py      # Thin launchers (dev runner + webhook host)
├── app/                    # The application package
│   ├── main.py             # FastAPI app factory (create_app) + /healthz
│   ├── config.py           # Env parsing + bootstrap (dotenv, LOG_LEVEL)
│   ├── voice/              # The agent: services, pipeline, tools, transports,
│   │                       #   metrics_logger, collections (prompt + math)
│   ├── telephony/          # Vobiz adapter: vobiz.py (REST), router.py
│   │                       #   (webhooks), ws.py (WebSocket lifecycle)
│   ├── calls/              # repo.py (Supabase SQL), registry.py (live-WS
│   │                       #   cache), router.py (/calls REST surface)
│   ├── batch/              # api.py (/batch/*), runner.py (worker + retries),
│   │                       #   dialer.py (real/mock), mapper.py (CSV mapping)
│   (recordings stay on Vobiz — no local/storage module)
├── evals/                  # Behavioral eval scenarios + judge factory + suite
├── scripts/                # Legacy CLI (batch_caller_cli) + download_recording
├── tests/{unit,integration}/ # pytest suites (DB tests skip w/o Supabase)
├── supabase/migrations/    # DB schema (0001_batch_calling.sql)
├── architecture/           # Plain-English docs
├── plan/                   # Internal planning docs (gitignored)
├── CONFIG.md               # Every tunable explained
├── requirements*.txt       # Dependencies
```

## Docs

- [`architecture/architecture.md`](architecture/architecture.md) — why/what/how → technical walkthrough
- [`architecture/evals.md`](architecture/evals.md) — how testing works
- [`architecture/batch-calling.md`](architecture/batch-calling.md) — the spreadsheet → calls → results flow
- [`architecture/database.md`](architecture/database.md) — Supabase schema (copy-paste SQL)
- [`CONFIG.md`](CONFIG.md) — configuration reference
- [`PROGRESS.md`](PROGRESS.md) — build log + checklist

## License

MIT — see [LICENSE](LICENSE).
