#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import json
import os

from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import EndWorkerFrame, LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments, SmallWebRTCRunnerArguments
from pipecat.serializers.vobiz import VobizFrameSerializer, parse_vobiz_start
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.sarvam.llm import SarvamLLMService
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

load_dotenv(override=True)


SYSTEM_PROMPT = (
    "You are the automated calling assistant for a business that sends outbound "
    "reminder calls. You call customers to remind them of an upcoming appointment, "
    "payment, or delivery, and you collect their response.\n"
    "Your responses will be read aloud over the phone, so keep them short, clear, and "
    "conversational. Avoid special characters, emojis, bullets, or any formatting that "
    "cannot be spoken.\n"
    "Begin by saying: 'Hello! This is an automated reminder call from our team. Am I "
    "speaking with the right person?' Then confirm the caller's identity, deliver the "
    "reminder details you were given, and ask them to confirm, reschedule, or cancel.\n"
    "Once the caller has given their answer and you have thanked them, call the "
    "end_call function to end the call gracefully."
)


async def log_outcome(params: FunctionCallParams, status: str, note: str = ""):
    """Record the outcome of the reminder call so it can be reported back.

    Args:
        status: The caller's response. One of "confirmed", "rescheduled",
            "cancelled", "declined", or "no_answer".
        note: Optional short note from the caller.
    """
    logger.info(f"[OUTCOME] call outcome: {status} — {note}")
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


async def run_bot(
    transport: BaseTransport,
    handle_sigint: bool,
    body_data: dict | None = None,
    audio_in_sample_rate: int = 8000,
):
    api_key = os.getenv("SARVAM_API_KEY")

    llm = SarvamLLMService(api_key=api_key)

    stt = SarvamSTTService(
        api_key=api_key,
        settings=SarvamSTTService.Settings(
            model=os.getenv("SARVAM_STT_MODEL", "saaras:v3"),
        ),
    )

    # bulbul:v3-beta outputs 24000 Hz, matching PipelineParams.audio_out_sample_rate
    # below. If you switch to bulbul:v2 (22050 Hz), change audio_out_sample_rate too.
    tts = SarvamTTSService(
        api_key=api_key,
        sample_rate=int(os.getenv("SARVAM_TTS_SAMPLE_RATE", "24000")),
        settings=SarvamTTSService.Settings(
            model=os.getenv("SARVAM_TTS_MODEL", "bulbul:v3-beta"),
            voice=os.getenv("SARVAM_VOICE", "priya"),
        ),
    )

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
    ]

    # Prime this specific call with the reminder details carried from POST /start
    # (body -> query param on /answer -> base64 body= param on the WebSocket URL).
    if body_data:
        messages.append(
            {
                "role": "developer",
                "content": (
                    "Reminder details for THIS call (use them to inform the caller; if any "
                    "field is missing, ask the caller for it): "
                    + json.dumps(body_data)
                ),
            }
        )

    if body_data is None:
        body_data = _dev_reminder_body()

    tools = [log_outcome, end_call]

    context = LLMContext(messages, tools=tools)
    # pipecat 1.x: vad_analyzer lives on LLMUserAggregatorParams now,
    # not on the transport (transport-side vad_analyzer is silently a no-op).
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),  # Websocket input from client
            stt,  # Speech-To-Text
            context_aggregator.user(),
            llm,  # LLM
            tts,  # Text-To-Speech
            transport.output(),  # Websocket output to client
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=audio_in_sample_rate,  # Vobiz 8kHz mu-law / SmallWebRTC 16kHz
            audio_out_sample_rate=24000, # Sarvam bulbul:v3-beta native (auto-resampled to the transport rate)
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Starting outbound call conversation")
        # Speak first (reminder calls shouldn't wait for the caller): seed a
        # one-off instruction and trigger a single LLM run.
        context.add_message(
            {
                "role": "developer",
                "content": (
                    "The call has just started. Begin with your greeting now and "
                    "deliver the reminder details."
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

    await run_bot(transport, handle_sigint, body_data=body_data)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
