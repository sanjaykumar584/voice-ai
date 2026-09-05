# Voice Agent Progress — Pipecat + Vobiz + Sarvam

> Living document. Keep the checklist current and append to the Progress Log as we work.

**Goal:** Outbound reminder/notification voice agent. Bot dials out from a Vobiz number, holds a real-time conversation (STT → LLM → TTS) over a bidirectional WebSocket audio stream, records the call, and reports the outcome.

**Stack:** Pipecat (voice pipeline) · Vobiz (telephony: Voice API + Voice XML + WebSocket streaming) · Sarvam AI (STT / LLM / TTS)

**Starting point:** Official `vobiz-ai/Vobiz-X-Pipecat` repo, customized (chosen by user). We keep its FastAPI + serializer + pipeline wiring and swap OpenAI services for Sarvam.

---

## Architecture

```
Your backend ──POST /start──► Vobiz REST API ──► callee's phone rings
                                   │  (answered)
                                   ▼
                    Vobiz fetches POST /answer  (your FastAPI server)
                                   │  returns Voice XML:
                                   ▼  <Record/> + <Stream> wss://your-server/ws
                    Vobiz opens WebSocket ──start event──►
                                   │
     ┌─────────────────────────────┘
     ▼
FastAPIWebsocketTransport + VobizFrameSerializer  (pipecat-vobiz pkg)
     → SarvamSTT → context aggregator → SarvamLLM → SarvamTTS
     → transport.output() → context aggregator.assistant()
     ──playAudio events──► back to caller; recording → MP3 on /recording-ready
```

Key facts locked in during research:

- Vobiz is India-focused AI telephony (Voice API, Voice XML, SIP, DIDs, WebSocket streaming, ~80 ms carrier leg).
- `pipecat-vobiz` PyPI package provides `VobizFrameSerializer` + `parse_vobiz_start()`.
- Vobiz webhooks: `/answer` returns Voice XML with `<Record>` + `<Stream>` pointing at our `wss://` endpoint.
- Wire format: 8 kHz μ-law (`audio/x-mulaw`) by default; keep `add_wav_header=False`.
- VAD: Silero on `LLMUserAggregatorParams` (NOT on transport — no-op under Pipecat 1.x).
- Pipeline sample rates: in `8000`, out `24000` (matches Sarvam bulbul:v3-beta; v2 is 22050 → must change `audio_out_sample_rate`).
- Sarvam Pipecat services (`pipecat-ai[sarvam]` extra):
  - `SarvamSTTService` — WebSocket, VAD-segmented, modes transcribe/translate/verbatim/translit/codemix.
  - `SarvamLLMService` — OpenAI-compatible, model `sarvam-105b`, base `https://api.sarvam.ai/v1`, function calling supported.
  - `SarvamTTSService` (WS) / `SarvamHttpTTSService` — models bulbul:v2 (22050 Hz) / bulbul:v3-beta (24000 Hz); voices via `SarvamTTSSpeakerV3`.
- Reference repos: `vobiz-ai/Vobiz-X-Pipecat` (our base), `vobiz-ai/Vobiz-Sarvam` (Sarvam example).

---

## Checklist

### Phase 0 — Credentials & prerequisites
- [ ] Confirm `VOBIZ_AUTH_ID` / `VOBIZ_AUTH_TOKEN` (Vobiz Console → Settings → API)
- [ ] Confirm Vobiz phone number provisioned for caller-ID (`VOBIZ_PHONE_NUMBER`)
- [ ] Confirm `SARVAM_API_KEY` + model access (LLM `sarvam-105b`, STT saaras/saarika, TTS bulbul)
- [x] Confirm Python 3.12.3, `uv` 0.12.3, git — present
- [ ] Install `ngrok` (missing; needed for Phase 5 public tunnel)

### Phase 1 — Seed codebase from official example
- [x] Clone `vobiz-ai/Vobiz-X-Pipecat` into workspace (server.py, bot.py, download_recording.py, env.example, requirements.txt)
- [x] Create `.venv`; install `requirements.txt` (extra swapped `openai` → `sarvam`)
- [x] Sanity: `compileall` clean; `import bot` OK; server.py imports fine

