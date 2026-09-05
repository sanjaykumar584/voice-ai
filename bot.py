"""Launcher for the voice bot (dev runner contract). Run: `python -m app.bot -t webrtc|eval|...`

Kept at the repo root so tools that execute `bot.py` by path (the eval-suite
spawner) keep working; the implementation lives in app/.
"""

from app.voice.transports import bot  # noqa: F401

if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
