"""Voice service factory — build the LLM (provider-switchable)."""

import os

from app.config import env_float, env_int


def build_llm():
    """Build the conversation LLM per LLM_PROVIDER (sarvam | deepseek | openai).

    Sarvam STT + TTS stay the same; only the "brain" is swappable. sarvam-105b
    reasons heavily before answering (~7-19s, sometimes empty), which is too slow
    for real-time voice — a fast provider like deepseek/openai is recommended.
    Provider keys come from .env (DEEPSEEK_API_KEY / OPENAI_API_KEY).
    """
    provider = os.getenv("LLM_PROVIDER", "sarvam").lower()
    temperature = env_float("LLM_TEMPERATURE", 0.5)
    max_tokens = env_int("LLM_MAX_TOKENS", None)

    def _with_tokens(settings: dict) -> dict:
        if max_tokens is not None:
            settings["max_tokens"] = max_tokens
        return settings

    if provider == "deepseek":
        from pipecat.services.deepseek.llm import DeepSeekLLMService

        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise ValueError("LLM_PROVIDER=deepseek but DEEPSEEK_API_KEY is not set in .env")
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
    from pipecat.services.sarvam.llm import SarvamLLMService

    return SarvamLLMService(
        api_key=os.getenv("SARVAM_API_KEY", ""),
        settings=SarvamLLMService.Settings(**settings),
    )