### Phase 2 — Configure `.env`
- [x] `env.example` updated for Sarvam (`SARVAM_API_KEY` + model/voice overrides)
- [ ] Copy to `.env` and fill in real keys (`SARVAM_API_KEY`, `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN`, `VOBIZ_PHONE_NUMBER`)
- [ ] `PUBLIC_URL` set once ngrok is up (Phase 5)
- [ ] Never commit real `.env`; keep `env.example` as template

### Phase 3 — Swap OpenAI → Sarvam in `bot.py`
- [x] STT → `SarvamSTTService` (default `saaras:v3`, env-overridable)
- [x] LLM → `SarvamLLMService` (default `sarvam-105b`)
- [x] TTS → `SarvamTTSService` (`bulbul:v3-beta` @ 24000 Hz, voice `priya`, env-overridable)
- [x] Align `PipelineParams.audio_out_sample_rate=24000` with bulbul:v3-beta (comment notes v2 = 22050)
- [x] Keep Vobiz serializer/transport wiring intact (`add_wav_header=False`, μ-law 8 kHz, `pipecat-vobiz` 0.0.3 verified)
- [x] Verified every Sarvam class/settings field against installed package source (pipecat 1.7.0)

### Phase 4 — Make it an outbound reminder bot
- [x] System prompt: reminder script, voice-safe guard, "Begin by saying" opening line
- [x] Thread per-call context: `body` from `/start` → `/answer` → base64 `body=` on WS URL → decoded in server.py → passed to `bot(body_data=…)` → developer message in LLM context
- [x] Function tools: `log_outcome(status, note)` (logs outcome) + `end_call` (graceful `EndWorkerFrame`)
- [x] Keep recording: `<Record>` → MP3 in `recordings/` (unchanged)

### Phase 4.5 — Dev-only browser testing (SmallWebRTC, no Vobiz)
- [x] Add `webrtc` + `runner` extras to requirements (aiortc, opencv, `pipecat-ai-prebuilt` UI); installed
- [x] `bot()` branches on runner args: `SmallWebRTCRunnerArguments` → SmallWebRTCTransport (dev), else Vobiz path
- [x] `run_bot()` transport-aware input sample rate (Vobiz 8000 / browser 16000)
- [x] Bot speaks first via `LLMRunFrame` + `context.add_message()` on connect (both modes)
- [x] `main()` dev-runner entry (`python bot.py` → prebuilt UI at localhost:7860)
- [x] `DEV_REMINDER_BODY` env injects a sample reminder for the browser test
- [x] Verified: dev runner boots (`/status` ready, `/client/` 200); `server.py` still boots clean
- [ ] **Test in browser**: `python bot.py` → open http://localhost:7860 → click start → bot greets → conversation → `[OUTCOME]` tool log fires
- [ ] Regression note: Vobiz phone path unchanged (`python server.py`); full call test waits for a number

### Phase 4B — EMI Collections agent (single system prompt, mirrors Sarvam dashboard)
- [x] `collections_logic.py`: `compute_derived()` (emis_due_till_today / overdue_count / overdue_amount / remaining_tenor / has_arrears) — deterministic, self-tested against the prompt's worked example (14 / 8 / 89,464 / 30)
- [x] Full collections system prompt embedded as template; variables filled from per-call data
- [x] `developer` message seeds variables + computed derived values + today's date (LLM never computes)
- [x] Tools: `log_outcome(status)` → PTP / NO_PTP / NO_ARREARS / DISPUTE / HARDSHIP / DECEASED / SURRENDER / HOSTILE / WRONG_NUMBER; `end_call` kept
- [x] Speak-first greeting = Step 1 identity (no company/loan/amount before confirmation)
- [x] Tamil-only tuning: STT `saaras:v3` + `language=ta-IN`; TTS bulbul:v3-beta + `language=ta-IN`, voice `priya`
- [x] Mock call data in `DEV_REMINDER_BODY` (Kumar / ABC Finance / ₹89,464 arrears)
- [x] Verified: derived self-test OK, compileall clean, prompt fully filled, both modes boot
- [ ] **Browser test the full script**: `python bot.py` → identity → arrears in 3 turns → ladder → PTP logged
- [ ] Phone path regression once Vobiz number arrives
- [ ] (Later) Pipecat Flows for enforced ladder + rung-5 gate + escalation flags

