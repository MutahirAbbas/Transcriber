"""DB queries shared by the routes, the worker and the import script."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from statistics import mean
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, col, select

from api import derive
from api.media import slugify
from api.models import Agent, Call, CallStatus

FILTERS = ("all", "sales", "flagged", "below60")


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
_OVERALL = _jx("$.overall_score")
_FLAGS = (func.coalesce(func.json_array_length(Call.score_json, "$.compliance.red_flags"), 0)
          + func.coalesce(func.json_array_length(Call.score_json, "$.compliance.missing"), 0))


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _conditions(filter_: str, q: str | None) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = []
    if filter_ == "sales":
        conds.append(_OUTCOME == "sale")
    elif filter_ == "flagged":
        conds.append(_FLAGS > 0)
    elif filter_ == "below60":
        conds.append(_OVERALL < 60)
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
    offset: int = 0, limit: int | None = None,
) -> tuple[list[tuple[Call, str | None]], int]:
    conds = _conditions(filter_, q)
    base = select(Call, Agent.name).join(Agent, col(Call.agent_id) == Agent.id, isouter=True).where(*conds)
    total = session.exec(
        select(func.count()).select_from(Call).join(Agent, col(Call.agent_id) == Agent.id, isouter=True).where(*conds)
    ).one()
    stmt = base.order_by(col(Call.uploaded_at).desc()).offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.exec(stmt).all()), total


def in_progress(session: Session, errors_since: datetime) -> list[tuple[Call, str | None]]:
    """Non-terminal rows, plus recent errors so the strip can show what failed."""
    stmt = (
        select(Call, Agent.name).join(Agent, col(Call.agent_id) == Agent.id, isouter=True)
        .where(or_(
            col(Call.status).not_in([CallStatus.done, CallStatus.error]),
            (col(Call.status) == CallStatus.error) & (col(Call.uploaded_at) >= errors_since),
        ))
        .order_by(col(Call.uploaded_at))
    )
    return list(session.exec(stmt).all())


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
    return {
        "calls_scored": len(scores),
        "avg_score": round(mean(overall)) if overall else None,
        "compliance_flags": sum(derive.flag_count(s) for s in scores),
        "weakest_category": derive.lowest_category({c: mean(v) for c, v in by_cat.items()}, order),
    }
