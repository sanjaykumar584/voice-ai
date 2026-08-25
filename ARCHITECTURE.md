# Architecture — Outbound EMI Collections Voice Agent

> How the whole system fits together: the Pipecat voice pipeline, the two run
> modes (browser dev / Vobiz phone), the collections logic, and the data flow
> for a single call. Living document — keep in sync with the code.

---

## 1. What this is

An **outbound EMI collections voice agent** for Tamil-speaking customers. The bot
dialls out from a Vobiz phone number (or a browser during development), greets the
customer, verifies identity, states the arrears, and negotiates a firm dated
payment commitment using a strict scripted ladder — all in colloquial Tamil /
Tanglish with compliance guardrails.

Built on three pieces:

| Piece | Role |
|---|---|
| **Pipecat 1.7** | Real-time voice pipeline (STT → LLM → TTS), transports, turn-taking, function calling |
| **Vobiz** | Telephony: places the call, streams bidirectional audio over WebSocket (Voice XML `<Stream>`), records the call |
| **Sarvam AI** | All three AI services: STT (`saaras:v3`), LLM (`sarvam-105b`), TTS (`bulbul:v3-beta`) |

---

## 2. Tech stack

### Runtime & framework
- **Python 3.12**, `uv` for the venv
- **`pipecat-ai` 1.7.0** with extras: `websocket` (telephony transport), `sarvam` (services), `silero`, `webrtc` (dev browser), `runner` (dev runner + prebuilt UI)
- **`pipecat-vobiz` 0.0.3** — `VobizFrameSerializer` + `parse_vobiz_start()` (the bridge between Pipecat frames and Vobiz's wire format)

### Services (Sarvam AI + swappable LLM)
| Service | Class | Config |
|---|---|---|
| STT | `SarvamSTTService` | `saaras:v3`, `language=ta-IN`, `vad_signals=True`, `high_vad_sensitivity=True`, keepalive 10s |
| **LLM (swappable)** | `LLM_PROVIDER` env: `sarvam` (default) · `deepseek` · `openai` | Sarvam `sarvam-105b` (reasoning-heavy, slow) OR `DeepSeekLLMService` (`deepseek-chat`) OR `OpenAILLMService` (`gpt-4o-mini`). **DeepSeek recommended for real-time voice.** |
| TTS | `SarvamTTSService` | `bulbul:v3-beta`, `language=ta-IN`, voice `priya`, 24000 Hz, `min_buffer_size=30` (Sarvam rejects <30) |

### Telephony (Vobiz)
- **REST API** (`https://api.vobiz.ai/api/v1`) — place outbound calls, transfer, fetch recordings; auth via `X-Auth-ID` / `X-Auth-Token`
- **Voice XML webhooks** — `/answer` returns `<Record>` + `<Stream>`; recording callbacks hit `/recording-ready`
- **WebSocket media stream** — bidirectional base64 audio, wire format 8 kHz μ-law (or L16), negotiated at call start

### Web / tooling
- **FastAPI + uvicorn** — the Vobiz host (`server.py`) and the Pipecat dev runner
- **Pipecat Prebuilt UI** (`pipecat-ai-prebuilt`) — browser voice test at `http://localhost:7860`
- **ngrok** — public HTTPS tunnel so Vobiz can reach webhooks (phone path)
- **loguru** logging; `LOG_LEVEL=DEBUG` surfaces STT transcripts / turn frames

---

## 3. High-level flow

```
                          ┌──────────────────────────────────────────────┐
                          │              THE BOT PROCESS                 │
                          │                                              │
   OUTBOUND (phone)       │   server.py (FastAPI)      bot.py            │
 ─────────────────        │   ┌───────────────┐        ┌───────────────┐ │
 POST /start ───────────► │   │ /start        │        │               │ │
 (body: customer, EMI,    │   │ /answer (XML) │        │  collections_ │ │
  dates, received, …)     │   │ /ws  ◄── audio│        │  logic.py     │ │
                          │   │ /recording-*  │        │  (prompt +    │ │
   CALLEE phone ◄────────►│   └──────┬────────┘        │   derived)    │ │
   (Vobiz number)         │          │  WebSocket      └──────┬────────┘ │
                          │          ▼   (μ-law 8 kHz)        │          │
                          │   ┌────────────────────────────┐  │          │
                          │   │  Pipecat voice pipeline    │  │          │
                          │   │  ┌────┐   ┌────┐   ┌────┐  │  │          │
                          │   │  │STT │──►│LLM │──►│TTS │  │  │          │
                          │   │  └────┘   └────┘   └────┘  │  │          │
                          │   └────────────────────────────┘  │          │
                          │              ▲        │            │          │
                          │              │  [OUTCOME] log      │          │
                          └──────────────┼────────┼────────────┼──────────┘
                                         │        └────────────┘
   DEV (browser)                         ▼
 ──────────────          python bot.py -t webrtc → prebuilt UI at :7860
   talk to the bot        (same pipeline, no Vobiz; mock body via DEV_REMINDER_BODY)
```

The **same `bot.py` pipeline serves both modes** — `bot()` picks the transport from
the runner args it receives (see §5).

---

## 4. The Pipecat pipeline

Canonical cascade loop, assembled in `run_bot()` (`bot.py`):

```
transport.input()
  → SarvamSTTService                     (user speech → text)
  → LLMContextAggregatorPair.user()      (accumulate user turns)
  → SarvamLLMService                     (collections script + tools)
  → SarvamTTSService                     (text → speech, 24 kHz)
  → transport.output()                   (audio back to caller)
  → LLMContextAggregatorPair.assistant() (record what was spoken)
```

### Context
- **System message** — the full collections script, templated per call (see §6).
- **Developer message** — this call's variables + **computed** derived values +
  today's date (the LLM never does arithmetic).
- **Tools** (function calling, auto-registered via `LLMContext(messages, tools=…)`):
  - `log_outcome(status, note)` — logs `[OUTCOME]` with a status from the script's
    set (`PTP`, `NO_PTP`, `NO_ARREARS`, `DISPUTE`, `HARDSHIP`, `DECEASED`,
    `SURRENDER`, `HOSTILE`, `WRONG_NUMBER`).
  - `end_call` — reports success then pushes `EndWorkerFrame` for a graceful end.

### Turn-taking (the bugfix that matters)
- **Sarvam STT drives turn boundaries** (`vad_signals=True`): Sarvam's own VAD
  detects speech start/stop and broadcasts `UserStartedSpeakingFrame` /
  `UserStoppedSpeakingFrame`, each **paired with its transcript atomically**.
- No separate Silero VAD on the aggregator — a second VAD caused short utterances
  ("ஹலோ") to be flushed before a transcript existed and get dropped.
- The turn controller's default strategies (transcript-driven start +
  `LocalSmartTurnAnalyzerV3` end-of-turn) handle Sarvam's frames natively.
