from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    # SQLModel's datetime column stores aware values as UTC and reads them back aware
    return datetime.now(timezone.utc)


class CallStatus(str, Enum):
    queued = "queued"
    transcribing = "transcribing"
    scoring = "scoring"
    done = "done"
    error = "error"


TERMINAL = (CallStatus.done, CallStatus.error)


class Agent(SQLModel, table=True):
    __tablename__ = "agents"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    slug: str = Field(index=True, unique=True)


class Call(SQLModel, table=True):
    __tablename__ = "calls"

    id: str = Field(primary_key=True)
    filename: str
    agent_id: int | None = Field(default=None, foreign_key="agents.id", index=True)
    uploaded_at: datetime = Field(default_factory=utcnow, index=True)
    duration_s: float | None = None
    stereo: bool | None = None
    status: CallStatus = Field(default=CallStatus.queued, index=True)
    progress: float = 0.0
    error: str | None = None
    provider: str | None = None
    model: str | None = None
    transcript_json: str | None = None
    score_json: str | None = None
    scored_at: datetime | None = None
