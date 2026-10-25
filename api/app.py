from __future__ import annotations

import queue
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from api.config import ROOT, Settings, load_scorecard, load_settings  # loads .env — keep before pipeline imports
from api.db import make_engine
from api.routes import calls, meta
from api.worker import Scorer, Worker
from transcribers import get_transcriber


def transcriber_info(provider: str, worker_mode: str = "local") -> dict[str, str]:
    try:
        model = get_transcriber(provider).model
    except Exception:  # e.g. a cloud provider whose API key isn't set
        model = ""
    return {"provider": provider, "model": model.split("/")[-1], "worker_mode": worker_mode}


def mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve the built front end at /. A path that's a file in `dist` gets that file;
    any other non-/api path gets index.html so client-side routes load. Register
    after the /api routers: they match first, and unknown /api paths stay 404s."""
    root = dist.resolve()
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Not Found")
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        # no-cache: after a rebuild the page must pick up the new hashed asset names
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def _default_scorer() -> Scorer:
    from scoring.rate_call import CallScorer
    return CallScorer()


def create_app(
    settings: Settings | None = None,
    *,
    start_worker: bool = True,
    process_call: Callable[..., dict] | None = None,
    make_scorer: Callable[[], Scorer] = _default_scorer,
    web_dist: Path | None = ROOT / "web" / "dist",
) -> FastAPI:
    settings = settings or load_settings()
    if process_call is None and not settings.remote:   # remote mode never transcribes here
        from pipeline import process_call

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        worker = None
        if start_worker:
            worker = Worker(app.state.engine, app.state.jobs, settings.uploads_dir, settings.provider,
                            None if settings.remote else process_call, make_scorer, remote=settings.remote)
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
    app.state.transcriber_info = transcriber_info(settings.provider, settings.worker_mode)
    app.include_router(calls.router, prefix="/api")
    app.include_router(meta.router, prefix="/api")
    if web_dist and (web_dist / "index.html").is_file():   # built by scripts/serve.sh; absent in dev
        mount_spa(app, web_dist)
    return app
