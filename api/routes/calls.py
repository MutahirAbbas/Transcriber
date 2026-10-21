from __future__ import annotations

import csv
import io
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from sqlmodel import Session

from api import derive, repo
from api.db import get_session
from api.media import (
    AUDIO_TYPES, MAX_DURATION_S, UnreadableAudio, agent_slug_from_filename, audio_ext, find_audio, make_call_id,
    probe,
)
from api.models import TERMINAL, Agent, Call, CallStatus, utcnow

router = APIRouter()

MAX_FILES = 20
ERRORS_VISIBLE_FOR = timedelta(hours=1)

SessionDep = Annotated[Session, Depends(get_session)]
FilterParam = Annotated[str, Query(alias="filter", pattern="^(all|sales|flagged|below60)$")]


def _order(request: Request) -> list[str]:
    return request.app.state.scorecard.categories


def _get_call(session: Session, call_id: str) -> Call:
    call = session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, "Call not found")
    return call


# ---- upload ------------------------------------------------------------------------

@router.post("/calls", status_code=201)
def upload_calls(
    request: Request,
    session: SessionDep,
    files: Annotated[list[UploadFile], File()],
    agent: Annotated[str, Form()] = "",
) -> list[dict[str, Any]]:
    settings = request.app.state.settings
    if not 1 <= len(files) <= MAX_FILES:
        raise HTTPException(400, f"Upload between 1 and {MAX_FILES} files at a time")
    for f in files:
        if audio_ext(f.filename or "") is None:
            raise HTTPException(400, f"{f.filename} is not an audio file ({', '.join(AUDIO_TYPES)})")

    chosen: Agent | None = None
    if agent:
        chosen = session.get(Agent, int(agent)) if agent.isdigit() else None
        if chosen is None:
            raise HTTPException(400, "Unknown agent")

    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[UploadFile, str, Path]] = []
    probed: dict[str, tuple[float, bool] | str] = {}
    try:
        for f in files:
            name = f.filename or ""
            call_id = make_call_id(name)
            dest = settings.uploads_dir / f"{call_id}{audio_ext(name)}"
            with dest.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            saved.append((f, call_id, dest))
            try:
                probed[call_id] = probe(dest)
            except UnreadableAudio as e:
                probed[call_id] = str(e)
                dest.unlink(missing_ok=True)   # keep the error row, not the file
        too_long = [f.filename for f, cid, _ in saved
                    if isinstance(probed[cid], tuple) and probed[cid][0] > MAX_DURATION_S]
        if too_long:
            raise HTTPException(400, f"Longer than 2 hours: {', '.join(n or '' for n in too_long)}")
    except BaseException:
        for _, _, dest in saved:
            dest.unlink(missing_ok=True)
        raise

    created: list[Call] = []
    for f, call_id, _ in saved:
        name = f.filename or ""
        row = Call(id=call_id, filename=name)
        if chosen is not None:
            row.agent_id = chosen.id
        elif slug := agent_slug_from_filename(name):
            row.agent_id = repo.find_or_create_agent(session, slug).id
        result = probed[call_id]
        if isinstance(result, str):
            row.status, row.error = CallStatus.error, result
        else:
            row.duration_s, row.stereo = result
        session.add(row)
        created.append(row)
    session.commit()

    for row in created:
        if row.status == CallStatus.queued:
            request.app.state.jobs.put((row.id, "full"))
    return [derive.call_summary(r, repo.agent_name(session, r.agent_id), _order(request)) for r in created]


# ---- list / export -----------------------------------------------------------------

@router.get("/calls")
def list_calls(
    request: Request,
    session: SessionDep,
    filter_: FilterParam = "all",
    q: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 25,
) -> dict[str, Any]:
    rows, total = repo.list_calls(session, filter_, q, offset=(page - 1) * page_size, limit=page_size)
    return {"items": [derive.call_summary(c, name, _order(request)) for c, name in rows], "total": total}


def _csv_cell(value: Any) -> Any:
    # keep spreadsheet apps from evaluating text as a formula
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


