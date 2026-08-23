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