- **Speak-first greeting**: on `on_client_connected`, a `developer` message seeds
  "Begin with Step 1 — Identity" and `task.queue_frames([LLMRunFrame()])` triggers
  one LLM run, so the bot opens the conversation (identity step).

---

## 5. Two run modes (one codebase)

`bot()` in `bot.py` dispatches on `isinstance(runner_args, …)`:

### A. Browser — dev only (`python bot.py -t webrtc`)
| | |
|---|---|
| Transport | `SmallWebRTCTransport` (peer-to-peer WebRTC) |
| UI | Pipecat prebuilt UI at `http://localhost:7860` |
| Sample rate in | 16000 Hz |
| Per-call data | `DEV_REMINDER_BODY` env (mock), since there's no Vobiz `/start` |
| Vobiz needed? | No — only `SARVAM_API_KEY` |

### B. Vobiz phone (`python server.py` + ngrok)
| | |
|---|---|
| Transport | `FastAPIWebsocketTransport` + `VobizFrameSerializer` |
| Entry | `server.py` → Vobiz webhooks → `/ws` WebSocket → `bot()` |
| Sample rate in | 8000 Hz (telephony μ-law) |
| Per-call data | real `body` from `POST /start` |
| Needs | Vobiz Auth ID/Token + a provisioned number, `PUBLIC_URL` (ngrok), keys |

A body is only used as call data if it looks like a collections payload
(`_is_collections_body` — has `first_due_date` + `emi`); otherwise the dev mock is
used. This is what keeps the browser test honest and the phone path strict.

---

## 6. Per-call data → prompt (collections_logic.py)

```
POST /start body (or DEV_REMINDER_BODY)
   ├─ agent_name, company_name, customer_name, account_number_last4
   ├─ principal, emi, first_due_date, tenor_months, emis_received
   │
   ▼  compute_derived(body, today)   ← deterministic, unit-tested
   ├─ emis_due_till_today  = count of monthly due dates up to today
   ├─ overdue_count        = max(0, emis_due − emis_received)
   ├─ overdue_amount       = overdue_count × emi
   ├─ remaining_tenor      = max(0, tenor_months − emis_received)
   └─ has_arrears          = overdue_count > 0   (no arrears → script ends, logs NO_ARREARS)
   │
   ▼  build_call_context(body)
   ├─ system prompt   = COLLECTIONS_SYSTEM_PROMPT.format(**vars)  (full script)
   └─ developer msg   = vars + derived values + today's date
```

