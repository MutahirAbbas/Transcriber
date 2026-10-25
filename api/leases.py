"""Remote mode: leases on queued calls for the Mac transcription worker.

A worker claims the oldest queued call that has no transcript; the claim is a
lease (15 min, renewed by heartbeats). If the lease runs out, or the worker
reports a failure, the call goes back to the queue — until it has been tried
MAX_ATTEMPTS times, then it's an error.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, col, select

from api.models import Call, CallStatus, WorkerSeen, utcnow

log = logging.getLogger(__name__)

LEASE = timedelta(minutes=15)
MAX_ATTEMPTS = 3
REAP_EVERY_S = 60.0
ONLINE_WITHIN = timedelta(seconds=90)   # idle workers poll every 10 s, busy ones heartbeat every 30 s
MAX_ERROR_CHARS = 500
_CLAIM_TRIES = 5


def claim(engine: Engine, worker_id: str, now: datetime | None = None) -> Call | None:
    """Lease the oldest queued call that needs transcribing, or None if there isn't one.

    Compare-and-set: the UPDATE only succeeds while the row is still `queued`, so two
    workers racing for the same call can't both get it; the loser tries the next one."""
    for _ in range(_CLAIM_TRIES):
        now = now or utcnow()
        with Session(engine) as s:
            call_id = s.exec(
                select(Call.id)
                .where(Call.status == CallStatus.queued, col(Call.transcript_json).is_(None))
                .order_by(col(Call.uploaded_at)).limit(1)
            ).first()
            if call_id is None:
                return None
            try:
                won = s.exec(
                    update(Call)
                    .where(col(Call.id) == call_id, col(Call.status) == CallStatus.queued)
                    .values(status=CallStatus.transcribing, lease_until=now + LEASE, attempts=Call.attempts + 1,
                            worker_id=worker_id, progress=0.0, error=None)
                ).rowcount == 1
                s.commit()
            except OperationalError:   # lost a write race on the database lock; try again
                s.rollback()
                continue
            if won:
                return s.get(Call, call_id)
    return None


def holds_lease(call: Call, worker_id: str, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return (call.status == CallStatus.transcribing and call.worker_id == worker_id
            and call.lease_until is not None and call.lease_until > now)


def release(call: Call, error: str) -> None:
    """Give a leased call back: to the queue while it has attempts left, else to `error`."""
    error = error[:MAX_ERROR_CHARS]
    call.lease_until, call.worker_id, call.progress = None, None, 0.0
    if call.attempts < MAX_ATTEMPTS:
        call.status, call.error = CallStatus.queued, error     # kept for the record; UI shows it waiting
    else:
        call.status, call.error = CallStatus.error, f"Transcription failed after {call.attempts} attempts: {error}"


def requeue_expired(engine: Engine, now: datetime | None = None) -> int:
    now = now or utcnow()
    with Session(engine) as s:
        expired = s.exec(
            select(Call).where(Call.status == CallStatus.transcribing, col(Call.lease_until) < now)
        ).all()
        for call in expired:
            release(call, "Transcriber stopped responding")
            s.add(call)
        s.commit()
    if expired:
        log.info("requeued %d call(s) whose transcriber lease expired", len(expired))
    return len(expired)


def record_seen(engine: Engine, worker_id: str, model: str | None = None) -> None:
    with Session(engine) as s:
        row = s.get(WorkerSeen, worker_id) or WorkerSeen(id=worker_id, last_seen=utcnow())
        row.last_seen = utcnow()
        if model:
            row.model = model
        s.add(row)
        s.commit()


def worker_status(engine: Engine, now: datetime | None = None) -> dict:
    now = now or utcnow()
    with Session(engine) as s:
        row = s.exec(select(WorkerSeen).order_by(col(WorkerSeen.last_seen).desc())).first()
    if row is None:
        return {"online": False, "last_seen": None, "worker_id": None, "model": None}
    return {
        "online": now - row.last_seen < ONLINE_WITHIN,
        "last_seen": row.last_seen.isoformat().replace("+00:00", "Z"),
        "worker_id": row.id,
        "model": row.model,
    }


class LeaseReaper:
    """Background check every minute: expired leases go back to the queue."""

    def __init__(self, engine: Engine, every_s: float = REAP_EVERY_S) -> None:
        self.engine, self.every_s = engine, every_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="lease-reaper", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        self._stop.set()
        self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.wait(self.every_s):
            try:
                requeue_expired(self.engine)
            except Exception:
                log.exception("lease check failed")
