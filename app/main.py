"""FastAPI app factory for the voice-bot host.

Assembles the telephony webhooks, the call-state REST surface, and the batch
calling API into one app. Run with: `python -m app.server`.
"""

import os
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.batch import api as batch_api
from app.calls import router as calls_router
from app.telephony import router as telephony_router
from app.telephony import ws as telephony_ws


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # aiohttp session for Vobiz REST calls (dialing, transfers).
    app.state.session = aiohttp.ClientSession()
    yield
    await app.state.session.close()


def create_app() -> FastAPI:
    """Build the FastAPI application (routers + middleware)."""
    app = FastAPI(lifespan=_lifespan, title="EMI Collections Voice Agent")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(telephony_router.router)
    app.include_router(telephony_ws.router)
    app.include_router(calls_router.router)
    app.include_router(batch_api.router)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    return app