### Phase 4C — Bugfix: greeting name missing + dropped short utterances
- [x] **Root cause (greeting)**: WebRTC dev path passed the raw RTVI `/api/offer` payload as `body_data` — it has no collections fields, so every variable (customer name, company, amounts) filled empty → "Hello, pesreengala?" and incoherent replies
- [x] Fix: `_is_collections_body()` — if the incoming body lacks `first_due_date`+`emi`, fall back to `DEV_REMINDER_BODY` (real `/start` bodies are used as-is)
- [x] Verified: offer payload → mock → greeting now "Hello, Kumar pesreengala?"
- [x] **Root cause (dropped turns)**: Silero VAD drove turn boundaries while Sarvam STT segmented audio server-side; on user-stop, `flush()` was called but could return empty for short clips → first 3 short utterances ("ஹலோ"…) got no response
- [x] Fix: `vad_signals=True` + `high_vad_sensitivity=True` on Sarvam STT so Sarvam's own VAD pairs each transcript with end-of-speech atomically; removed Silero from the aggregator (turn controller handles Sarvam's `UserStarted/StoppedSpeakingFrame` natively)
- [x] Added `keepalive_timeout=10` on STT; `LOG_LEVEL=DEBUG` toggle to surface transcripts/turn frames
- [x] Verified: compile clean, imports OK, both modes boot
- [x] **Retest in browser** (`python bot.py` → localhost:7860) with `LOG_LEVEL=DEBUG`; confirm greeting has the name and short replies get responses
- [x] Retested after body/VAD fixes — replies now come per-utterance but were slow (~3–6s)

### Phase 4D — Latency: reply too slow (~3–6s)
- [x] Diagnosed (NOT WebSockets — transport is ~ms): (1) turn-stop waited on smart-turn analyzer + Sarvam `SARVAM_TTFS_P99=1.17s` safety net; (2) `sarvam-105b` LLM ran at server-default reasoning effort + wiki grounding; (3) TTS buffered 50 chars before first audio
- [x] LLM: `SarvamLLMService.Settings(reasoning_effort="low", wiki_grounding=False, max_tokens=150, temperature=0.5)` — big first-token win for a scripted bot
- [x] Turn-stop: added `SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.8, wait_for_transcript=True)` FIRST in stop strategies (fires ~0.8s after transcript); smart-turn analyzer kept as fallback
- [x] TTS: `SarvamTTSService.Settings(min_buffer_size=12)` — first audio chunk sooner
- [x] Added `MetricsLogger` pipeline processor → logs `[METRICS]` TTFB/TTFA/processing/LLM tokens/TTS chars per turn at INFO
- [x] Verified: compile clean, imports OK, both modes boot
- [ ] **Retest**: measure per-turn latency from `[METRICS]` logs; tune Sarvam STT VAD end-of-speech params (`negative_frames_count`/`negative_frames_window`) if STT-side delay remains

### Phase 5 — First live call (local + ngrok)
- [ ] `ngrok http 7860`; copy URL into `PUBLIC_URL`; restart server
- [ ] `POST /start` to dial a test number with a `body`
- [ ] Observe: rings → WebSocket accepted → `Vobiz start: callId=… mediaFormat=(audio/x-mulaw, 8000)` → bot speaks → conversation
- [ ] Confirm recording lands in `recordings/`
- [ ] Debug any audio issues (byte order, sample rate, STT silence) via Vobiz troubleshooting table

### Phase 6 — Verification
- [ ] Drive calls headlessly; assert on log lines (`[SUCCESS] WebSocket…`, start event, "Outbound call ended")
- [ ] Confirm greeting + reminder text spoken correctly
- [ ] (Optional) Add `pipecat eval` scenarios later for scripted tests

### Phase 7 — Hardening & next steps
- [ ] TRAI/DLT compliance for outbound India calls (calling hours, header/template registration)
- [ ] Retry / answering-machine detection (Vobiz AMD)
- [ ] Persist call state (Redis) + move beyond ngrok (TLS host / Docker)
- [ ] Lock down webhook endpoints (shared secret / signature / IP allowlist)
- [ ] (Optional) Inbound support via Vobiz Application → Answer URL

---

## Progress Log

<!-- Append newest entries at the TOP of this list. Format:
### YYYY-MM-DD — HH:MM — <short title>
- What we did, decisions made, files touched.
- Current status: <Phase> / checklist state.
- Next steps.
-->

