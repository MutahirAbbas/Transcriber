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
    # remote mode: the Mac worker currently holding this call (see api.leases)
    lease_until: datetime | None = None
    attempts: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    worker_id: str | None = None


class User(SQLModel, table=True):
    """Someone who can sign in. Created with `python -m api.users add` — there's no sign-up."""

    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)   # stored lower-cased
    password_hash: str                            # argon2
    created_at: datetime = Field(default_factory=utcnow)
    last_login: datetime | None = None


class UserSession(SQLModel, table=True):
    """A signed-in browser. The cookie holds a random token; only its hash is stored, so
    logout, password reset and user removal revoke sessions server-side."""

    __tablename__ = "sessions"

    id: str = Field(primary_key=True)                        # sha256 of the cookie's token
    user_id: int = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime


class WorkerSeen(SQLModel, table=True):
    """Last contact from each remote transcription worker, for the sidebar status."""

    __tablename__ = "workers"

    id: str = Field(primary_key=True)      # WORKER_ID
    last_seen: datetime
    model: str | None = None               # transcriber model from its last transcript
