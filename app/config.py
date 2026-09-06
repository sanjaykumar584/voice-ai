"""Environment configuration helpers + process-wide bootstrap.

Centralizes env parsing (used to live in bot.py as _env_*) and the one-time
setup (dotenv load + the LOG_LEVEL=DEBUG logger toggle) so the rest of the
package reads config through here.
"""

import os
import sys

from dotenv import load_dotenv
from loguru import logger

load_dotenv(override=True)

# Set LOG_LEVEL=DEBUG to surface STT transcripts + turn-detection frames while
# debugging turn-taking (short utterances, barge-in, dropped replies).
if os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG":
    logger.remove()
    logger.add(sys.stdout, level="DEBUG")


def env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int | None) -> int | None:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return int(v)


def env_float(name: str, default: float | None) -> float | None:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return float(v)
