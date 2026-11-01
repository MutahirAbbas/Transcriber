"""DB queries shared by the routes, the worker and the import script."""
from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy import func, literal_column, or_
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, col, select

from api import derive
from api.media import slugify
from api.models import ON_WORKER, TERMINAL, Agent, Call, CallStatus, utcnow

FILTERS = ("all", "sales", "flagged", "below_target", "below60", "unassigned")
AGENT_AVERAGE_DAYS = 30


def find_or_create_agent(session: Session, slug: str, name: str | None = None) -> Agent:
    agent = session.exec(select(Agent).where(Agent.slug == slug)).first()
    if agent is None:
        agent = Agent(slug=slug, name=name or slug.title())
        session.add(agent)
        session.flush()
    return agent


def agent_for_name(session: Session, name: str) -> Agent | None:
    slug = slugify(name)
    return find_or_create_agent(session, slug, name.strip()) if slug else None


def match_agent(session: Session, name: str) -> Agent | None:
    """An existing agent by name or alias, ignoring case. Never creates one: a name heard on a
    call is only a suggestion until someone assigns it."""
    wanted = name.strip().casefold()
    if not wanted:
        return None
    for agent in session.exec(select(Agent)).all():
        aliases = json.loads(agent.aliases) if agent.aliases else []
        if wanted in {agent.name.casefold(), agent.slug.casefold(), *(a.casefold() for a in aliases)}:
            return agent
    return None


def delete_agent_if_unused(session: Session, agent_id: int | None) -> None:
    if agent_id is None:
        return
    if session.exec(select(Call.id).where(Call.agent_id == agent_id)).first() is None:
        agent = session.get(Agent, agent_id)
        if agent:
            session.delete(agent)


# ---- list / search / filter ------------------------------------------------------
# Same definitions as api.derive, expressed in SQL so paging and totals are right.

def _jx(path: str) -> ColumnElement[Any]:
    return func.json_extract(Call.score_json, path)


_OUTCOME = _jx("$.call_outcome")
_OVERALL = func.coalesce(Call.overall_score, _jx("$.overall_score"))
# v2 scores have findings; older ones have compliance.* (each is zero for the other kind)
_FLAGS = (func.coalesce(func.json_array_length(Call.score_json, "$.compliance.red_flags"), 0)
          + func.coalesce(func.json_array_length(Call.score_json, "$.compliance.missing"), 0)
          + literal_column("(SELECT COUNT(*) FROM json_each(calls.findings) WHERE json_extract(value, '$.kind') = 'flag')"))


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _conditions(filter_: str, q: str | None, target: int) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = [col(Call.cancelled_at).is_(None)]
    if filter_ == "sales":
        conds.append(_OUTCOME == "sale")
    elif filter_ == "flagged":
        conds.append(_FLAGS > 0)
    elif filter_ == "below_target":
        conds.append(_OVERALL < target)
    elif filter_ == "below60":   # the name before the target became a setting
        conds.append(_OVERALL < 60)
    elif filter_ == "unassigned":
        conds.append(col(Call.agent_id).is_(None))
    q = (q or "").strip()
    if q:
        like = f"%{_escape_like(q)}%"
        # "no sale" should find no_sale: spaces become the single-char wildcard
        outcome_like = f"%{_escape_like(q).replace(' ', '_')}%"
        conds.append(or_(
            col(Agent.name).ilike(like, escape="\\"),
            col(Call.id).ilike(like, escape="\\"),
            _OUTCOME.ilike(outcome_like, escape="\\"),
            col(Call.transcript_json).ilike(like, escape="\\"),
        ))
    return conds


def list_calls(
    session: Session, filter_: str = "all", q: str | None = None,
    offset: int = 0, limit: int | None = None, target: int = 60,
) -> tuple[list[tuple[Call, str | None]], int]:
    conds = _conditions(filter_, q, target)
    base = select(Call, Agent.name).join(Agent, col(Call.agent_id) == Agent.id, isouter=True).where(*conds)
    total = session.exec(
        select(func.count()).select_from(Call).join(Agent, col(Call.agent_id) == Agent.id, isouter=True).where(*conds)
    ).one()
    stmt = base.order_by(col(Call.uploaded_at).desc(), col(Call.id).desc()).offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.exec(stmt).all()), total


