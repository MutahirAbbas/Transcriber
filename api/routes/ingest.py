"""The door for another system (TQC) to hand over a call and read reviews back.

Bearer-token auth (INGEST_TOKEN), not the user login and not the worker's token. The
sender pushes a recording with its own id for the call, then asks "what finished since X".
Nothing here ever calls the sender. Never log audio names, transcript text or request bodies.
"""
from __future__ import annotations

import hmac
import json
import re
import secrets
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from api import derive, repo
from api.db import get_session
from api.media import (
    MAX_DURATION_S, MAX_UPLOAD_BYTES, UnreadableAudio, UploadTooLarge, audio_ext, probe_strict, rewrite_audio,
    save_upload,
)
from api.models import TERMINAL, Call, CallStatus

router = APIRouter(prefix="/ingest")

SessionDep = Annotated[Session, Depends(get_session)]

SOURCE = "tqc"
_EXTERNAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
MAX_META_BYTES = 4_000
MIN_CLIP_S = 5.0
CHANGES_MAX = 100


def ingest_auth(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
    ip = request.client.host if request.client else "unknown"
    if not request.app.state.ingest_limiter.allow(ip):
        raise HTTPException(429, "Too many requests")
    token = request.app.state.settings.ingest_token
    if not token or not authorization or not hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
        raise HTTPException(401, "Invalid ingest token", headers={"WWW-Authenticate": "Bearer"})


Authed = Depends(ingest_auth)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:           # SQLite hands datetimes back naive; they were stored as UTC
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _by_external_id(session: Session, external_id: str) -> Call | None:
    return session.exec(select(Call).where(Call.external_id == external_id)).first()


def _receipt(call: Call, created: bool) -> dict[str, Any]:
    return {"call_id": call.id, "external_id": call.external_id, "status": call.status.value, "created": created}


def _call_id(external_id: str) -> str:
    return "tqc-" + re.sub(r"[^A-Za-z0-9_-]+", "-", external_id).strip("-")[:100]


# ---- hand over one call ------------------------------------------------------------

@router.post("/calls", dependencies=[Authed])
def hand_over(
    request: Request,
    response: Response,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    external_id: Annotated[str, Form()],
    agent_name: Annotated[str, Form()] = "",
    meta: Annotated[str, Form()] = "",
    is_test: Annotated[bool, Form()] = False,
    backlog: Annotated[bool, Form()] = False,
    clip_s: Annotated[float | None, Form()] = None,
) -> dict[str, Any]:
    settings = request.app.state.settings
    if not _EXTERNAL_ID.match(external_id):
        raise HTTPException(400, "external_id must be 1-120 letters, digits or . _ : -")
    # the same call handed over twice is the same review: answer with the one we have
    if existing := _by_external_id(session, external_id):
        return _receipt(existing, created=False)

    ext = audio_ext(file.filename or "")
    if ext is None:
        raise HTTPException(400, "Not an audio file")
    meta_obj: Any = None
    if meta:
        if len(meta.encode()) > MAX_META_BYTES:
            raise HTTPException(400, "meta is too large")
        try:
            meta_obj = json.loads(meta)
        except json.JSONDecodeError:
            raise HTTPException(400, "meta must be JSON") from None
        if not isinstance(meta_obj, dict):
            raise HTTPException(400, "meta must be a JSON object")
    if clip_s is not None and not MIN_CLIP_S <= clip_s <= MAX_DURATION_S:
        raise HTTPException(400, f"clip_s must be between {MIN_CLIP_S:g} and {MAX_DURATION_S} seconds")

    call_id = _call_id(external_id)
    if session.get(Call, call_id) is not None:   # two external ids that sanitize to the same name
        raise HTTPException(409, "That external_id collides with another call")
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.uploads_dir / f"{call_id}{ext}"
    # written under a name no other request shares, and only given the call's name once the row is ours
    incoming = settings.uploads_dir / f".incoming-{secrets.token_hex(8)}{ext}"
    row = Call(id=call_id, filename=f"{call_id}{ext}", source=SOURCE, external_id=external_id,
               external_meta=json.dumps(meta_obj, ensure_ascii=False) if meta_obj is not None else None,
               is_test=is_test, backlog=backlog)
    try:
        try:
            save_upload(file.file, incoming, MAX_UPLOAD_BYTES)
        except UploadTooLarge:
            raise HTTPException(413, f"Larger than {MAX_UPLOAD_BYTES // 1_000_000} MB") from None
        try:
            duration, _ = probe_strict(incoming)
            # always rewritten, cut or not: the stored file is one ffmpeg made, never the raw upload
            rewrite_audio(incoming, clip_s if clip_s is not None and duration > clip_s else None)
            duration, stereo = probe_strict(incoming)
            if duration > MAX_DURATION_S:
                raise HTTPException(400, "Longer than 2 hours")
            row.duration_s, row.stereo = duration, stereo
        except UnreadableAudio as e:
            row.status, row.error = CallStatus.error, str(e)   # keep the row so the sender hears why, not the file
        if name := agent_name.strip()[:100]:
            agent = repo.match_agent(session, name) or repo.agent_for_name(session, name)
            row.agent_id = agent.id if agent else None
        session.add(row)
        try:
            session.flush()
        except IntegrityError:
            # the same call arrived twice at once and the other request won
            session.rollback()
            if existing := _by_external_id(session, external_id):
                return _receipt(existing, created=False)
            raise HTTPException(409, "That call is already being received") from None
        if row.status == CallStatus.queued:
            incoming.replace(dest)
        session.commit()
    finally:
        incoming.unlink(missing_ok=True)

    if not settings.remote and row.status == CallStatus.queued:   # remote: waits for a Mac worker
        request.app.state.jobs.put((row.id, "full"))
    response.status_code = 201
    return _receipt(row, created=True)


# ---- read results back -------------------------------------------------------------

def _review(request: Request, session: Session, call: Call) -> dict[str, Any]:
    """Everything the sender stores, so its own pages work when this service is off."""
    card = request.app.state.scorecard
    score = derive.usable_score(derive.parse_json(call.score_json)) if call.status == CallStatus.done else None
    transcript = derive.parse_json(call.transcript_json) if score else None
    meta = (score or {}).get("_meta") or {}
    out: dict[str, Any] = {
        "external_id": call.external_id,
        "call_id": call.id,
        "status": call.status.value,
        "error": call.error if call.status == CallStatus.error else None,
        "is_test": call.is_test,
        "backlog": call.backlog,
        "meta": derive.parse_json(call.external_meta),
        "finished_at": _iso(call.finished_at),
        "duration_s": call.duration_s,
        "agent": repo.agent_name(session, call.agent_id),
    }
    if not score:
        return out
    return {
        **out,
        "scored_at": _iso(call.scored_at),
        "overall_score": call.overall_score,
        "scores": score.get("scores"),
        "summary": score.get("summary"),
        "call_outcome": score.get("call_outcome"),
        "agent_name_heard": score.get("agent_name"),
        "customer": derive.parse_json(call.customer),
        "sale": derive.parse_json(call.sale),
        "findings": derive.parse_json(call.findings) or [],
        "phases": derive.parse_json(call.phases) or [],
        "required_phrases": score.get("required_phrases") or [],
        "work_on_next": score.get("work_on_next"),
        "retention": score.get("retention"),
        "speaker_map": derive.speaker_map(score, transcript),
        "transcript": {
            "segments": [{"start": seg.get("start"), "end": seg.get("end"), "speaker": seg.get("speaker"),
                          "text": seg.get("text")} for seg in (transcript or {}).get("segments", [])],
            "speaker_source": call.speaker_source,
        },
        "engine": {
            "scorer_version": call.scorer_version,
            "scorer_model": meta.get("scorer_model"),
            "requested_model": meta.get("requested_model"),
            "transcriber": f"{call.provider}/{call.model}" if call.provider else None,
            "input_tokens": meta.get("input_tokens"),
            "output_tokens": meta.get("output_tokens"),
            "score_attempts": call.score_attempts,
        },
        # a copy of the names in force when it was scored, so the sender never needs this service to label them
        "scorecard": {
            "version": card.version, "target_score": card.target_score,
            "categories": [{"id": key, "label": card.labels.get(key, key), "weight": card.weights.get(key)}
                           for key in card.categories],
            "phases": card.phases, "required_phrases": card.required_phrases,
            "flag_types": card.flag_types, "coach_types": card.coach_types, "compliance_cap": card.compliance_cap,
            "retention_signals": card.retention_signals,
        },
    }


@router.get("/calls/{external_id}", dependencies=[Authed])
def one(external_id: str, request: Request, session: SessionDep) -> dict[str, Any]:
    call = _by_external_id(session, external_id)
    if call is None or call.source != SOURCE or call.cancelled_at is not None:
        raise HTTPException(404, "Call not found")
    return _review(request, session, call)


@router.get("/changes", dependencies=[Authed])
def changes(
    request: Request,
    session: SessionDep,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=CHANGES_MAX)] = 50,
) -> dict[str, Any]:
    """Every handed-over call that reached done or error after `since`, oldest first.

    Ask again with the returned `cursor` as `since`. A page never splits calls that finished
    at the same instant, so nothing is skipped; a call scored again shows up again."""
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    conds = [Call.source == SOURCE, col(Call.status).in_(TERMINAL), col(Call.finished_at).is_not(None),
             col(Call.cancelled_at).is_(None)]
    if since is not None:
        conds.append(col(Call.finished_at) > since)
    rows = list(session.exec(
        select(Call).where(*conds).order_by(col(Call.finished_at), col(Call.id)).limit(limit + 1)
    ).all())
    more = len(rows) > limit
    rows = rows[:limit]
    if more and rows:   # keep everything that shares the last instant on this page
        last = rows[-1].finished_at
        seen = {r.id for r in rows}
        rows += [r for r in session.exec(select(Call).where(*conds, col(Call.finished_at) == last)).all()
                 if r.id not in seen]
    return {
        "items": [_review(request, session, r) for r in rows],
        "cursor": _iso(rows[-1].finished_at) if rows else _iso(since),
        "more": more,
    }


