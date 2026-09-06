"""Pipeline processor that logs per-service latency + usage at INFO."""

from loguru import logger
from pipecat.frames.frames import Frame, MetricsFrame
from pipecat.metrics.metrics import (
    LLMUsageMetricsData,
    ProcessingMetricsData,
    TTFAMetricsData,
    TTFBMetricsData,
    TTSUsageMetricsData,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


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
