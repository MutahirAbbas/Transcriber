from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import event, inspect
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    # SQLModel's datetime column stores aware values as UTC and reads them back aware
    return datetime.now(timezone.utc)


class CallStatus(str, Enum):
    queued = "queued"
    transcribing = "transcribing"
    separating = "separating"
    scoring = "scoring"
    done = "done"
    error = "error"


TERMINAL = (CallStatus.done, CallStatus.error)
ON_WORKER = (CallStatus.transcribing, CallStatus.separating)   # a transcriber holds the lease


class Agent(SQLModel, table=True):
    __tablename__ = "agents"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    slug: str = Field(index=True, unique=True)
    aliases: str | None = None             # JSON list: other names this agent goes by on calls


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
    speaker_source: str | None = None      # stereo | diarized | provider | none
    waveform: str | None = None            # JSON: 400 peaks, 0–1
    # From the latest good score, unpacked for filters and the detail page (see repo.apply_score)
    overall_score: int | None = None       # computed in code, never the model's figure
    findings: str | None = None            # JSON list
    phases: str | None = None              # JSON list
    customer: str | None = None            # JSON object
    sale: str | None = None                # JSON object
    scorer_version: str | None = None
    score_history: str | None = None       # JSON list: earlier scores, oldest first
    cancelled_at: datetime | None = None   # asked to stop; the row goes when the job does
    # Calls handed over by another system (see api.routes.ingest). `upload` rows leave these alone.
    source: str = Field(default="upload", sa_column_kwargs={"server_default": "'upload'"})   # upload | tqc
    external_id: str | None = Field(default=None, index=True)   # the sender's own id for this call; one row each
    external_meta: str | None = None       # JSON object the sender wants back with the review
    is_test: bool = Field(default=False, sa_column_kwargs={"server_default": "0"})    # made from a test system
    backlog: bool = Field(default=False, sa_column_kwargs={"server_default": "0"})    # waits behind every new call
    score_attempts: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    finished_at: datetime | None = Field(default=None, index=True)   # last time it reached done / error
    started_at: datetime | None = None     # when a transcriber last picked it up
    known_sale: bool = Field(default=False, sa_column_kwargs={"server_default": "0"})   # the sender's record says application


@event.listens_for(Call, "before_insert")
@event.listens_for(Call, "before_update")
def _stamp_finished(_mapper, _connection, call: Call) -> None:  # type: ignore[no-untyped-def]
    """`finished_at` moves every time a call lands on done or error, or is scored again, so
    "what changed since X" never misses a result."""
    if call.status not in TERMINAL:
        return
    attrs = inspect(call).attrs
    if any(attrs[name].history.has_changes() for name in ("status", "score_json", "error")):
        call.finished_at = utcnow()


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
