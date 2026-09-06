# Evals — How we test the voice agent

> From "why do we even test this?" to how the harness actually works.
> Companion file: [`architecture.md`](./architecture.md).

---

## 1. Why — you can't "eyeball" a voice bot

A website you can look at and see if it's broken. A voice agent is different:

- it's **audio**, so you'd have to sit through a call to judge it,
- it's **conversational**, so it behaves differently every time,
- and it's **production-critical**: a collections bot that says the wrong
  thing (threatens, reveals a loan to the wrong person) is a compliance
  problem, not just a bug.

So we need **automated checks** that:

- catch regressions the moment we change the prompt or the pipeline,
- prove the script's rules actually hold (identity first, ladder in order,
  no threats, correct outcome logged),
- and run without needing a phone or a human on the line.

That's what this test setup does — in two layers.

---

## 2. What it is — two layers

| Layer | What it tests | Speed | Needs keys? |
|---|---|---|---|
| **Unit tests** (`tests/`, pytest) | Pure logic: the overdue math, the prompt building, env parsing, LLM provider switch | Millis | No |
| **Behavioral evals** (`evals/`, `pipecat eval`) | The *real running bot*: what it says and does in a conversation | Seconds–minutes | Yes (Sarvam + DeepSeek) |

**Rule of thumb:** unit tests check the **ingredients** are right; evals check
the **dish** tastes right.

---

## 3. The simple version — how an eval works

1. You start the **bot in test mode** — it's the real agent, but instead of
   dialling a phone it opens a tiny test server and waits.
2. You (or a script) give it a **scenario**: a short scripted conversation,
   like "user confirms identity, bot should state the arrears".
