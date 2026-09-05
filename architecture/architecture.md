# Architecture — Outbound EMI Collections Voice Agent

> A plain-English-to-technical walkthrough of the whole system.
> Companion files: [`evals.md`](./evals.md) (how we test it),
> [`batch-calling.md`](./batch-calling.md) (spreadsheet → calls → results), and
> [`database.md`](./database.md) (Supabase schema).

---

## 1. Why — the problem this solves

A lender (like a small NBFC or a bank) has customers who miss EMI payments.
Someone has to **call each of them**, remind them, understand *why* they
haven't paid, and get them to **commit to a payment date**. Doing that by hand
means:

- a team of call-centre agents,
- who are inconsistent and expensive,
- and can't be scaled up when hundreds of loans go overdue at once.

A **voice AI agent** can make those calls 24/7, follow the same script every
time, never get tired or angry, and hand off to a human only when it must
(hardship, disputes, "stop calling me").

This project is that agent — for **outbound EMI reminder/collections calls**
in **colloquial Tamil/Tanglish**, for Indian phone numbers.

---

## 2. What it is

A program that:

1. **Places a phone call** to a customer (or you test it in a browser).
2. **Talks to them** — verifies who they are, tells them how much is overdue,
   works a negotiation ladder to get a payment commitment, and handles common
   objections (no salary yet, hospitalised, dispute, anger).
3. **Records the outcome** (PTP / NO PTP / HARDSHIP / DISPUTE / DECEASED / …)
   so a real backend can act on it.
4. **Records the call** for compliance.

It's built from three pieces working together:

| Piece | Job | Analogy |
|---|---|---|
| **Pipecat** | The voice pipeline that ties everything together | The "call-centre switchboard + agent coach" |
| **Vobiz** | The phone company — dials the number, streams the audio, records | The "telephone line + network" |
| **Sarvam AI** | The ears, brain, and voice: STT, LLM, TTS | "hearing, thinking, speaking" |

> Later we swapped the **brain (LLM)** to DeepSeek because Sarvam's model was
> too slow for real-time talking — see §8.

---

## 3. How it works — the simple version

Think of the agent as a call-centre employee:

```
Customer speaks  ──►  STT (Sarvam)      "hears" the words
                        │
                        ▼
                  LLM (DeepSeek)          "thinks" what to say next
                        │
                        ▼
                  TTS (Sarvam)           "speaks" it aloud
                        │
                        ▼
                Customer hears reply
```

The **Vobiz** phone line connects the customer's phone to this loop. When the
customer hangs up, the bot stops. Every call is the same loop, repeated.

**The trick that makes it "a collections agent" and not a chatbot:**
before the call, the bot is handed a big **script** (a system prompt) that tells
it exactly *how* to behave — verify identity first, never reveal the amount
before that, work the payment ladder rung by rung, never threaten, and so on.
It also gets the **caller's data** (name, EMI, overdue count) pre-computed, so
it never has to do arithmetic.

---

## 4. The flow — end to end

### Phone mode (production)

```
Your backend ──POST /start──► Vobiz REST API ──► customer's phone rings
                                    │  (they answer)
                                    ▼
                     Vobiz calls YOUR server's /answer  (a webhook)
                                    │  your server replies with "Voice XML":
                                    ▼  <Record the call> + <Stream audio to me>
                     Vobiz opens a WebSocket to your bot and
                     streams the customer's audio to it
                                    │
        ┌───────────────────────────┘
        ▼
   Pipecat pipeline (STT → LLM → TTS)  ◄── the "agent"
        │
        ▼  replies streamed back, then the call is recorded + hung up
```

### Browser mode (development)

Same agent, but instead of a phone, you talk through a **browser page** that
uses WebRTC (mic → speaker). No phone number or ngrok needed — great for
testing the conversation while building.

---

## 5. The files

```
pipecat-bot/
├── bot.py                 The agent: builds the pipeline, picks the transport,
│                          speaks first, wires the tools (log_outcome, end_call).
├── server.py              The Vobiz webhook host: /start, /answer (Voice XML),
│                          /ws (audio stream), /recording-*, call transfer.
├── collections_logic.py   The script (system prompt template) + the math that
│                          works out overdue amounts before the call.
├── download_recording.py  Helper to fetch a call recording.
├── architecture/          This guide + the evals guide.
├── server/evals/          Behavioral test scenarios (see evals.md).
├── tests/                 Fast unit tests (pytest).
├── requirements.txt       Production dependencies.
├── requirements-dev.txt   Test-only dependencies.
├── env.example            Template for .env (secrets live in .env, gitignored).
└── CONFIG.md              Every tuning knob explained.
```

