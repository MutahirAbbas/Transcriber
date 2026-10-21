from __future__ import annotations

import queue

from fastapi import FastAPI

from api.config import Settings, load_scorecard, load_settings  # loads .env — keep before pipeline imports
from api.db import make_engine
from api.routes import calls, meta
from transcribers import get_transcriber


def transcriber_info(provider: str) -> dict[str, str]:
    try:
        model = get_transcriber(provider).model
    except Exception:  # e.g. a cloud provider whose API key isn't set
        model = ""
    return {"provider": provider, "model": model.split("/")[-1]}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="call-bench")
    app.state.settings = settings
    app.state.engine = make_engine(settings.db_path)
    app.state.scorecard = load_scorecard(settings.scorecard_path)
    app.state.jobs = queue.Queue()                      # (call_id, "full" | "rescore")
    app.state.transcriber_info = transcriber_info(settings.provider)
    app.include_router(calls.router, prefix="/api")
    app.include_router(meta.router, prefix="/api")
    return app