3. The harness **plays the user's lines** and **watches the bot's replies**.
4. For each reply it checks the expectation — either a **hard check**
   ("a `log_outcome` tool was called") or a **soft check** ("the reply says
   something about 8 EMIs pending"), which a **judge LLM** grades.
5. Pass/fail per scenario. All green = the agent still behaves correctly.

Think of it as a **role-play test with an examiner** (the judge).

---

## 4. Unit tests — the fast layer

Run anytime: `.venv/bin/python -m pytest tests/ -q`

They hit **no network** — they test functions directly.

| File | What it proves |
|---|---|
| `test_compute_derived.py` | The overdue math is right (worked example: 8 EMIs → ₹89,464; no-arrears → ends; bad dates don't crash) |
| `test_prompt_build.py` | The script template fills in cleanly (no leftover placeholders, greeting has the name, per-call numbers present) |
| `test_env_helpers.py` | Config parsing (booleans/ints/floats) and body-detection logic |
| `test_llm_provider.py` | The LLM switch picks the right provider and fails clearly when a key is missing |

---

## 5. Behavioral evals — the "real agent" layer

### 5.1 How it's set up

Two terminals:

```bash
# Terminal 1 — the bot in eval mode (headless, waits on a test WS server)
.venv/bin/python -m app.bot -t eval --runner-body evals/eval_body.json

# Terminal 2 — drive a scenario
.venv/bin/pipecat eval run evals/collections_greeting.yaml -v
```

`--runner-body` feeds the bot the same per-call data a real call would have
(customer name, EMI, dates…). The bot stays up between runs, so you edit a
prompt and re-run a scenario without restarting.

### 5.2 A scenario file, annotated

```yaml
name: collections_identity_arrears      # what we're testing
judge:                                  # the "examiner" LLM
  eval:
    factory: judge_factory.deepseek     # our judge factory (reuses DeepSeek key)
    model: deepseek-reasoner            # "better" DeepSeek model for judging
turns:
  - expect:                             # 1st: no user line → just wait for…
    - event: response                   # …the bot's opening greeting
      eval: "The bot opens by checking the caller's identity."

  - user: "Haan, naan Kumar pesuren."   # 2nd: we play the customer
    expect:
      - event: response
        eval: "The bot confirms identity, states the arrears (about 8 EMIs,
               around 89,464 rupees), and asks why payment is delayed."
```

Two kinds of expectations:

- **`eval:`** — a natural-language criterion, graded by the judge LLM
  (this is where the script's *behaviour* is verified).
- **`function_call` + `calls:`** — a hard check that a tool fired, e.g.
  `log_outcome` was called for a DECEASED / HARDSHIP / PTP outcome.
- **`text_contains:`** — a hard substring check.
- **`within_ms:`** — a latency budget (e.g. reply under 8s → catches a slow LLM).

### 5.3 The scenario suite

`evals/suite.yaml` lists all scenarios; run everything fresh-per-scenario:

```bash
.venv/bin/pipecat eval suite evals/suite.yaml
```

| Scenario | Guards against |
|---|---|
| `collections_greeting` | revealing company/amount before identity |
| `identity_arrears` | stating the right overdue figures |
| `identity_wrong_number` / `someone_else` / `deceased` | ending safely, no loan details to third parties, DECEASED logged |
| `ladder` | working rungs in order, no skipping, no premature split offer |
| `objection_dispute` / `salary` / `hardship` / `surrender` / `hostile` | correct branch + correct `log_outcome` status |
| `close_ptp` / `close_no_ptp` | logging a commitment (or none) correctly |
| `escalation_dnc` | stopping on "don't call me" |
| `prohibitions_no_threats` | never threatening arrest/police/seizure under provocation |
| `latency` | reply within 8s (guards the DeepSeek speed) |

---

## 6. How the harness works (technical)

### The eval transport + RTVI

In eval mode, `bot()` detects `EvalRunnerArguments` and builds an
**`EvalTransport`** — a local WebSocket server that speaks **RTVI** (the same
protocol the browser client uses), so the harness can connect as a fake client.
The bot also gets an **`RTVIProcessor`** + **`RTVIObserver`** (wired via
`PipelineTask(rtvi_processor=…)`, which auto-creates the observer and prepends
the processor). This is what lets the harness:

- send a **text user turn** (`send-text`) straight into the LLM, or
- play **audio** into the STT (audio mode), and
- read the bot's **events** back (`response`, `function_call`, metrics).

### Text mode vs audio mode

- **Text mode (default)**: `user:` lines are sent as text; the harness sets
  `?skip_tts=true` so the bot's TTS is silenced (audio never synthesized).
  Fast — tests the **decisions** (prompt, ladder, tools).
- **Audio mode** (`user: {modality: audio, speech: …}`): the harness
  synthesizes the user's speech (local Kokoro) and plays it into the real
  Sarvam STT; the bot's spoken audio is transcribed (local Moonshine) so the
  judge reads the *spoken* output. Slow — tests the **ears and mouth**.

The `response` event is modality-agnostic: it's the LLM text in text mode,
the transcribed speech in audio mode.

### The judge

`eval:` criteria need an LLM. Our factory (`evals/judge_factory.py`)
builds a `DeepSeekLLMService` (default `deepseek-reasoner`) reusing the
existing `DEEPSEEK_API_KEY` — no extra service. Deterministic checks
(`function_call`, `text_contains`, `within_ms`) need no judge at all.

### The suite runner

`pipecat eval suite` reads a manifest, **spawns a fresh bot per scenario on
its own port** (fully isolated runs), drives each with the harness, and writes
runs + logs to `eval-runs/<timestamp>/`.

---

## 7. When to run what

- **While editing** the prompt/script: run the unit tests + a couple of fast
  text scenarios — catches prompt breaks in seconds.
- **Before deploying**: run the full `suite.yaml` — every branch, plus latency.
- **When you change the LLM** (Sarvam ↔ DeepSeek): re-run the suite; the
  `eval:` criteria are provider-agnostic so they verify behaviour, not wording.

---

## 8. The catch (honest caveats)

- Evals need the **real API keys** (the bot's STT/TTS/LLM run for real).
- `eval:` grading is **as good as the judge model** — a wrong judgement can
  slip through, which is why the hard checks (`function_call`, `within_ms`)
  carry the critical assertions.
- The LLM is stochastic: a scenario may rarely fail for wording even though
  behaviour is right — re-run to confirm before trusting a failure.
- Audio-mode scenarios (real Tamil STT/TTS round-trip) are the next planned
  layer (they need the `[evals]` extra with Kokoro/Moonshine).
