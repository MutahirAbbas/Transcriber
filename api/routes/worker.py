"""Worker API for the Mac transcription worker (WORKER_MODE=remote only).

Bearer-token auth (WORKER_TOKEN), not the user login. A worker identifies itself
with an `X-Worker-Id` header (URL-encoded); job routes only answer the worker
holding that call's lease. Never log transcript text or request bodies here.
"""
from __future__ import annotations

import hmac
import json
from typing import Annotated, Any
from urllib.parse import unquote

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session

from api import leases, repo
from api.db import get_session
from api.media import AUDIO_TYPES, find_audio
from api.models import Call, CallStatus, utcnow
from api.redaction import redact_transcript_dict
from api.worker import SCORING_PROGRESS

router = APIRouter(prefix="/worker")

SessionDep = Annotated[Session, Depends(get_session)]


def worker_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_worker_id: Annotated[str | None, Header()] = None,
) -> str:
    """Rate limit, check the bearer token, record last_seen. Returns the worker id."""
    ip = request.client.host if request.client else "unknown"
    if not request.app.state.worker_limiter.allow(ip):
        raise HTTPException(429, "Too many requests")
    token = request.app.state.settings.worker_token
    if not token or not authorization or not hmac.compare_digest(
        authorization.encode(), f"Bearer {token}".encode()
    ):
        raise HTTPException(401, "Invalid worker token", headers={"WWW-Authenticate": "Bearer"})
    worker_id = unquote(x_worker_id or "").strip()[:100]
    if worker_id:
        leases.record_seen(request.app.state.engine, worker_id)
    return worker_id


WorkerId = Annotated[str, Depends(worker_auth)]


def _leased(session: Session, call_id: str, worker_id: str) -> Call:
    call = session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, "Call not found")
    if not leases.holds_lease(call, worker_id):
        raise HTTPException(409, "This worker doesn't hold the lease on that call")
    return call


# ---- claim ---------------------------------------------------------------------------

class ClaimIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=100)
    model: str | None = Field(default=None, max_length=200)   # transcriber model, for the status line


@router.post("/claim", response_model=None)
def claim(body: ClaimIn, request: Request, session: SessionDep, _worker: WorkerId) -> dict[str, Any] | Response:
    engine, settings = request.app.state.engine, request.app.state.settings
    leases.record_seen(engine, body.worker_id, body.model)
    call = leases.claim(engine, body.worker_id)
    if call is None:
        return Response(status_code=204)
    from pipeline import load_keywords

    audio = find_audio(settings.uploads_dir, call.id)
    return {
        "call_id": call.id,
        "audio_path_hint": audio.name if audio else f"{call.id}.audio",
        "stereo": bool(call.stereo),
        "keywords": load_keywords(settings.keywords_path),
        "duration_s": call.duration_s,
        "rtf": repo.recent_rtf(session, settings.provider, call.stereo),   # for the progress estimate
    }


# ---- one job -------------------------------------------------------------------------

@router.get("/jobs/{call_id}/audio")
def job_audio(call_id: str, request: Request, session: SessionDep, worker_id: WorkerId) -> FileResponse:
    _leased(session, call_id, worker_id)
    path = find_audio(request.app.state.settings.uploads_dir, call_id)
    if path is None:
        raise HTTPException(404, "Audio file not found")
    return FileResponse(path, media_type=AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream"),
                        headers={"X-Content-Type-Options": "nosniff"})


class HeartbeatIn(BaseModel):
    stage: str = Field(default="transcribing", max_length=40)
    progress: float = Field(default=0.0, ge=0.0, le=1.0)


@router.post("/jobs/{call_id}/heartbeat")
def heartbeat(call_id: str, body: HeartbeatIn, session: SessionDep, worker_id: WorkerId) -> dict[str, Any]:
    call = _leased(session, call_id, worker_id)
    call.lease_until = utcnow() + leases.LEASE
    call.progress = round(min(body.progress, 0.9), 3)
    session.add(call)
    session.commit()
    return {"lease_until": call.lease_until.isoformat().replace("+00:00", "Z")}


class SegmentIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str
    speaker: str | None = None
    confidence: float | None = None


class TranscriptIn(BaseModel):
    """`TranscriptResult.to_dict()`."""
    model_config = ConfigDict(extra="ignore", protected_namespaces=())
    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=200)
    call_id: str | None = None
    segments: list[SegmentIn]
    latency_s: float = Field(ge=0)
    audio_duration_s: float = Field(ge=0)
    cost_usd: float = 0.0


@router.post("/jobs/{call_id}/transcript")
def post_transcript(
    call_id: str, request: Request, session: SessionDep, worker_id: WorkerId,
    payload: Annotated[Any, Body()],
) -> dict[str, str]:
    call = session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, "Call not found")
    if call.transcript_json is not None or call.status in (CallStatus.scoring, CallStatus.done):
        return {"status": "already received"}   # idempotent: a retried post changes nothing
    if not leases.holds_lease(call, worker_id):
        raise HTTPException(409, "This worker doesn't hold the lease on that call")
    try:
        tx = TranscriptIn.model_validate(payload)
    except ValidationError as e:
        # field locations only — never echo the body, it's customer speech
        where = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:5])
        raise HTTPException(400, f"Invalid transcript: {where}") from None

    transcript = redact_transcript_dict({**tx.model_dump(), "call_id": call.id})   # before anything is written
    call.transcript_json = json.dumps(transcript, ensure_ascii=False)
    call.provider, call.model = tx.provider, tx.model
    call.status, call.progress, call.lease_until, call.error = CallStatus.scoring, SCORING_PROGRESS, None, None
    session.add(call)
    session.commit()
    leases.record_seen(request.app.state.engine, worker_id or "unknown", tx.model)
    request.app.state.jobs.put((call.id, "rescore"))   # scored by this server's worker thread
    return {"status": "scoring"}


class FailIn(BaseModel):
    error: str = Field(default="", max_length=10_000)


@router.post("/jobs/{call_id}/fail")
def fail(call_id: str, body: FailIn, session: SessionDep, worker_id: WorkerId) -> dict[str, Any]:
    call = session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, "Call not found")
    if call.transcript_json is not None or call.status != CallStatus.transcribing:
        return {"status": call.status.value}    # already moved on
    if not leases.holds_lease(call, worker_id):
        raise HTTPException(409, "This worker doesn't hold the lease on that call")
    leases.release(call, body.error.strip() or "Transcriber reported a failure")
    session.add(call)
    session.commit()
    return {"status": call.status.value, "attempts": call.attempts}
