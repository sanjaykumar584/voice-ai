"""The voice-agent application package.

Package-level bootstrap: load .env once so every submodule (repo, storage,
config, services…) sees the same environment, regardless of which entry point
imports the package first.
"""

from dotenv import load_dotenv

load_dotenv(override=True)
