"""Voice agent entry: `python -m app.bot -t webrtc|eval|...`"""

from app.voice.transports import bot  # noqa: F401  (dev-runner contract)

if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