### 2026-08-31 — Database design doc + migration
- `architecture/database.md`: full Supabase DB design — table purposes, copy-paste SQL (campaigns / call_jobs / calls / blocklist / escalations / audit_log + indexes), apply instructions, Storage bucket note, flow mapping, integration notes.
- Saved the same schema as `supabase/migrations/0001_batch_calling.sql` (replays via `supabase db reset`).
- Linked from `architecture/architecture.md` + README docs.
- Decisions: local Supabase only, psycopg direct, RLS off (tenant_id ready), recordings → Supabase Storage + signed URLs.

### 2026-08-29 — Batch input CSV made configurable
- `batch_caller.py`: input CSV is no longer hardcoded — resolution order: `--csv` flag → `BATCH_INPUT_CSV` env → built-in Downloads default (`default_csv()`).
- Added `BATCH_INPUT_CSV=` to `.env` + `env.example`, documented in `CONFIG.md`, `architecture/batch-calling.md`, and the README.
- Tests: 3 new `default_csv` cases (48 total passing); verified env override + fallback via dry-run.

### 2026-08-29 — Batch-calling developer doc
- Added `architecture/batch-calling.md`: developer guide for the batch layer — flow diagram, component responsibilities, `call_state`/`app_resources` plumbing, CSV→body mapping table, result columns + `derive_result` branches, run flags, safety/edge cases, endpoints, testing, known limitations.
- Linked from `architecture/architecture.md` and the README docs section.

### 2026-08-29 — Batch calling implemented (plan/batch-calling.md)
- **`call_state.py`** — shared in-process call registry (avoids the server↔bot import cycle).
- **`server.py`**: keeps ended calls in history (`status: "ended"`, `connected`, `outcome`, `outcome_note`, `ended_at`); `POST /start` records phone + body; `/recording-ready` stores `recording_served_url`; new **`GET /calls`** (history with outcome/recording) and **`GET /recordings/{file}`** (path-traversal-guarded MP3 serving).
- **`bot.py`**: `run_bot` accepts `app_resources` → `PipelineTask`; Vobiz path passes `{"call_id": …}`; `log_outcome` writes status/note into `call_state.active_calls` (verified the framework threads `app_resources` into `FunctionCallParams`).
- **`batch_caller.py`**: loads the CSV (`/home/sanjay/Downloads/callingv1 - Sheet1.csv`), maps rows (phone→+91, DD/MM/YYYY→ISO, decimal `pos`→int, `loanNo` last-4), places 1 call at a time via `POST /start`, polls `GET /calls`, writes `outcome/outcome_note/recording/call_status/called_at/call_uuid` back into the same CSV (timestamped backup, atomic rewrite, resume-safe). Flags: `--dry-run`, `--limit`, `--from`, `--force`, `--delay`, `--timeout`, `--poll-interval`. 8AM–7PM IST guard.
- **Tests**: `tests/test_batch_mapper.py` (mapping/phone/date/derive-result/IST) + `tests/test_outcome_store.py` (log_outcome writes to the registry). 45 passing.
- Verified: dry-run maps all 176 rows cleanly; `/calls` + `/recordings` work; traversal guard 404s.
- **README reverted by git flow** → rewrote it (now includes batch calling). Live batch run still blocked on a Vobiz number.

### 2026-08-29 — Architecture docs directory
- Created `architecture/` with two prose guides, each flowing why → what → how (simple) → technical:
  - `architecture/architecture.md` — overall system: problem, components, call flow, pipeline, two run modes, LLM swap, technical reference.
  - `architecture/evals.md` — the test setup: why voice needs automated tests, unit vs behavioral layers, how `pipecat eval` works (RTVI, text/audio modes, DeepSeek judge), scenario map, caveats.
- Root `ARCHITECTURE.md` now points to the directory (kept as the technical reference).