Worked example (mock, today 2026-08-24): first_due 2025-07-01, received 6, EMI
11,183 → emis_due 14, overdue_count **8**, overdue_amount **₹89,464**,
remaining_tenor **30**.

The prompt enforces (via LLM instructions — not yet structurally):
identity → arrears in ≤3 turns → payment ladder (rungs 1→5, rung-5 gated) →
close (log PTP only with the customer's own amount+date), plus language rules
(spoken Tamil register, banned-form replacements, forced code-mixing, ≤10-word
turns) and hard prohibitions / human-escalation flags.

---

## 7. Vobiz call lifecycle (phone path)

1. `POST /start {"phone_number", "body", "from_number"}` → Vobiz REST `POST /Call/`
   → `call_uuid`; `body` travels as `body_data=` on the answer URL.
2. Callee answers → Vobiz fetches `POST /answer` → server returns Voice XML:
   `<Record callbackUrl=…/recording-ready>` + `<Stream contentType="audio/x-mulaw;rate=8000">wss://…/ws?body=…</Stream>`.
3. Vobiz opens the WebSocket → sends a `start` event (streamId, callId, negotiated mediaFormat).
4. `bot()` reads it with `parse_vobiz_start()`, builds `VobizFrameSerializer` +
   `FastAPIWebsocketTransport`, runs the pipeline.
5. Audio: caller speech (μ-law → PCM 8 kHz → STT) in; TTS out (24 kHz → resampled → μ-law via `playAudio`).
6. Recording: Vobiz posts to `/recording-ready`; server downloads the MP3 into `recordings/`.
7. Hangup → `on_client_disconnected` → `task.cancel()`.

Wire format details: `VOBIZ_ENCODING` (μ-law default | L16), `VOBIZ_SAMPLE_RATE`
(8000/16000), `VOBIZ_L16_ENDIAN` (be/le); `add_wav_header=False` is mandatory
(telephony frames are raw payloads, not WAV).

---

## 8. File map

| File | Responsibility |
|---|---|
| `bot.py` | Pipeline, transport dispatch (WebRTC vs Vobiz), Sarvam services, tools, greeting, dev-runner entry |
| `server.py` | Vobiz FastAPI host: `/start`, `/answer` (Voice XML), `/ws` media stream, `/recording-*`, transfer endpoints, in-memory `active_calls` |
| `collections_logic.py` | `compute_derived()`, `COLLECTIONS_SYSTEM_PROMPT` template, `build_call_context()` |
| `download_recording.py` | Standalone recording download helper |
| `requirements.txt` | Pinned deps (pipecat-ai extras incl. `sarvam`, `webrtc`, `runner`) |
| `env.example` | Documented env template (never commit real `.env`) |
| `PROGRESS.md` | Living build log + checklist |

---

## 9. Configuration (env)

| Var | Mode | Purpose |
|---|---|---|
| `SARVAM_API_KEY` | both | All three AI services |
| `SARVAM_STT_MODEL` / `SARVAM_TTS_MODEL` / `SARVAM_VOICE` / `SARVAM_TTS_SAMPLE_RATE` | both | Service overrides (defaults: saaras:v3, bulbul:v3-beta, priya, 24000) |
| `VOBIZ_AUTH_ID` / `VOBIZ_AUTH_TOKEN` | phone | Vobiz API + serializer hang-up |
| `VOBIZ_PHONE_NUMBER` | phone | Outbound caller-ID |
| `PUBLIC_URL` | phone | Public https URL for webhooks (ngrok) |
| `ENV` | phone | `local` (tunnel) vs `production` (hosted `wss://`) |
| `VOBIZ_ENCODING` / `VOBIZ_SAMPLE_RATE` / `VOBIZ_L16_ENDIAN` | phone | Wire format |
| `ENABLE_RECORDING` / `MAX_RECORDING_LENGTH` | phone | Recording |
| `TRANSFER_AGENT_NUMBER` | phone | Human-transfer destination |
| `DEV_REMINDER_BODY` | dev | Mock call data for the browser test |
| `LOG_LEVEL=DEBUG` | dev | Surface STT transcripts + turn frames |

---

## 10. Security & compliance notes

- `.env` holds secrets and is gitignored; `env.example` is the committed template.
- Webhook endpoints (`/answer`, `/recording-*`) are unauthenticated — put behind a
  shared secret / IP allowlist before production.
- The script's hard prohibitions (no threats, no asset seizure, no third-party
  disclosure, no fabricated figures) are LLM-enforced today.
- **Roadmap:** enforce the ladder, rung-5 gate, and escalation flags *structurally*
  with Pipecat Flows; gate outbound calls to 8 AM–7 PM IST in `/start`; turn the
  prompt's QA checklist into `pipecat eval` audio-mode scenarios.