---

## 6. The "brain" — the collections script

The conversation isn't free-form. `collections_logic.py` holds your
**collections script** and turns per-call data into the prompt:

```
per-call data (name, EMI, first-due date, tenor, EMIs paid)
        │
        ▼  compute_derived()  ← pure math, unit-tested
   overdue_count = EMIs due so far − EMIs paid
   overdue_amount = overdue_count × EMI
        │
        ▼  build_call_context()
   system message   = the full script (role, language rules, payment ladder,
                      objection handling, hard prohibitions)
   developer message = THIS call's numbers + today's date
```

The script enforces things like:
- **Step order**: identity → state arrears → ladder → close (never skip).
- **Payment ladder**: rung 1 = close fully → … → rung 5 = split an EMI (gated).
- **Language**: everyday spoken Tamil, English loanwords kept in English,
  ≤10-word turns, no fillers.
- **Hard prohibitions**: never threaten arrest/seizure, never reveal the loan
  to a third party, no fabricated charges, etc.

Because the numbers are **computed in code and injected**, the LLM never has to
do arithmetic (which it'd get wrong).

---

## 7. The pipeline — how a turn flows (technical)

Inside `bot.py`, `run_bot()` assembles the Pipecat cascade loop:

```
transport.input()                    # raw audio in (Vobiz 8kHz / browser 16kHz)
  → SarvamSTTService                 # speech → text (saaras:v3, Tamil VAD)
  → LLMContextAggregatorPair.user()  # collects the user's turns
  → LLM (Sarvam/DeepSeek)            # script + tools → next reply text
  → SarvamTTSService                 # text → speech (bulbul:v3-beta, 24kHz)
  → transport.output()               # audio back to the caller
  → LLMContextAggregatorPair.assistant()  # records what the bot actually said
  → MetricsLogger                    # logs [METRICS] TTFB/processing per turn
```

Key behaviours wired in:

- **Speaks first**: on connect, a `developer` message seeds "Begin with
  Step 1 — identity" and an `LLMRunFrame` triggers one LLM run, so the agent
  opens the call instead of waiting.
- **Turn-taking**: Sarvam STT runs its **own VAD** (`vad_signals=True`), so a
  transcript arrives paired with "user stopped speaking" — reliable for short
  Tamil replies. A `SpeechTimeoutUserTurnStopStrategy` (0.8s) bounds reply
  latency; a smart-turn analyzer is the fallback.
- **Tools (function calling)**: the LLM can call `log_outcome(status, note)` to
  record the call result, and `end_call` to hang up gracefully.
- **Sample rates**: in = 8000 Hz (telephony μ-law) or 16000 Hz (browser);
  out = 24000 Hz (Sarvam bulbul v3), resampled to the transport.

---

## 8. The two run modes + the LLM swap

`bot()` picks the transport from the runner args it receives — same agent, three hats:

| Mode | Command | Transport | When |
|---|---|---|---|
| Browser dev | `python bot.py -t webrtc` | SmallWebRTC (WebRTC, prebuilt UI at :7860) | Fast iteration, no phone |
| Phone | `python server.py` + ngrok | FastAPI WebSocket + `VobizFrameSerializer` | Real calls |
| Eval | `python bot.py -t eval --runner-body …` | EvalTransport (RTVI, headless) | Automated tests → see evals.md |

**Why DeepSeek?** `sarvam-105b` reasons for 7–19 seconds (invisible
chain-of-thought) before answering and sometimes returns nothing — too slow
for talking. `LLM_PROVIDER` switches the brain (sarvam | deepseek | openai)
while Sarvam stays for STT + TTS.

---

## 9. Technical reference (quick)

- **Vobiz wire**: audio is base64 μ-law (or L16) at 8kHz in JSON WebSocket
  events; `VobizFrameSerializer` converts to/from Pipecat `AudioRawFrame`s.
- **Vobiz flow**: `POST /start` → call → `/answer` returns Voice XML with
  `<Record>` + `<Stream>` → Vobiz opens the `wss://` stream → `parse_vobiz_start()`
  negotiates the format → pipeline runs → recording callback → MP3 saved.
- **Per-call data**: `body` from `/start` → query param on `/answer` →
  base64 `body=` on the WebSocket URL → decoded in `server.py` → passed to
  `bot(body_data=…)`.
- **Config**: every tunable is an env var — see `CONFIG.md` (LLM effort,
  TTS buffer, turn timeout, VAD knobs, wire format…).
- **Security**: secrets in `.env` (gitignored); webhooks are unauthenticated —
  lock them down before production; the script has built-in compliance
  guardrails (no threats, no third-party disclosure, welfare escalation).
