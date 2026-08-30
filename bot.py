#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import json
import os
import sys

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.frames.frames import EndWorkerFrame, Frame, LLMRunFrame, MetricsFrame
from pipecat.metrics.metrics import (
    LLMUsageMetricsData,
    ProcessingMetricsData,
    TTFAMetricsData,
    TTFBMetricsData,
    TTSUsageMetricsData,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.types import EvalRunnerArguments, RunnerArguments, SmallWebRTCRunnerArguments
from pipecat.serializers.vobiz import VobizFrameSerializer, parse_vobiz_start
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.sarvam.llm import SarvamLLMService
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from collections_logic import build_call_context
from call_state import active_calls

load_dotenv(override=True)

# Set LOG_LEVEL=DEBUG to surface STT transcripts + turn-detection frames while
# debugging turn-taking (short utterances, barge-in, dropped replies).
if os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG":
    logger.remove()
    logger.add(sys.stdout, level="DEBUG")


async def log_outcome(params: FunctionCallParams, status: str, note: str = ""):
    """Record the outcome of a collections call.

    Args:
        status: One of "PTP", "NO_PTP", "NO_ARREARS", "DISPUTE", "HARDSHIP",
            "DECEASED", "SURRENDER", "HOSTILE", "WRONG_NUMBER".
        note: Optional note. For PTP, echo the customer's own stated amount and date.
    """
    logger.info(f"[OUTCOME] call outcome: {status} — {note}")

    # Write into the shared call registry (server.py's /calls reads it) so the
    # batch caller can write the result back to the sheet. app_resources carries
    # the call_id on the Vobiz path; other paths leave it empty.
    call_id = (params.app_resources or {}).get("call_id")
    if call_id and call_id in active_calls:
        active_calls[call_id]["outcome"] = status
        active_calls[call_id]["outcome_note"] = note

    await params.result_callback({"recorded": True, "status": status})


async def end_call(params: FunctionCallParams):
    """End the call once the user has said goodbye and the outcome is recorded."""
    await params.result_callback({"success": True})
    await params.llm.push_frame(EndWorkerFrame())


def _dev_reminder_body() -> dict | None:
    """Read a sample reminder body from DEV_REMINDER_BODY (dev-only).

    Lets the browser (SmallWebRTC) test exercise the real reminder flow with
    per-call details even though there is no Vobiz /start body. Ignored when a
    real body_data was supplied by the transport.
    """
    raw = os.getenv("DEV_REMINDER_BODY")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("DEV_REMINDER_BODY is not valid JSON; ignoring.")
        return None


def _is_collections_body(body) -> bool:
    """True if ``body`` carries the collections per-call fields.

    The WebRTC (dev) path passes the raw RTVI /api/offer payload as
    ``runner_args.body``, which is NOT a collections body — only a real Vobiz
    /start body (or the dev mock) has these fields. Fall back to the dev mock
    in that case so the greeting and all variables are populated.
    """
    return (
        isinstance(body, dict)
        and bool(body.get("first_due_date"))
        and bool(body.get("emi"))
    )


class MetricsLogger(FrameProcessor):
    """Log per-service latency + usage at INFO so reply latency is debuggable.

    Sits at the end of the pipeline and logs the MetricsFrame data the
    services emit (enable_metrics is on): TTFB/TTFA/processing times per
    processor, LLM token usage, and TTS character counts.
    """

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        # Must call super() so StartFrame/CancelFrame/lifecycle frames are
        # handled (sets _started); skipping it made _check_started spam errors.
        await super().process_frame(frame, direction)
        if isinstance(frame, MetricsFrame):
            for m in frame.data:
                if isinstance(m, (TTFBMetricsData, TTFAMetricsData, ProcessingMetricsData)):
                    logger.info(
                        f"[METRICS] {m.processor}: "
                        f"{type(m).__name__.removesuffix('MetricsData').upper()} "
                        f"{m.value * 1000:.0f} ms"
                    )
                elif isinstance(m, LLMUsageMetricsData):
                    v = m.value
                    logger.info(
                        f"[METRICS] {m.processor}: LLM tokens in={v.prompt_tokens} "
                        f"out={v.completion_tokens} reasoning={v.reasoning_tokens}"
                    )
                elif isinstance(m, TTSUsageMetricsData):
                    logger.info(f"[METRICS] {m.processor}: TTS chars={m.value}")
        await self.push_frame(frame, direction)


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int | None) -> int | None:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return int(v)


