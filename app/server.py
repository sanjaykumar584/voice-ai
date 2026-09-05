"""Webhook host entry: `python -m app.server`."""

import uvicorn

from app.main import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=7860)
