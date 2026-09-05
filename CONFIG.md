# Configuration Guide — Outbound EMI Collections Voice Agent

> Every knob you can turn, what it does in plain words, the current value, and
> how to change it. **Where a parameter is an env var, edit `.env` and restart
> the bot — no code changes.** Where it's "code", change `bot.py` (and it's
> clearly marked).
>
> Tip: to see the effect of a change, watch the `[METRICS]` lines in the
> terminal (they show per-turn STT/LLM/TTS latency in ms). `LOG_LEVEL=DEBUG`
> additionally prints every transcript + turn frame (noisy, for diagnosis).

---

## 1. How to experiment (quick workflow)

1. Edit a value in `.env`.
2. Restart the bot (`Ctrl+C`, then `python bot.py -t webrtc`).
3. Talk to it at `http://localhost:7860` and watch the `[METRICS]` lines.
4. Change one value at a time so you know what moved the needle.

---

## 2. LLM (the "brain" — provider-switchable)

Sarvam **STT + TTS stay Sarvam**; only the LLM is swappable via `LLM_PROVIDER`.

| Parameter | Where | Current | What it does | Effect / how to tune |
|---|---|---|---|---|
| `LLM_PROVIDER` | `.env` | `deepseek` | `sarvam` · `deepseek` · `openai`. | **sarvam-105b reasons heavily before answering (7–19s, sometimes nothing) — too slow for real-time voice.** DeepSeek (`deepseek-chat`) is fast (~1–2s) and cheap; OpenAI mini models also work. |
| `LLM_TEMPERATURE` | `.env` | `0.5` | Randomness. `0` = always same, `1` = creative. | Keep 0.3–0.6 for a consistent, professional tone. |
| `LLM_MAX_TOKENS` | `.env` | *(empty)* | Optional cap on reply length. | Leave EMPTY. (For Sarvam, a low cap truncates mid-reasoning → empty replies.) |
| `SARVAM_LLM_REASONING_EFFORT` | `.env` | `low` | Sarvam-only: how hard the model thinks (`low`/`medium`/`high`). | Only used when `LLM_PROVIDER=sarvam`. `low` ≈ 2× faster. |
| `DEEPSEEK_API_KEY` | `.env` | *(empty)* | Required for `LLM_PROVIDER=deepseek`. | Set it and `LLM_PROVIDER=deepseek` to use DeepSeek. |
| `DEEPSEEK_MODEL` | `.env` | `deepseek-chat` | DeepSeek model. | `deepseek-chat` (V3) is the fast general model. |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | `.env` | *(empty)* / `gpt-4o-mini` | Required for `LLM_PROVIDER=openai`. | Set both to use OpenAI. |

> ⚠️ `wiki_grounding` is **not** supported by Sarvam's OpenAI-compatible endpoint
> (crashes the LLM) — there is intentionally no option for it.

---

## 3. STT (Sarvam speech-to-text)

| Parameter | Where | Current | What it does | Effect / how to tune |
|---|---|---|---|---|
| `SARVAM_STT_MODEL` | `.env` | `saaras:v3` | Speech→text model. | `saaras:v3` (best, VAD-aware) · `saarika:v2.5` (simpler) · `saaras:v2.5` (auto-translate). Change only if you hit accuracy issues. |
| `SARVAM_STT_VAD_SIGNALS` | `.env` | `true` | Let Sarvam's own VAD decide when you start/stop talking. | Keep `true` — it fixed dropped short utterances. `false` re-enables a Pipecat-side VAD (Silero) + flush, which dropped short clips. |
| `SARVAM_STT_HIGH_VAD_SENSITIVITY` | `.env` | `true` | Hear softer/quieter speech. | `true` = better for soft speakers, but background noise may count as "speech". |
| `SARVAM_STT_KEEPALIVE_TIMEOUT` | `.env` | `10` | Seconds of silence before the STT connection sends a keep-alive. | Leave 10–30. Empty = disabled. |
| `SARVAM_STT_KEEPALIVE_INTERVAL` | `.env` | `5` | How often it re-checks idle time. | Leave 5. |