def _env_float(name: str, default: float | None) -> float | None:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return float(v)


def _build_llm():
    """Build the conversation LLM per LLM_PROVIDER (sarvam | deepseek | openai).

    Sarvam STT + TTS stay the same; only the "brain" is swappable. sarvam-105b
    reasons heavily before answering (~7-19s, sometimes empty), which is too slow
    for real-time voice — a fast provider like deepseek/openai is recommended.
    Provider keys come from .env (DEEPSEEK_API_KEY / OPENAI_API_KEY).
    """
    provider = os.getenv("LLM_PROVIDER", "sarvam").lower()
    temperature = _env_float("LLM_TEMPERATURE", 0.5)
    max_tokens = _env_int("LLM_MAX_TOKENS", None)

    def _with_tokens(settings: dict) -> dict:
        if max_tokens is not None:
            settings["max_tokens"] = max_tokens
        return settings

    if provider == "deepseek":
        from pipecat.services.deepseek.llm import DeepSeekLLMService

        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError(
                "LLM_PROVIDER=deepseek but DEEPSEEK_API_KEY is not set in .env"
            )
        settings = _with_tokens({"temperature": temperature})
        model = os.getenv("DEEPSEEK_MODEL")
        if model:
            settings["model"] = model
        return DeepSeekLLMService(
            api_key=api_key,
            settings=DeepSeekLLMService.Settings(**settings),
        )

    if provider == "openai":
        from pipecat.services.openai.llm import OpenAILLMService

        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set in .env")
        settings = _with_tokens({"temperature": temperature})
        model = os.getenv("OPENAI_MODEL")
        if model:
            settings["model"] = model
        return OpenAILLMService(
            api_key=api_key,
            settings=OpenAILLMService.Settings(**settings),
        )

    # default: Sarvam. reasoning_effort="low" roughly halves response time;
    # do NOT set max_tokens low (the model truncates mid-reasoning, empty reply).
    settings = _with_tokens(
        {
            "reasoning_effort": os.getenv("SARVAM_LLM_REASONING_EFFORT", "low"),
            "temperature": temperature,
        }
    )
    return SarvamLLMService(
        api_key=os.getenv("SARVAM_API_KEY", ""),
        settings=SarvamLLMService.Settings(**settings),
    )