def in_progress(session: Session, errors_since: datetime) -> list[tuple[Call, str | None]]:
    """Non-terminal rows, plus recent errors so the strip can show what failed."""
    stmt = (
        select(Call, Agent.name).join(Agent, col(Call.agent_id) == Agent.id, isouter=True)
        .where(col(Call.cancelled_at).is_(None), or_(
            col(Call.status).not_in([CallStatus.done, CallStatus.error]),
            (col(Call.status) == CallStatus.error) & (col(Call.uploaded_at) >= errors_since),
        ))
        .order_by(col(Call.uploaded_at))
    )
    return list(session.exec(stmt).all())


DEFAULT_RTF = 0.15        # local Whisper, until there are measured calls
DEFAULT_SEPARATION_RTF = 0.05
MAX_ESTIMATED_PROGRESS = 0.9
RTF_SAMPLES = 10


def recent_rtf(session: Session, provider: str, stereo: bool | None) -> float:
    """Mean latency/duration of the last few finished calls from this provider —
    same channel layout if there are any (stereo split runs Whisper twice)."""
    ratio = (func.json_extract(Call.transcript_json, "$.latency_s")
             / func.json_extract(Call.transcript_json, "$.audio_duration_s"))
    for same_layout in (True, False):
        stmt = select(ratio).where(
            Call.status == CallStatus.done, Call.provider == provider, col(Call.transcript_json).is_not(None),
        )
        if same_layout:
            stmt = stmt.where(Call.stereo == stereo)
        ratios = [r for r in session.exec(stmt.order_by(col(Call.scored_at).desc()).limit(RTF_SAMPLES)).all()
                  if r and r > 0]
        if ratios:
            return sum(ratios) / len(ratios)
    return DEFAULT_RTF


def recent_separation_rtf(session: Session) -> float:
    """Same idea for speaker separation, from the calls that were actually diarized."""
    ratio = (func.json_extract(Call.transcript_json, "$.separation_s")
             / func.json_extract(Call.transcript_json, "$.audio_duration_s"))
    stmt = select(ratio).where(Call.status == CallStatus.done, Call.speaker_source == "diarized")
    ratios = [r for r in session.exec(stmt.order_by(col(Call.scored_at).desc()).limit(RTF_SAMPLES)).all() if r and r > 0]
    return sum(ratios) / len(ratios) if ratios else DEFAULT_SEPARATION_RTF


@dataclass(frozen=True)
class Timing:
    """Measured speeds, for time-left estimates. Worked out once per request."""
    rtf_mono: float
    rtf_stereo: float
    separation_rtf: float


def timing(session: Session, provider: str) -> Timing:
    return Timing(recent_rtf(session, provider, False), recent_rtf(session, provider, True),
                  recent_separation_rtf(session))


def eta_s(call: Call, t: Timing) -> int | None:
    """Seconds left until the transcript is ready: the transcribers report progress as a share
    of the expected time, capped, so at the cap there's no honest figure to give."""
    if not call.duration_s or (call.status != CallStatus.queued and call.status not in ON_WORKER):
        return None
    rtf = t.rtf_stereo if call.stereo else t.rtf_mono + t.separation_rtf
    expected = call.duration_s * rtf
    if call.status == CallStatus.queued:
        return round(expected)
    return None if call.progress >= MAX_ESTIMATED_PROGRESS else max(1, round(expected * (1 - call.progress)))


def neighbours(session: Session, call_id: str) -> tuple[str | None, str | None]:
    """(previous, next) in the list's own order: newest first, over all calls."""
    ids = list(session.exec(
        select(Call.id).where(col(Call.cancelled_at).is_(None))
        .order_by(col(Call.uploaded_at).desc(), col(Call.id).desc())
    ).all())
    if call_id not in ids:
        return None, None
    i = ids.index(call_id)
    return (ids[i - 1] if i > 0 else None, ids[i + 1] if i + 1 < len(ids) else None)