### 2026-08-29 — Test suite + MetricsLogger bugfix
- **Unit tests** (`tests/`, pytest, 30 passing): `test_compute_derived.py` (worked example, no-arrears, due_day edge, malformed input), `test_prompt_build.py` (placeholders filled, greeting name, dev-message values), `test_env_helpers.py` (env parsing, `_is_collections_body`, `_dev_reminder_body`), `test_llm_provider.py` (provider switch, missing-key errors, temp/max_tokens). `pytest.ini` (pythonpath=., testpaths=tests), `requirements-dev.txt` (pytest + `pipecat-ai[cli]`).
- **Behavioral evals** (`pipecat eval`): installed `[cli]` extra; `bot()` now handles `EvalRunnerArguments` → `EvalTransport` + `RTVIProcessor`/`RTVIObserver` (task auto-wires observer + prepends processor via `enable_rtvi`). Body comes via `--runner-body`. Judge = DeepSeek (`judge_factory.deepseek`, default `deepseek-reasoner`, reuses `DEEPSEEK_API_KEY`). 16 scenarios in `server/evals/` (greeting, identity×4, ladder, 6 objections, PTP/NO-PTP, DNC escalation, prohibitions, latency) + `eval_body.json` + `suite.yaml`. All scenarios parse.
- **Fixed a real bug**: `MetricsLogger.process_frame` never called `super()` → never handled `StartFrame` → the "StartFrame not received yet" ERROR spam (was blamed on service failures earlier). Now clean.
- Verified: eval bot boots headless cleanly (0 StartFrame errors, 0 tracebacks, STT/LLM/TTS connect, `[METRICS]` log) with `LLM_PROVIDER=sarvam`; `.env` restored to `deepseek`.
- Next: **user adds `DEEPSEEK_API_KEY` to `.env`**, then:
  - `pytest tests/ -q`
  - two terminals: `python bot.py -t eval --runner-body server/evals/eval_body.json` + `pipecat eval run server/evals/<scenario>.yaml -v` (or `pipecat eval suite server/evals/suite.yaml`)

### 2026-08-26 — LLM provider switch: DeepSeek integration
- Measured sarvam-105b with the real collections prompt: TTFB ~1.1s but 7–19s of invisible reasoning, frequently `finish=length` with **empty content** (the 10s dead air + occasional silence). Confirmed `sarvam-30b`/`sarvam-m` are deprecated → no fast Sarvam chat model exists; a no-chain-of-thought prompt instruction doesn't suppress the reasoning.
- Conclusion: keep Sarvam **STT + TTS**, swap only the LLM to a fast provider.
- Added `LLM_PROVIDER` switch in `bot.py` (`_build_llm()`): `sarvam` (default, existing config) · `deepseek` (`DeepSeekLLMService`, `deepseek-chat`) · `openai` (`OpenAILLMService`, `gpt-4o-mini`). Generic `LLM_TEMPERATURE`/`LLM_MAX_TOKENS`; provider keys read from env with a clear error if missing. DeepSeek `supports_developer_role=False` → pipecat auto-translates our developer message to `system`.
- `.env`/`env.example`/`CONFIG.md`/`ARCHITECTURE.md` updated (`LLM_PROVIDER=deepseek`, `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`).
- Verified: clean error when key missing, DeepSeek service constructs with a dummy key, both modes boot.
- Next: **user adds `DEEPSEEK_API_KEY` to `.env`, runs `python bot.py`, confirms replies are fast (~1-2s)**.

### 2026-08-25 — Bugfix: LLM crash (wiki_grounding) + TTS 422 (min_buffer_size)
- User hit two runtime failures after the config-var wiring: LLM `AsyncCompletions.create() got an unexpected keyword argument 'wiki_grounding'`, and TTS `Input parameters has to be a valid dictionary` (then connection-failed ×3).
- Reproduced BOTH directly against the real Sarvam API (scripts in `/tmp/opencode`; note: `load_dotenv()` searches from the *script's* dir, so repros must pass the `.env` path explicitly — earlier repros 403'd on an empty key, a red herring).
- **LLM:** `wiki_grounding=False` is passed as a Python kwarg to the OpenAI-compatible endpoint, which rejects it → remove entirely (also removed the `SARVAM_LLM_WIKI_GROUNDING` env var). Verified `reasoning_effort="low"` IS accepted and measured ~2× faster (2.0s → 1.1s) → keep. Discovered `sarvam-105b` reasons heavily (hundreds–1000s of tokens) before answering, so `max_tokens=150` truncated it mid-thought → empty replies; made `SARVAM_LLM_MAX_TOKENS` optional (empty = not sent).
- **TTS:** Sarvam's TTS WS rejects `min_buffer_size < 30` with a 422 "Input parameters has to be a valid dictionary". Tested 15/20/25 = fail, 30/40/50 = OK. Changed default 12 → 30.
- `env.example`, `.env`, `CONFIG.md` updated to match (removed wiki_grounding, max_tokens optional, min_buffer_size min 30).
- Verified: compile clean, settings resolve (max_tokens NOT_GIVEN, min_buffer 30), both modes boot.
- Current status: **ready to retest** — greeting + conversation should now work with faster LLM (`reasoning_effort=low`) and working TTS.

