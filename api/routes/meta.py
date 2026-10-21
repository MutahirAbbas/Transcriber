from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, col, select

from api import repo
from api.db import get_session
from api.models import Agent

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/stats")
def stats(request: Request, session: SessionDep) -> dict[str, Any]:
    return repo.week_stats(session, date.today(), request.app.state.scorecard.categories)


@router.get("/agents")
def agents(session: SessionDep) -> list[dict[str, Any]]:
    rows = session.exec(select(Agent).order_by(col(Agent.name))).all()
    return [{"id": a.id, "name": a.name, "slug": a.slug} for a in rows]


@router.get("/settings")
def settings(request: Request) -> dict[str, str]:
    return request.app.state.transcriber_info
