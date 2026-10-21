from __future__ import annotations

import queue
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.config import Settings, load_scorecard, load_settings  # loads .env — keep before pipeline imports
from api.db import make_engine
from api.routes import calls, meta
from api.worker import Scorer, Worker
from transcribers import get_transcriber


def transcriber_info(provider: str) -> dict[str, str]:
    try:
        model = get_transcriber(provider).model
    except Exception:  # e.g. a cloud provider whose API key isn't set
        model = ""
    return {"provider": provider, "model": model.split("/")[-1]}


def _default_scorer() -> Scorer:
    from scoring.rate_call import CallScorer
    return CallScorer()


def create_app(
    settings: Settings | None = None,
    *,
    start_worker: bool = True,
    process_call: Callable[..., dict] | None = None,
    make_scorer: Callable[[], Scorer] = _default_scorer,
) -> FastAPI:
    settings = settings or load_settings()
    if process_call is None:
        from pipeline import process_call

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        worker = None
        if start_worker:
            worker = Worker(app.state.engine, app.state.jobs, settings.uploads_dir, settings.provider,
                            process_call, make_scorer)
            worker.start()
        app.state.worker = worker
        yield
        if worker:
            worker.stop(timeout=1)   # a job mid-transcription is picked up again on next start

    app = FastAPI(title="call-bench", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = make_engine(settings.db_path)
    app.state.scorecard = load_scorecard(settings.scorecard_path)
    app.state.jobs = queue.Queue()                      # (call_id, "full" | "rescore")
    app.state.transcriber_info = transcriber_info(settings.provider)
    app.include_router(calls.router, prefix="/api")
    app.include_router(meta.router, prefix="/api")
    return app