**Fine-grained VAD (saaras:v3 only — leave EMPTY to use Sarvam's defaults):**

| Parameter | Current | What it does | When to change it |
|---|---|---|---|
| `SARVAM_STT_MIN_SPEECH_FRAMES` | *(empty)* | Minimum audio needed before a segment counts as speech. | **Dropped short replies** → lower it (e.g. 5–15). Units are audio frames. |
| `SARVAM_STT_FIRST_TURN_MIN_SPEECH_FRAMES` | *(empty)* | Same, but for the very first utterance of the call. | If the first "hello/yes" is missed → lower it. |
| `SARVAM_STT_NEGATIVE_FRAMES_COUNT` | *(empty)* | How many silent frames end a segment. | **Reply feels slow (bot waits too long to hear "I'm done")** → lower it and the window below. |
| `SARVAM_STT_NEGATIVE_FRAMES_WINDOW` | *(empty)* | The window those silent frames are counted in. | Lower = faster end-of-speech, but a mid-sentence pause can cut the caller off. |
| `SARVAM_STT_START_SPEECH_VOLUME_THRESHOLD` | *(empty)* | Loudness needed to start a segment. | Lower = catches quiet speakers; too low = noise triggers it. |
| `SARVAM_STT_POSITIVE_SPEECH_THRESHOLD` | *(empty)* | Confidence to *start* treating audio as speech. | Lower = more sensitive. |
| `SARVAM_STT_NEGATIVE_SPEECH_THRESHOLD` | *(empty)* | Confidence to *stop* treating audio as speech. | Higher = ends faster. |

> ⚠️ These params are only valid for `saaras:v3`. If you change `SARVAM_STT_MODEL`
> and set any of them, the bot will error on start — leave them empty.

---

## 4. TTS (Sarvam text-to-speech)

| Parameter | Where | Current | What it does | Effect / how to tune |
|---|---|---|---|---|
| `SARVAM_TTS_MODEL` | `.env` | `bulbul:v3-beta` | Text→speech model. | `bulbul:v3-beta` (24 kHz, many voices) · `bulbul:v2` (22.05 kHz). If you switch to v2, also set `SARVAM_TTS_SAMPLE_RATE=22050`. |
| `SARVAM_TTS_SAMPLE_RATE` | `.env` | `24000` | Audio quality rate. | Must match the model (v3=24000, v2=22050). |
| `SARVAM_VOICE` | `.env` | `priya` | The voice. | v3 voices: `aditya, priya, neha, rahul, pooja, rohan, simran, kavya, …`. Try a couple — pick what sounds right for your customer base. |
| `SARVAM_TTS_MIN_BUFFER_SIZE` | `.env` | `30` | Characters collected before TTS starts speaking. | **Lower = faster first audio.** Sarvam rejects values below **30** (422 error) — keep 30–50. 30 is the sweet spot for snappy replies. |
| `SARVAM_TTS_MAX_CHUNK_LENGTH` | `.env` | `150` | Max characters per synthesis chunk. | Higher = fewer requests, more buffering. Leave ~150. |

---

## 5. Turn-taking / reply latency

| Parameter | Where | Current | What it does | Effect / how to tune |
|---|---|---|---|---|
| `TURN_SPEECH_TIMEOUT` | `.env` | `0.8` | Seconds of silence (after your last transcript) before the bot replies. | **Lower = snappier** (0.5–0.6) but risks cutting off a caller who pauses mid-thought. Higher (1.0–1.5) = safer, slightly slower. |

Fixed in code (not a knob): the smart-turn analyzer remains a **fallback** stop
strategy, and replies always wait for a transcript (`wait_for_transcript=True`)
so the bot never answers an empty/partial sentence.

---

## 6. Logging & dev

| Parameter | Where | Current | What it does | Effect / how to tune |
|---|---|---|---|---|
| `LOG_LEVEL` | `.env` | `INFO` | `INFO` = normal + `[METRICS]` latency lines. `DEBUG` = also prints every transcript and turn frame. | Use `DEBUG` when diagnosing STT/turn problems; it's very noisy for normal use. |
| `DEV_REMINDER_BODY` | `.env` | mock (Kumar) | Fake call data used by the **browser** test (there's no Vobiz `/start` in that mode). | Change to test different customers/amounts without making a real call. A real `/start` body overrides it. |
| `BATCH_INPUT_CSV` | `.env` | *(empty → built-in)* | The input spreadsheet `batch_caller.py` reads. | Point it at whichever CSV you're calling from (`callingv1 - Sheet1.csv` etc.). The `--csv` CLI flag overrides it. |

---

## 7. Vobiz telephony (phone mode only — `python server.py`)

| Parameter | Current | What it does |
|---|---|---|
| `VOBIZ_AUTH_ID` / `VOBIZ_AUTH_TOKEN` | *(set)* | Vobiz API credentials (Console → Settings → API). |
| `VOBIZ_PHONE_NUMBER` | *(empty)* | The number calls are placed **from** (caller-ID). Needed to test the phone path. |
| `VOBIZ_ENCODING` | `audio/x-mulaw` | Audio wire format. `audio/x-mulaw` or `audio/x-l16`. |
| `VOBIZ_SAMPLE_RATE` | `8000` | Wire sample rate. `8000`/`16000` (24000 unreliable in some regions). |
| `VOBIZ_L16_ENDIAN` | `le` | Byte order if using `audio/x-l16`. |
| `ENV` | `local` | `local` (ngrok) or `production`. |
| `PUBLIC_URL` | ngrok URL | Public https URL Vobiz calls for webhooks. |
| `VOBIZ_PROD_WS_URL` | *(empty)* | Only for `ENV=production`. |
| `TRANSFER_AGENT_NUMBER` | *(empty)* | Number for human-transfer (if you use it). |
| `ENABLE_RECORDING` | `true` | Record calls. |
| `MAX_RECORDING_LENGTH` | `3600` | Max recording length (seconds). |
| `AGENT_NAME` / `ORGANIZATION_NAME` | *(empty)* | Only for Pipecat Cloud production. |

---

## 8. Fixed in code (change `bot.py` — no env var)

These are intentionally tied to the transport/script and don't have env vars:

| Setting | Current | Why it's fixed |
|---|---|---|
| Audio input rate | `16000` (browser) / `8000` (Vobiz) | Each transport's native rate; set in `bot()` per mode. |
| Audio output rate | `24000` | Must match `SARVAM_TTS_SAMPLE_RATE` (bulbul v3). |
| STT language | `ta-IN` | Caller speaks Tamil (your requirement). |
| TTS language | `ta-IN` | Tamil voice output. |
| `wait_for_transcript` | `true` | Never reply to an empty transcript. |
| Smart-turn fallback | enabled | Backup "user finished talking" detector. |
| Tools (`log_outcome`, `end_call`) | — | The script's reporting + graceful hang-up. |
| Speak-first greeting | on connect | Identity step fires without waiting for the caller. |
| Collections script + variables | — | Lives in `collections_logic.py` (system prompt template + `compute_derived`). |

---

## 9. Per-call data (what you send for each call)

When placing a real call, `POST /start`'s `body` must carry these fields. The bot
computes the rest itself (never asks the LLM to do arithmetic).

| Field | Meaning |
|---|---|
| `agent_name` | Agent/bot name (may be blank — never read aloud). |
| `company_name` | Finance company name. |
| `customer_name` | Customer name (may be blank). |
| `account_number_last4` | Last 4 digits — only ever spoken if the customer asks. |
| `principal` | Full closure amount (NOT the arrears). |
| `emi` | One month's EMI. |
| `first_due_date` | First due date, `YYYY-MM-DD`. |
| `tenor_months` | Total loan tenure in months. |
| `emis_received` | Number of EMIs paid so far. |

**Derived automatically** (`collections_logic.compute_derived`):
`emis_due_till_today`, `overdue_count`, `overdue_amount` (overdue_count × emi),
`remaining_tenor`, `has_arrears`. If `has_arrears` is false, the script logs
`NO ARREARS` and ends politely.

---

## 10. Supabase & batch calling (the `/batch/*` API)

| Parameter | Where | Current | What it does | How to tune |
|---|---|---|---|---|
| `DATABASE_URL` | `.env` | local Supabase Postgres | Where campaigns/jobs/calls live. | From `supabase status` → "URL" under Database. |
| `SUPABASE_API_URL` | `.env` | `http://127.0.0.1:54321` | Base URL for Storage uploads/signing. | From `supabase status` → Project URL. |
| `SUPABASE_SECRET_KEY` | `.env` | *(set from `supabase status`)* | Storage auth (secret key). | From `supabase status` → Authentication Keys → Secret. |
| `RECORDING_BUCKET` | `.env` | `recordings` | Storage bucket for call MP3s (created automatically). | |
| `RECORDING_SIGNED_URL_TTL` | `.env` | `3600` | Seconds a recording link stays valid. | Lower = more secure but links expire sooner. |
| `MOCK_CALLS` | `.env` | *(empty)* | `true` = simulate calls with scripted outcomes — no dialing, no Vobiz. | Use for local testing of the whole flow. |
| `MOCK_CALL_DURATION` | `.env` | `1.5` | Seconds a mock call "lasts". | Lower for faster test runs. |
| `BATCH_POLL_INTERVAL` | `.env` | `2.0` | Seconds between DB polls while waiting for a call. | |
| `BATCH_CALL_TIMEOUT` | `.env` | `600` | Max seconds to wait for one call before marking it `TIMEOUT`. | |
| `BATCH_RETRY_MINUTES_NO_ANSWER` | `.env` | `1440` | When a `NO_ANSWER` is retried (default: next day). | |
| `BATCH_RETRY_MINUTES_DIAL_ERROR` | `.env` | `10` | When a dial failure is retried. | |

Trigger a batch: `POST /batch/import` (upload CSV) → `POST /batch/{id}/run` →
`GET /batch/{id}` → `GET /batch/{id}/export`. See `architecture/batch-calling.md`.

---

*Keep `CONFIG.md` in sync with `env.example` and `bot.py` when you add knobs.*
