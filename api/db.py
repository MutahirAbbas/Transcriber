from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from api import models  # noqa: F401  — registers the tables on SQLModel.metadata


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # the worker thread and request threads share the file
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _record) -> None:  # type: ignore[no-untyped-def]
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    SQLModel.metadata.create_all(engine)
    return engine


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session
