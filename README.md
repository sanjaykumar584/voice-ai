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
.venv/bin/python bot.py -t webrtc
```

Open **http://localhost:7860**, click start, and talk to the bot. The mock
customer (from `DEV_REMINDER_BODY`) is Kumar with ₹89,464 overdue. Watch the
`[METRICS]` lines in the terminal for per-turn latency.

---

## Run modes

| Mode | Command | What it does |
|---|---|---|
| **Browser dev** | `.venv/bin/python bot.py -t webrtc` | Talk to the bot at `http://localhost:7860` — no Vobiz, no phone |
| **Phone (Vobiz)** | `.venv/bin/python server.py` + ngrok | Make real outbound calls (needs a Vobiz number) |
| **Behavioral evals** | `.venv/bin/python bot.py -t eval` + `pipecat eval run …` | Headless scripted conversation tests |
| **Unit tests** | `.venv/bin/python -m pytest tests/ -q` | Fast logic tests (no keys, no network) |

### Phone mode (real calls)

1. Buy a number on [Vobiz](https://vobiz.ai) and set `VOBIZ_PHONE_NUMBER` in `.env`
   (plus `VOBIZ_AUTH_ID` / `VOBIZ_AUTH_TOKEN`).
2. Start the server and expose it:

   ```bash
   .venv/bin/python server.py     # terminal 1 — FastAPI on :7860
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
   Recordings land in `recordings/`.

> `.venv/bin/python bot.py` and `.venv/bin/python server.py` both use port
> **7860** — run one at a time.

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
.venv/bin/python bot.py -t eval --runner-body server/evals/eval_body.json

# Terminal 2 — drive a scenario
# (PYTHONPATH so the DeepSeek judge factory is importable)
PYTHONPATH=server/evals .venv/bin/pipecat eval run server/evals/collections_greeting.yaml -v

# …or run the whole suite (fresh bot per scenario)
PYTHONPATH=server/evals .venv/bin/pipecat eval suite server/evals/suite.yaml
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
| `PUBLIC_URL` | ngrok URL for Vobiz webhooks (phone mode) |
| `DEV_REMINDER_BODY` | Mock customer for the browser test |
| `LOG_LEVEL` | `INFO` (+`[METRICS]`) · `DEBUG` (transcripts/turn frames) |

---

## Project structure

```
├── bot.py                 # Pipeline, transports (webrtc/vobiz/eval), tools, greeting
├── server.py              # Vobiz webhook host: /start, /answer, /ws, /recording-*
├── collections_logic.py   # The collections script (prompt template) + overdue math
├── download_recording.py  # Recording download helper
├── batch_caller.py        # (planned — see plan/batch-calling.md)
├── tests/                 # pytest unit tests
├── server/evals/          # Behavioral eval scenarios + judge factory + suite
├── architecture/          # Plain-English docs: architecture.md, evals.md
├── CONFIG.md              # Every tunable explained
├── requirements.txt       # Production deps
└── requirements-dev.txt   # Test deps (pytest, pipecat CLI)
```

## Docs

- [`architecture/architecture.md`](architecture/architecture.md) — why/what/how → technical walkthrough
- [`architecture/evals.md`](architecture/evals.md) — how testing works
- [`CONFIG.md`](CONFIG.md) — configuration reference
- [`PROGRESS.md`](PROGRESS.md) — build log + checklist

## License

MIT — see [LICENSE](LICENSE).