### 2026-08-25 — Config guide + all tunables exposed as env vars
- Created `CONFIG.md`: every configurable parameter in plain language — what it does, current value, effect, and experiment range, grouped (LLM / STT / TTS / turn-taking / logging / Vobiz / code-fixed / per-call data).
- Wired previously-hardcoded tunables to env vars (edit `.env`, no code): `SARVAM_LLM_REASONING_EFFORT|WIKI_GROUNDING|MAX_TOKENS|TEMPERATURE`, `SARVAM_STT_VAD_SIGNALS|HIGH_VAD_SENSITIVITY|KEEPALIVE_*` + fine-grained VAD (`MIN_SPEECH_FRAMES`, `FIRST_TURN_MIN_SPEECH_FRAMES`, `NEGATIVE_FRAMES_COUNT|WINDOW`, `START_SPEECH_VOLUME_THRESHOLD`, `POSITIVE/NEGATIVE_SPEECH_THRESHOLD`), `SARVAM_TTS_MIN_BUFFER_SIZE|MAX_CHUNK_LENGTH`, `TURN_SPEECH_TIMEOUT`, `LOG_LEVEL`.
- Fine-grained VAD params only sent when set in `.env` (empty = Sarvam defaults); validated saaras:v3-only.
- **Fixed a latent bug**: `wiki_grounding=Fealse` typo in `bot.py` (would have crashed at call time).
- `env.example` rewritten with the full matrix + comments; `.env` updated with the new vars (current defaults).
- Verified: settings resolve from `.env`, compile clean, both modes boot.
- Next: user experiments via `.env`/`CONFIG.md`; tune STT `negative_frames_*` if end-of-speech feels slow.

### 2026-08-24 — Latency fix: reply time ~3–6s → bounded
- User: "each reply takes too long — is it the WebSockets?" Answer: no — the WebSocket/WebRTC transport is ~ms. Verified in source that the delay stacked three AI-stage latencies.
- **Turn-stop:** the smart-turn stop strategy waits for its analyzer then Sarvam's P99 safety net (`SARVAM_TTFS_P99 = 1.17s`) since Sarvam transcripts aren't `finalized=True`. Added `SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.8, wait_for_transcript=True)` as the FIRST stop strategy (transcript-driven — verified it re-arms on `TranscriptionFrame`, so it works with Sarvam VAD); `LocalSmartTurnAnalyzerV3` kept as fallback.
- **LLM:** `sarvam-105b` was running at server-default reasoning effort + wiki grounding. Set `reasoning_effort="low"`, `wiki_grounding=False`, `max_tokens=150`, `temperature=0.5` (SarvamLLMSettings validated) — the likely single biggest win.
- **TTS:** `min_buffer_size` default 50 → set 12 so Sarvam synthesizes the first chunk sooner (better TTFA).
- **Observability:** added `MetricsLogger` FrameProcessor at pipeline end logging `[METRICS] TTFB/TTFA/Processing <ms>`, LLM token usage, TTS chars at INFO.
- Verified: compileall clean, imports OK, dev runner + server.py both boot.
- Current status: **retest pending** — run `python bot.py`, watch `[METRICS]` to see the per-stage split; if STT-side delay remains, tune Sarvam `negative_frames_count`/`negative_frames_window`.

### 2026-08-24 — Architecture doc added
- Created `ARCHITECTURE.md`: overview, tech stack, high-level + pipeline diagrams, the two run modes, per-call data → prompt flow, Vobiz call lifecycle, file map, env reference, security/compliance notes.