def agent_average(session: Session, agent_id: int | None, now: datetime | None = None) -> int | None:
    if agent_id is None:
        return None
    since = (now or utcnow()) - timedelta(days=AGENT_AVERAGE_DAYS)
    rows = session.exec(select(_OVERALL).where(
        Call.agent_id == agent_id, Call.status == CallStatus.done, col(Call.scored_at) >= since,
    )).all()
    values = [r for r in rows if r is not None]
    return round(mean(values)) if values else None


# ---- saving and removing ------------------------------------------------------------

def apply_score(session: Session, call: Call, score: dict[str, Any], history_extra: dict[str, Any] | None = None) -> None:
    """Store a good, redacted score. A previous score moves to score_history first."""
    if call.score_json:
        history = json.loads(call.score_history) if call.score_history else []
        history.append({
            "scored_at": derive.iso(call.scored_at),
            "scorer_version": call.scorer_version,
            "overall_score": call.overall_score if call.overall_score is not None else derive.overall_score(derive.parse_json(call.score_json)),
            "score": derive.parse_json(call.score_json),
            **(history_extra or {}),
        })
        call.score_history = json.dumps(history, ensure_ascii=False)
    call.score_json = json.dumps(score, ensure_ascii=False)
    call.overall_score = derive.overall_score(score)
    call.scored_at = utcnow()
    call.scorer_version = (score.get("_meta") or {}).get("scorer_version")
    for field in ("findings", "phases", "customer", "sale"):
        value = score.get(field)
        setattr(call, field, None if value is None else json.dumps(value, ensure_ascii=False))
    if call.agent_id is None and isinstance(score.get("agent_name"), str):
        agent = match_agent(session, score["agent_name"])
        call.agent_id = agent.id if agent else None


def purge_call(session: Session, call: Call, uploads_dir: Path | None) -> None:
    """Remove a call and its audio. The caller commits."""
    if uploads_dir is not None:
        for path in uploads_dir.glob(f"{call.id}.*"):
            path.unlink(missing_ok=True)
    session.delete(call)


def agent_name(session: Session, agent_id: int | None) -> str | None:
    agent = session.get(Agent, agent_id) if agent_id is not None else None
    return agent.name if agent else None


# ---- stats -----------------------------------------------------------------------

def week_bounds(today: date) -> tuple[datetime, datetime]:
    """Monday 00:00 → next Monday 00:00 in server local time, as aware datetimes."""
    monday = today - timedelta(days=today.weekday())
    return (datetime.combine(monday, time()).astimezone(),
            datetime.combine(monday + timedelta(days=7), time()).astimezone())


def week_stats(session: Session, today: date, order: Sequence[str]) -> dict[str, Any]:
    start, end = week_bounds(today)
    rows = session.exec(
        select(Call.score_json).where(
            Call.status == CallStatus.done, col(Call.scored_at) >= start, col(Call.scored_at) < end,
        )
    ).all()
    scores = [s for s in (derive.usable_score(derive.parse_json(r)) for r in rows) if s]
    overall = [v for v in (derive.overall_score(s) for s in scores) if v is not None]
    by_cat: dict[str, list[float]] = {}
    for s in scores:
        for cat, v in derive.category_scores(s).items():
            by_cat.setdefault(cat, []).append(v)
    averages = {c: mean(v) for c, v in by_cat.items()}
    weakest = derive.lowest_category(averages, order)
    in_progress = session.exec(
        select(func.count()).select_from(Call).where(col(Call.status).not_in(TERMINAL), col(Call.cancelled_at).is_(None))
    ).one()
    return {
        "calls_scored": len(scores),
        "avg_score": round(mean(overall)) if overall else None,
        "compliance_flags": sum(derive.flag_count(s) for s in scores),
        "flagged_calls": sum(1 for s in scores if derive.flag_count(s)),
        "weakest_category": weakest,
        "weakest_avg": round(averages[weakest], 1) if weakest else None,
        "in_progress": in_progress,
    }
