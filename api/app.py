from __future__ import annotations

import logging
import queue
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy.engine import Engine
from sqlmodel import Session

from api.auth import Auth, normalize_email, require_user
from api.config import ROOT, Settings, load_scorecard, load_settings  # loads .env — keep before pipeline imports
from api.db import make_engine
from api.leases import LeaseReaper
from api.ratelimit import RateLimiter
from api.routes import auth as auth_routes, calls, meta, worker as worker_routes
from api.users import UserError, ensure_env_user
from api.worker import Scorer, Worker
from transcribers import get_transcriber

log = logging.getLogger(__name__)


def transcriber_info(provider: str, worker_mode: str = "local") -> dict[str, str]:
    try:
        model = get_transcriber(provider).model
    except Exception:  # e.g. a cloud provider whose API key isn't set
        model = ""
    return {"provider": provider, "model": model.split("/")[-1], "worker_mode": worker_mode}


def mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve the built front end at /. A path that's a file in `dist` gets that file;
    any other non-/api path gets index.html so client-side routes load — or, when
    signed out, a redirect to /login. Register after the /api routers: they match
    first, and unknown /api paths stay 404s."""
    root = dist.resolve()
    index = root / "index.html"

    # unknown /api paths stay JSON 404s for every method (not a 405 from the page route below)
    @app.api_route("/api/{rest:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                   include_in_schema=False)
    def api_not_found(rest: str) -> None:
        raise HTTPException(404, "Not Found")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request) -> Response:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Not Found")
        candidate = (root / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)   # built assets hold no call data
        if path != "login" and not request.app.state.auth.signed_in(request):
            target = "/" + path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=302)
        # no-cache: after a rebuild the page must pick up the new hashed asset names
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def sync_env_login(settings: Settings, engine: Engine) -> None:
    """Keep the LOGIN_USER / LOGIN_PASS account in step with .env (when sign-in is on)."""
    if not settings.auth_enabled or not (settings.login_user or settings.login_pass):
        return
    if not (settings.login_user and settings.login_pass):
        raise RuntimeError("Set both LOGIN_USER and LOGIN_PASS, or neither")
    with Session(engine) as s:
        try:
            outcome = ensure_env_user(s, settings.login_user, settings.login_pass)
        except UserError as e:
            raise RuntimeError(str(e)) from None
    log.info("sign-in account %s from LOGIN_USER: %s", normalize_email(settings.login_user), outcome)


def _default_scorer() -> Scorer:
    from scoring.rate_call import CallScorer
    return CallScorer()


def create_app(
    settings: Settings | None = None,
    *,
    start_worker: bool = True,
    process_call: Callable[..., dict] | None = None,
    make_scorer: Callable[[], Scorer] = _default_scorer,
    web_dist: Path | None = None,          # the built front end to serve at / (api.main passes web/dist)
) -> FastAPI:
    settings = settings or load_settings()
    if process_call is None and not settings.remote:   # remote mode never transcribes here
        from pipeline import process_call

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        worker = reaper = None
        if start_worker:
            worker = Worker(app.state.engine, app.state.jobs, settings.uploads_dir, settings.provider,
                            None if settings.remote else process_call, make_scorer, remote=settings.remote)
            worker.start()
            if settings.remote:
                reaper = LeaseReaper(app.state.engine)
                reaper.start()
        app.state.worker = worker
        yield
        if reaper:
            reaper.stop(timeout=1)
        if worker:
            worker.stop(timeout=1)   # a job mid-transcription is picked up again on next start

    app = FastAPI(title="call-bench", lifespan=lifespan)
    app.state.settings = settings
    app.state.auth = Auth(settings.session_secret, enabled=settings.auth_enabled)
    app.state.engine = make_engine(settings.db_path)
    sync_env_login(settings, app.state.engine)
    app.state.scorecard = load_scorecard(settings.scorecard_path)
    app.state.jobs = queue.Queue()                      # (call_id, "full" | "rescore")
    app.state.transcriber_info = transcriber_info(settings.provider, settings.worker_mode)

    # container healthcheck (GET) and uptime checks like `curl -I` (HEAD); no auth, no data
    @app.api_route("/api/health", methods=["GET", "HEAD"], include_in_schema=False)
    def health() -> dict[str, bool]:
        return {"ok": True}

    signed_in = [Depends(require_user)]                 # every non-worker /api route
    app.include_router(auth_routes.router, prefix="/api")   # login is public; /me checks itself
    app.include_router(calls.router, prefix="/api", dependencies=signed_in)
    app.include_router(meta.router, prefix="/api", dependencies=signed_in)
    if settings.remote:
        app.state.worker_limiter = RateLimiter(rate=5, burst=20)   # per client IP
        app.include_router(worker_routes.router, prefix="/api")
    if web_dist and (web_dist / "index.html").is_file():   # built by scripts/serve.sh; absent in dev
        mount_spa(app, web_dist)
    return app