### 2026-08-24 — Bugfix: greeting without name + only every-4th utterance answered
- User's browser test: greeting "Hello, pesreengala?" (no name), and the bot replied only after the 4th short Tamil utterance ("ஹலோ" x3 → "ஹலோ கேட்குதா?"), then derailed ("நீங்க யார்?").
- **Bug 1 (greeting/variables):** the SmallWebRTC (dev) path passed `runner_args.body` — the raw RTVI `/api/offer` payload — into `run_bot`. It's not a collections body, so `build_call_context` got no `customer_name`/amounts → empty name + zeroed variables (also explains the incoherent "who are you?" reply). Fixed with `_is_collections_body()` fallback to `DEV_REMINDER_BODY`; verified greeting now "Hello, Kumar pesreengala?".
- **Bug 2 (dropped short turns):** two VADs were fighting. Silero (on the user aggregator) decided user start/stop, then flushed Sarvam STT on stop; Sarvam's server-side segmentation could return an empty transcript for short clips → turns 1–3 produced no user message → no LLM response. Fixed by switching to Sarvam's own VAD: `vad_signals=True` + `high_vad_sensitivity=True` on `SarvamSTTService`, removed Silero (`LLMUserAggregatorParams()`), added STT keepalive. Sarvam now emits `UserStartedSpeakingFrame`/`UserStoppedSpeakingFrame` + paired transcripts atomically (turn controller handles these natively — verified in source).
- Added `LOG_LEVEL=DEBUG` env toggle to surface STT transcripts and turn frames on the next test.
- Verified: compileall clean, imports OK, dev runner + server.py both boot.
- Current status: **retest pending.** Next: `python bot.py` (optionally `LOG_LEVEL=DEBUG`) → open localhost:7860 → confirm greeting has the name, short replies get responses, ladder runs, `[OUTCOME] PTP` logs.

### 2026-08-24 — EMI Collections agent implemented (single system prompt)
- User supplied the real agent prompt (`prompt.txt`): outbound EMI collections voice agent in colloquial Tamil/Tanglish. Decisions: single system prompt now (mirror Sarvam dashboard), Flows later; **mock static data**; **caller language Tamil only**.
- Mock data (worked-example-based, user confirmed "use mock"): Meena / ABC Finance / Kumar / acct 1234 / principal 371,987 / EMI 11,183 / first_due 2025-07-01 / tenor 36 / received 6 → derived 14 / 8 / 89,464 / 30.
- New `collections_logic.py`:
  - `compute_derived(body, today)` — deterministic emis_due_till_today (month-boundary count, due_day-aware), overdue_count (floor 0), overdue_amount, remaining_tenor, has_arrears. `_selftest()` asserts the prompt's worked example.
  - `COLLECTIONS_SYSTEM_PROMPT` — the user's full script as a template; stripped variables mapped to sensible placeholders (`{customer_name}`, `{company_name}`, `{agent_name}`, `{account_number_last4}`, `{principal}`, `{emi}`, `{first_due_date}`, `{tenor_months}`, `{emis_received}`, + derived). NOTE in code: ambiguous stripped spots (e.g. §7.4 disbursed line, §8 full-number prohibition) mapped sensibly — user should review the filled prompt.
  - `build_call_context(body)` → (system_prompt, developer_message) with variables + computed derived + today's date.
- `bot.py`: dropped generic reminder prompt; uses `build_call_context`; `log_outcome` statuses = collections set; STT/TTS set to `language=Language.TA_IN`; speak-first greeting = Step 1 identity.
- `.env`/`env.example`: `DEV_REMINDER_BODY` = the mock collections payload.
- Verified: selftest OK; compileall clean; prompt fully filled (no leftover placeholders; "8 EMI pending … 89464", "Hello, Kumar pesreengala?" correct); dev runner + server.py both boot.
- Current status: **ready for the browser run of the full collections script.** Next: `python bot.py`, open http://localhost:7860, walk the script (identity → arrears → ladder → PTP), check `[OUTCOME]` logs; then buy Vobiz number for the phone path.

### 2026-08-24 — Dev-only browser testing wired up (SmallWebRTC)
- Requirement: test the Sarvam pipeline without a Vobiz number; quick switch between browser and phone; browser mode is dev-only.
- Discovered the Pipecat **dev runner** (`python bot.py` via `pipecat.runner.run.main()`) already serves the prebuilt UI at localhost:7860 and selects transports via `-t`/`/start` — used it instead of hand-rolling a WebRTC server.
- `requirements.txt`: extra → `pipecat-ai[websocket,sarvam,silero,webrtc,runner]`. Installed `aiortc`, `opencv-python-headless`, `pipecat-ai-prebuilt` (the UI).
- `bot.py`:
  - `bot()` branches: `isinstance(runner_args, SmallWebRTCRunnerArguments)` → `SmallWebRTCTransport(webrtc_connection, TransportParams(audio_in_enabled=True, audio_out_enabled=True))`; otherwise the unchanged Vobiz path. No config flags needed.
  - `run_bot()` gained `audio_in_sample_rate` (8000 Vobiz / 16000 browser) and now **speaks first** on connect: `context.add_message(developer "begin greeting…")` + `await task.queue_frames([LLMRunFrame()])` (verified `PipelineTask.queue_frames` exists).
  - Added `main()` → `from pipecat.runner.run import main; main()`.
  - Added `DEV_REMINDER_BODY` (dev-only JSON) so the browser test exercises the real reminder flow; set a sample in `.env`.