async def run_bot(
    transport: BaseTransport,
    handle_sigint: bool,
    body_data: dict | None = None,
    audio_in_sample_rate: int = 8000,
    rtvi_processor=None,
    app_resources=None,
):
    llm = _build_llm()

    sarvam_api_key = os.getenv("SARVAM_API_KEY")

    stt_settings = dict(
        model=os.getenv("SARVAM_STT_MODEL", "saaras:v3"),
        language=Language.TA_IN,
        # Sarvam's own VAD drives turn boundaries and pairs each transcript with
        # end-of-speech atomically (more reliable for short Tamil utterances than
        # a Silero flush, which dropped short clips).
        vad_signals=_env_bool("SARVAM_STT_VAD_SIGNALS", True),
        high_vad_sensitivity=_env_bool("SARVAM_STT_HIGH_VAD_SENSITIVITY", True),
    )
    # Fine-grained VAD params (saaras:v3 only). Sent only when set in .env —
    # unset lets Sarvam's server use its own defaults. Only tweak these to tune
    # end-of-speech speed / short-utterance capture.
    for env_key, settings_key, parser in [
        ("SARVAM_STT_MIN_SPEECH_FRAMES", "min_speech_frames", _env_int),
        ("SARVAM_STT_FIRST_TURN_MIN_SPEECH_FRAMES", "first_turn_min_speech_frames", _env_int),
        ("SARVAM_STT_NEGATIVE_FRAMES_COUNT", "negative_frames_count", _env_int),
        ("SARVAM_STT_NEGATIVE_FRAMES_WINDOW", "negative_frames_window", _env_int),
        ("SARVAM_STT_START_SPEECH_VOLUME_THRESHOLD", "start_speech_volume_threshold", _env_float),
        ("SARVAM_STT_POSITIVE_SPEECH_THRESHOLD", "positive_speech_threshold", _env_float),
        ("SARVAM_STT_NEGATIVE_SPEECH_THRESHOLD", "negative_speech_threshold", _env_float),
    ]:
        val = parser(env_key, None)
        if val is not None:
            stt_settings[settings_key] = val

    stt = SarvamSTTService(
        api_key=sarvam_api_key,
        keepalive_timeout=_env_float("SARVAM_STT_KEEPALIVE_TIMEOUT", 10.0),
        keepalive_interval=_env_float("SARVAM_STT_KEEPALIVE_INTERVAL", 5.0),
        settings=SarvamSTTService.Settings(**stt_settings),
    )

    # bulbul:v3-beta outputs 24000 Hz, matching PipelineParams.audio_out_sample_rate
    # below. If you switch to bulbul:v2 (22050 Hz), change audio_out_sample_rate too.
    # min_buffer_size: fewer chars buffered before synthesizing -> faster first
    # audio. Sarvam's TTS API REJECTS values below 30 (422 error) — keep >= 30.
    tts = SarvamTTSService(
        api_key=sarvam_api_key,
        sample_rate=_env_int("SARVAM_TTS_SAMPLE_RATE", 24000),
        settings=SarvamTTSService.Settings(
            model=os.getenv("SARVAM_TTS_MODEL", "bulbul:v3-beta"),
            voice=os.getenv("SARVAM_VOICE", "priya"),
            language=Language.TA_IN,
            min_buffer_size=_env_int("SARVAM_TTS_MIN_BUFFER_SIZE", 30),
            max_chunk_length=_env_int("SARVAM_TTS_MAX_CHUNK_LENGTH", 150),
        ),
    )

    if not _is_collections_body(body_data):
        # The WebRTC dev path passes the raw RTVI offer payload here; use the
        # dev mock instead so the greeting and variables are populated. A real
        # Vobiz /start body has the collections fields and is used as-is.
        body_data = _dev_reminder_body()

    # Fill the collections script with this call's variables + computed derived
    # values, and seed them as a developer message so the LLM never computes.
    system_prompt, developer_message = build_call_context(body_data)

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "developer",
            "content": developer_message,
        },
    ]

    tools = [log_outcome, end_call]

    context = LLMContext(messages, tools=tools)
    # Turn detection is driven by Sarvam STT's own VAD signals (vad_signals=True
    # on the STT), which broadcast UserStarted/StoppedSpeakingFrame through the
    # pipeline. No separate vad_analyzer here — a second (Silero) VAD caused a
    # race where short utterances were flushed before Sarvam had a transcript.
    #
    # Stop strategies (latency): the speech-timeout fires ~0.8s after the last
    # transcript (bounded reply), instead of waiting on the smart-turn analyzer
    # + Sarvam's 1.17s P99 safety net. The analyzer stays as a fallback.
    user_turn_strategies = UserTurnStrategies(
        stop=[
            SpeechTimeoutUserTurnStopStrategy(
                user_speech_timeout=_env_float("TURN_SPEECH_TIMEOUT", 0.8),
                wait_for_transcript=True,
            ),
            TurnAnalyzerUserTurnStopStrategy(
                turn_analyzer=LocalSmartTurnAnalyzerV3(cpu_count=1)
            ),
        ]
    )
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(user_turn_strategies=user_turn_strategies),
    )

    metrics_logger = MetricsLogger()

    pipeline = Pipeline(
        [
            transport.input(),  # Websocket input from client
            stt,  # Speech-To-Text
            context_aggregator.user(),
            llm,  # LLM
            tts,  # Text-To-Speech
            transport.output(),  # Websocket output to client
            context_aggregator.assistant(),
            metrics_logger,  # Log per-service TTFB/usage at INFO
        ]
    )

    task = PipelineTask(
        pipeline,
        rtvi_processor=rtvi_processor,  # eval mode: RTVI harness drives the bot
        app_resources=app_resources,    # e.g. {"call_id": ...} for outcome capture
        params=PipelineParams(
            audio_in_sample_rate=audio_in_sample_rate,  # Vobiz 8kHz mu-law / SmallWebRTC+eval 16kHz
            audio_out_sample_rate=24000, # Sarvam bulbul:v3-beta native (auto-resampled to the transport rate)
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Starting outbound call conversation")
        # Speak first (collections calls shouldn't wait for the caller): seed a
        # one-off instruction and trigger a single LLM run.
        context.add_message(
            {
                "role": "developer",
                "content": (
                    "The call has just started. Begin with Step 1 — Identity. Do NOT "
                    "state the company, the loan, or any amount until identity is confirmed."
                ),
            }
        )
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Outbound call ended")
        # task.cancel() is correct when the *caller* hangs up first — the
        # WS is already dead so there is no in-flight TTS to drain. If your
        # bot ends the call itself (e.g. graceful EndFrame from a flow),
        # prefer `await task.stop_when_done()` so queued TTS frames finish
        # playing before the pipeline tears down.
        await task.cancel()

    runner = PipelineRunner(handle_sigint=handle_sigint)

    await runner.run(task)


async def bot(
    runner_args: RunnerArguments,
    call_id: str = None,
    stream_id: str = None,
    body_data: dict = None,
):
    """Main bot entry point compatible with Pipecat Cloud."""

    # Eval harness path (headless behavioral tests): `python bot.py -t eval`
    # + `pipecat eval run <scenario>.yaml`. Body comes from `--body <file>`.
    if isinstance(runner_args, EvalRunnerArguments):
        from pipecat.evals.serializer import RTVIEvalSerializer
        from pipecat.evals.transport import EvalTransport, EvalTransportParams
        from pipecat.processors.frameworks.rtvi import RTVIProcessor

        transport = EvalTransport(
            params=EvalTransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                serializer=RTVIEvalSerializer(),
            ),
            host=runner_args.host,
            port=runner_args.port,
        )
        await run_bot(
            transport,
            runner_args.handle_sigint,
            body_data=runner_args.body,
            audio_in_sample_rate=16000,
            rtvi_processor=RTVIProcessor(),
        )
        return

    # Dev-only browser path (no Vobiz): SmallWebRTC via the dev runner.
    # `python bot.py` -> prebuilt UI at http://localhost:7860.
    if isinstance(runner_args, SmallWebRTCRunnerArguments):
        transport = SmallWebRTCTransport(
            webrtc_connection=runner_args.webrtc_connection,
            params=TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
            ),
        )
        await run_bot(
            transport,
            runner_args.handle_sigint,
            body_data=runner_args.body,
            audio_in_sample_rate=16000,
        )
        return

    # Vobiz telephony path (called by server.py with a live WebSocket).
    # Read Vobiz's `start` event off the WebSocket to learn the negotiated
    # wire format (encoding + sample rate + IDs). Env vars are fallback hints.
    env_encoding = os.getenv("VOBIZ_ENCODING", "audio/x-mulaw")
    env_sample_rate = int(os.getenv("VOBIZ_SAMPLE_RATE", "8000"))

    parsed = await parse_vobiz_start(runner_args.websocket)
    logger.info(
        f"Vobiz start: callId={parsed['call_id']!r}, streamId={parsed['stream_id']!r}, "
        f"mediaFormat=({parsed['encoding']!r}, {parsed['sample_rate']})"
    )
    call_id = call_id or parsed["call_id"]
    stream_id = stream_id or parsed["stream_id"]
    vobiz_encoding = parsed["encoding"] or env_encoding
    vobiz_sample_rate = parsed["sample_rate"] or env_sample_rate

    serializer = VobizFrameSerializer(
        stream_id=stream_id,
        call_id=call_id,
        auth_id=os.getenv("VOBIZ_AUTH_ID", ""),
        auth_token=os.getenv("VOBIZ_AUTH_TOKEN", ""),
        params=VobizFrameSerializer.InputParams(
            vobiz_sample_rate=vobiz_sample_rate,
            encoding=vobiz_encoding,
            sample_rate=None,
            l16_byte_order=os.getenv("VOBIZ_L16_ENDIAN", "be"),
            auto_hang_up=True,
        ),
    )

    transport = FastAPIWebsocketTransport(
        websocket=runner_args.websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,  # CRITICAL: Must be False for telephony
            serializer=serializer,
            # NOTE: vad_analyzer is deprecated on FastAPIWebsocketParams in
            # pipecat 1.x. VAD is now wired on LLMUserAggregatorParams above.
        ),
    )

    handle_sigint = runner_args.handle_sigint

    # app_resources: lets the log_outcome tool write the result into server.py's
    # call registry (same process), which /calls exposes to the batch caller.
    await run_bot(
        transport,
        handle_sigint,
        body_data=body_data,
        app_resources={"call_id": call_id},
    )


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
