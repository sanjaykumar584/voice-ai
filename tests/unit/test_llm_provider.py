import pytest

from app.voice.services import build_llm
from pipecat.services.deepseek.llm import DeepSeekLLMService
from pipecat.services.sarvam.llm import SarvamLLMService


def test_default_is_sarvam(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("SARVAM_API_KEY", "sk-dummy")
    llm = build_llm()
    assert isinstance(llm, SarvamLLMService)


def test_deepseek(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-dummy")
    llm = build_llm()
    assert isinstance(llm, DeepSeekLLMService)


def test_deepseek_missing_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        build_llm()


def test_openai_missing_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        build_llm()


def test_temperature_and_max_tokens_threaded(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-dummy")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.7")
    monkeypatch.setenv("LLM_MAX_TOKENS", "200")
    llm = build_llm()
    assert llm._settings.temperature == 0.7
    assert llm._settings.max_tokens == 200


def test_sarvam_reasoning_effort_default(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("SARVAM_API_KEY", "sk-dummy")
    llm = build_llm()
    assert llm._settings.reasoning_effort == "low"
