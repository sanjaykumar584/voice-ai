"""Transport dispatch: the bot entry point for every run mode.

`bot(runner_args, ...)` is the contract the Pipecat dev runner (and server.py's
WebSocket handler) invokes. It picks the transport from the runner-args type:

- EvalRunnerArguments          → headless eval transport (`-t eval`)
- SmallWebRTCRunnerArguments   → browser dev transport (`-t webrtc`)
- WebSocketRunnerArguments     → Vobiz telephony transport (server.py's /ws)
"""

import os

from loguru import logger
from pipecat.runner.types import (
    EvalRunnerArguments,
    RunnerArguments,
    SmallWebRTCRunnerArguments,
)
from pipecat.serializers.vobiz import VobizFrameSerializer, parse_vobiz_start
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from app.voice.pipeline import run_bot


async def bot(
    runner_args: RunnerArguments,
    call_id: str = None,
    stream_id: str = None,
    body_data: dict = None,
):
    """Main bot entry point compatible with Pipecat Cloud."""

    # Eval harness path (headless behavioral tests): `-t eval` +
    # `pipecat eval run <scenario>.yaml`. Body comes from `--runner-body <file>`.
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
    # `python -m app.bot -t webrtc` -> prebuilt UI at http://localhost:7860.
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

    # Vobiz telephony path (called by server.py's /ws handler with a live
    # WebSocket). Read Vobiz's `start` event to learn the negotiated wire format.
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
            # pipecat 1.x. VAD is wired on the user aggregator in pipeline.py.
        ),
    )

    handle_sigint = runner_args.handle_sigint

    # app_resources: lets the log_outcome tool write the result into the call
    # registry + Supabase (batch flow), keyed by the Vobiz call id.
    await run_bot(
        transport,
        handle_sigint,
        body_data=body_data,
        app_resources={"call_id": call_id},
    )