- Verified: `compileall` clean; imports OK; `python bot.py -t webrtc` boots → `/status` ready + `/client/` 200; `python server.py` boots → `/active-calls` responds. (First pkill attempt self-matched the shell — cleaned up by PID; port 7860 confirmed free.)
- Current status: **ready for the browser test**. Next: user runs `python bot.py`, opens http://localhost:7860, talks to the bot; then buy a Vobiz number to test the phone path.

### 2026-08-24 — Phase 1–4: codebase seeded + swapped to Sarvam
- Cloned `vobiz-ai/Vobiz-X-Pipecat` (commit: latest on master) into the workspace root; files: `server.py`, `bot.py`, `download_recording.py`, `env.example`, `requirements.txt`, `README.md`, `LICENSE`, `.gitignore`.
- Created `.venv`; installed deps. `requirements.txt` extra changed from `openai` → `sarvam` (`pipecat-ai[websocket,sarvam,silero]>=1.2.0,<2`). Pipecat 1.7.0 + pipecat-vobiz 0.0.3 + sarvamai installed.
- Verified APIs against installed package source (pipecat 1.7.0):
  - Sarvam imports are submodules: `pipecat.services.sarvam.{stt,llm,tts}` (package `__init__` is empty).
  - STT models: `saarika:v2.5`, `saaras:v2.5`, `saaras:v3` (default). TTS: `bulbul:v2` (22050 Hz) / `bulbul:v3-beta` (24000 Hz). LLM: `sarvam-105b` only.
  - TTS `sample_rate` is a constructor arg (NOT a Settings field).
  - `FunctionCallParams` lives at `pipecat.services.llm_service` (NOT `pipecat.functions`); `EndWorkerFrame` confirmed in `pipecat.frames.frames`.
- `bot.py` rewritten: Sarvam STT/LLM/TTS, outbound-reminder system prompt (voice-safe guard + greeting), per-call reminder details as a `developer` message, and two tools — `log_outcome(status, note)` and graceful `end_call` (pushes `EndWorkerFrame`). Models/voice env-overridable via `SARVAM_*`.
- `server.py`: wired decoded `body_data` from the WebSocket `body=` query param through to `bot(body_data=…)` (it was previously decoded but never passed on).
- `env.example`: replaced OpenAI block with `SARVAM_API_KEY` + `SARVAM_STT_MODEL`, `SARVAM_TTS_MODEL`, `SARVAM_TTS_SAMPLE_RATE`, `SARVAM_VOICE` defaults.
- Sanity: `compileall` clean; `bot.py` imports OK; serializer `InputParams` fields and `WebSocketRunnerArguments` verified.
- Current status: Phase 1–4 code complete. **Blocked on Phase 5 (first live call): need real credentials + ngrok.** Next steps: user provides `SARVAM_API_KEY`, `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN`, `VOBIZ_PHONE_NUMBER`; install ngrok; then run server + tunnel + test call.

### 2026-08-24 — Kickoff
- User request: build a voice agent with Pipecat + Vobiz.
- Research done (Pipecat docs/context-hub + Vobiz docs): identified `pipecat-vobiz` serializer, official `Vobiz-X-Pipecat` example, and Sarvam's Pipecat services.
- Decisions (from user): outbound reminder/notification calls · outbound only · start from official Vobiz-X-Pipecat repo · Sarvam for STT/LLM/TTS · keys in hand · local + ngrok first.
- Created this PROGRESS.md with the full plan, checklist, and log.
- Current status: Phase 0 (prerequisites confirmation pending). Next: confirm credentials & tooling, then Phase 1 (clone repo).
