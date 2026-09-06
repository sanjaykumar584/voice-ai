"""Launcher for the webhook host (FastAPI/uvicorn). Run: `python -m app.server`

Kept at the repo root for convenience/parity; the implementation lives in app/.
"""

import uvicorn

from app.main import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=7860)
