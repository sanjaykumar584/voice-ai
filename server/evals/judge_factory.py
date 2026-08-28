"""Judge factory for pipecat eval — DeepSeek as the LLM judge.

Reference it from a scenario with::

    judge:
      eval:
        factory: judge_factory.deepseek
        model: deepseek-reasoner   # optional; default deepseek-reasoner

Run evals from `server/` so this module is importable by `pipecat eval run`.
"""

import os

from pipecat.services.deepseek.llm import DeepSeekLLMService


def deepseek(config: dict):
    """Build a DeepSeek judge LLM (reuses DEEPSEEK_API_KEY from the environment).

    Args:
        config: The scenario's ``judge.eval`` config dict (may carry ``model``).

    Returns:
        A ``DeepSeekLLMService`` with ``run_inference()``.
    """
    return DeepSeekLLMService(
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        settings=DeepSeekLLMService.Settings(
            model=config.get("model", "deepseek-reasoner"),
        ),
    )