@router.get("/in-progress", dependencies=[Authed])
def in_progress(session: SessionDep) -> dict[str, Any]:
    """Every handed-over call that is not finished: its stage, when a transcriber picked it up,
    and for one still in line how many calls go before it (new calls before backlog)."""
    rows = session.exec(
        select(Call).where(col(Call.status).not_in(TERMINAL), col(Call.cancelled_at).is_(None),
                           col(Call.transcript_json).is_(None) | (Call.source == SOURCE))
        .order_by(col(Call.backlog), col(Call.uploaded_at))
    ).all()
    items, ahead = [], 0
    for call in rows:       # the order the transcriber takes them in; hand uploads count as "ahead" too
        queued = call.status == CallStatus.queued
        if call.source == SOURCE:
            items.append({
                "external_id": call.external_id, "status": call.status.value, "is_test": call.is_test,
                "backlog": call.backlog, "received_at": _iso(call.uploaded_at),
                "started_at": _iso(call.started_at) if not queued else None,
                "ahead": ahead if queued else None, "attempts": call.attempts,
            })
        if queued:
            ahead += 1
    return {"items": items}


@router.get("/status", dependencies=[Authed])
def status(request: Request, session: SessionDep) -> dict[str, Any]:
    """What the sender shows a manager when reviews are slow: is the transcriber there, and how deep is the queue."""
    from api import leases

    waiting = session.exec(
        select(Call).where(Call.source == SOURCE, col(Call.status).not_in(TERMINAL), col(Call.cancelled_at).is_(None))
        .order_by(col(Call.uploaded_at))
    ).all()
    worker = leases.worker_status(request.app.state.engine) if request.app.state.settings.remote else None
    return {
        "waiting": len(waiting),
        "waiting_backlog": sum(1 for c in waiting if c.backlog),
        "oldest_waiting_since": _iso(waiting[0].uploaded_at) if waiting else None,
        "worker": worker,
    }