@router.get("/calls.csv")
def export_csv(request: Request, session: SessionDep, filter_: FilterParam = "all", q: str | None = None) -> Response:
    rows, _ = repo.list_calls(session, filter_, q)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Call", "Uploaded", "Agent", "Outcome", "Length", "Score", "Flags", "Weakest", "Status"])
    for call, name in rows:
        s = derive.call_summary(call, name, _order(request))
        w.writerow([_csv_cell(v) for v in (
            call.id,
            call.uploaded_at.astimezone().strftime("%Y-%m-%d %H:%M"),
            name or "",
            derive.OUTCOME_LABELS.get(s["outcome"] or "", s["outcome"] or ""),
            derive.fmt_ts(call.duration_s) if call.duration_s is not None else "",
            "" if s["overall_score"] is None else s["overall_score"],
            s["flag_count"],
            derive.category_label(s["weakest_category"]),
            call.status.value,
        )])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="calls.csv"'})


@router.get("/calls/in-progress")
def calls_in_progress(request: Request, session: SessionDep) -> list[dict[str, Any]]:
    rows = repo.in_progress(session, errors_since=utcnow() - ERRORS_VISIBLE_FOR)
    return [derive.call_summary(c, name, _order(request)) for c, name in rows]


# ---- one call ----------------------------------------------------------------------

@router.get("/calls/{call_id}")
def get_call(call_id: str, request: Request, session: SessionDep) -> dict[str, Any]:
    call = _get_call(session, call_id)
    transcript = derive.parse_json(call.transcript_json)
    score = derive.parse_json(call.score_json)
    card = request.app.state.scorecard
    return {
        **derive.call_summary(call, repo.agent_name(session, call.agent_id), card.categories),
        "transcript": transcript,
        "score": score,
        "speaker_map": derive.speaker_map(score, transcript),
        "rtf": derive.realtime_factor(transcript),
        "scorecard": {"categories": card.categories, "required_phrases": card.required_phrases},
    }


@router.get("/calls/{call_id}/audio")
def get_audio(call_id: str, request: Request, session: SessionDep) -> FileResponse:
    _get_call(session, call_id)
    path = find_audio(request.app.state.settings.uploads_dir, call_id)
    if path is None:
        raise HTTPException(404, "Audio file not found")
    media_type = AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream")
    # FileResponse answers Range requests, so <audio> can seek
    return FileResponse(path, media_type=media_type, headers={"X-Content-Type-Options": "nosniff"})


@router.get("/calls/{call_id}/transcript.txt")
def get_transcript_txt(call_id: str, session: SessionDep) -> PlainTextResponse:
    call = _get_call(session, call_id)
    transcript = derive.parse_json(call.transcript_json)
    if transcript is None:
        raise HTTPException(409, "No transcript yet")
    lines = derive.transcript_lines(transcript, derive.speaker_map(derive.parse_json(call.score_json), transcript))
    return PlainTextResponse("\n".join(lines) + "\n",
                             headers={"Content-Disposition": f'attachment; filename="{call.id}.txt"'})


@router.post("/calls/{call_id}/rescore")
def rescore(call_id: str, request: Request, session: SessionDep) -> dict[str, Any]:
    call = _get_call(session, call_id)
    if not call.transcript_json:
        raise HTTPException(409, "No transcript yet")
    if call.status not in TERMINAL:
        raise HTTPException(409, "Call is already being processed")
    call.status, call.progress, call.error = CallStatus.scoring, 0.95, None
    session.add(call)
    session.commit()
    request.app.state.jobs.put((call.id, "rescore"))
    return derive.call_summary(call, repo.agent_name(session, call.agent_id), _order(request))


@router.delete("/calls/{call_id}", status_code=204)
def delete_call(call_id: str, request: Request, session: SessionDep) -> Response:
    call = _get_call(session, call_id)
    for path in request.app.state.settings.uploads_dir.glob(f"{call.id}.*"):
        path.unlink(missing_ok=True)
    session.delete(call)
    session.commit()
    return Response(status_code=204)
