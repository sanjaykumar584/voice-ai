"""The voice pipeline (run_bot) — one bot session's STT→LLM→TTS loop."""

import json
import os

from loguru import logger
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies

from app.config import env_bool, env_float, env_int
from app.voice.collections import build_call_context
from app.voice.metrics_logger import MetricsLogger
from app.voice.services import build_llm
from app.voice.tools import end_call, log_outcome


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


async def run_bot(
    transport: BaseTransport,
    handle_sigint: bool,
    body_data: dict | None = None,
    audio_in_sample_rate: int = 8000,
    rtvi_processor=None,
    app_resources=None,
):
    llm = build_llm()

    sarvam_api_key = os.getenv("SARVAM_API_KEY")

    stt_settings = dict(
        model=os.getenv("SARVAM_STT_MODEL", "saaras:v3"),
        language=Language.TA_IN,
        # Sarvam's own VAD drives turn boundaries and pairs each transcript with
        # end-of-speech atomically (more reliable for short Tamil utterances than
        # a Silero flush, which dropped short clips).
        vad_signals=env_bool("SARVAM_STT_VAD_SIGNALS", True),
        high_vad_sensitivity=env_bool("SARVAM_STT_HIGH_VAD_SENSITIVITY", True),
    )
    # Fine-grained VAD params (saaras:v3 only). Sent only when set in .env —
    # unset lets Sarvam's server use its own defaults. Only tweak these to tune
    # end-of-speech speed / short-utterance capture.
    for env_key, settings_key, parser in [
        ("SARVAM_STT_MIN_SPEECH_FRAMES", "min_speech_frames", env_int),
        ("SARVAM_STT_FIRST_TURN_MIN_SPEECH_FRAMES", "first_turn_min_speech_frames", env_int),
        ("SARVAM_STT_NEGATIVE_FRAMES_COUNT", "negative_frames_count", env_int),
        ("SARVAM_STT_NEGATIVE_FRAMES_WINDOW", "negative_frames_window", env_int),
        ("SARVAM_STT_START_SPEECH_VOLUME_THRESHOLD", "start_speech_volume_threshold", env_float),
        ("SARVAM_STT_POSITIVE_SPEECH_THRESHOLD", "positive_speech_threshold", env_float),
        ("SARVAM_STT_NEGATIVE_SPEECH_THRESHOLD", "negative_speech_threshold", env_float),
    ]:
        val = parser(env_key, None)
        if val is not None:
            stt_settings[settings_key] = val

    stt = SarvamSTTService(
        api_key=sarvam_api_key,
        keepalive_timeout=env_float("SARVAM_STT_KEEPALIVE_TIMEOUT", 10.0),
        keepalive_interval=env_float("SARVAM_STT_KEEPALIVE_INTERVAL", 5.0),
        settings=SarvamSTTService.Settings(**stt_settings),
    )

    # bulbul:v3-beta outputs 24000 Hz, matching PipelineParams.audio_out_sample_rate
    # below. If you switch to bulbul:v2 (22050 Hz), change audio_out_sample_rate too.
    # min_buffer_size: fewer chars buffered before synthesizing -> faster first
    # audio. Sarvam's TTS API REJECTS values below 30 (422 error) — keep >= 30.
    tts = SarvamTTSService(
        api_key=sarvam_api_key,
        sample_rate=env_int("SARVAM_TTS_SAMPLE_RATE", 24000),
        settings=SarvamTTSService.Settings(
            model=os.getenv("SARVAM_TTS_MODEL", "bulbul:v3-beta"),
            voice=os.getenv("SARVAM_VOICE", "priya"),
            language=Language.TA_IN,
            min_buffer_size=env_int("SARVAM_TTS_MIN_BUFFER_SIZE", 30),
            max_chunk_length=env_int("SARVAM_TTS_MAX_CHUNK_LENGTH", 150),
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
                user_speech_timeout=env_float("TURN_SPEECH_TIMEOUT", 0.8),
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
            audio_out_sample_rate=24000,  # Sarvam bulbul:v3-beta native (auto-resampled)
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
