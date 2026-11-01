from __future__ import annotations

import json
import os
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from api import derive, leases, repo
from api.db import get_session
from api.models import Agent

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


def _agent(a: Agent) -> dict[str, Any]:
    return {"id": a.id, "name": a.name, "slug": a.slug, "aliases": json.loads(a.aliases) if a.aliases else []}


@router.get("/stats")
def stats(request: Request, session: SessionDep) -> dict[str, Any]:
    card = request.app.state.scorecard
    stats = repo.week_stats(session, date.today(), card.categories)
    return {
        **stats,
        "target_score": card.target_score,
        "weakest_label": derive.category_label(stats["weakest_category"], card.labels) or None,
    }


@router.get("/agents")
def agents(session: SessionDep) -> list[dict[str, Any]]:
    rows = session.exec(select(Agent).order_by(col(Agent.name))).all()
    return [_agent(a) for a in rows]


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.post("/agents", status_code=201)
def create_agent(body: AgentIn, session: SessionDep) -> dict[str, Any]:
    """Find or create by name, so assigning a name heard on a call can't make a duplicate."""
    agent = repo.match_agent(session, body.name) or repo.agent_for_name(session, body.name)
    if agent is None:
        raise HTTPException(422, "That name has no letters or numbers")
    session.commit()
    return _agent(agent)


@router.get("/settings")
def settings(request: Request) -> dict[str, str]:
    return {**request.app.state.transcriber_info, "scorer_model": os.environ.get("SCORER_MODEL", "claude-sonnet-5-5")}


@router.get("/worker/status")
def worker_status(request: Request) -> dict[str, Any]:
    """Remote transcription worker: {online, last_seen, worker_id, model}."""
    return leases.worker_status(request.app.state.engine)
