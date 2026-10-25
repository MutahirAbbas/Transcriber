"""A8: the deploy brief's test list, end to end — claim atomicity, worker auth vs sessions,
the remote flow through the real scoring thread, a dead worker, and local dev unchanged."""
from __future__ import annotations

import dataclasses
import json
import re
import threading
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from api import leases
from api.app import create_app
from api.config import Settings
from api.db import make_engine
from api.models import Call, CallStatus, utcnow
from tests.conftest import sign_in
from tests.test_worker import FakePipeline, FakeScorer

TOKEN = "flow-token"
MAC = {"Authorization": f"Bearer {TOKEN}", "X-Worker-Id": "mac-1"}


def _remote(settings: Settings, **kw: Any) -> FastAPI:
    return create_app(dataclasses.replace(settings, worker_mode="remote", worker_token=TOKEN), **kw)


def _wait_for(pred: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def _status(app: FastAPI, call_id: str) -> CallStatus:
    with Session(app.state.engine) as s:
        return s.get(Call, call_id).status


def _upload(client: TestClient, make_wav, name: str = "20261026-090000_jblake.wav") -> str:
    r = client.post("/api/calls", files=[("files", (name, make_wav(name).read_bytes(), "audio/wav"))])
    assert r.status_code == 201, r.text
    return r.json()[0]["id"]


def _transcript(call_id: str, text: str) -> dict[str, Any]:
    return {"provider": "whisper_local", "model": "whisper-large-v3-turbo", "call_id": call_id,
            "segments": [{"start": 0.0, "end": 2.0, "text": text, "speaker": "SPEAKER_0", "confidence": None}],
            "latency_s": 0.5, "audio_duration_s": 1.0, "cost_usd": 0.0}


# ---- claim atomicity -----------------------------------------------------------------

def test_two_claims_one_job_one_204(settings: Settings, make_wav) -> None:
    app = _remote(settings, start_worker=False)
    with TestClient(app, base_url="https://testserver") as c:
        _upload(sign_in(c, app), make_wav)
        first = c.post("/api/worker/claim", json={"worker_id": "mac-1"}, headers=MAC)
        second = c.post("/api/worker/claim", json={"worker_id": "mac-2"}, headers={**MAC, "X-Worker-Id": "mac-2"})
    assert first.status_code == 200 and second.status_code == 204


def test_racing_workers_never_share_a_call(settings: Settings, add_call) -> None:
    app = _remote(settings, start_worker=False)
    for i in range(3):
        add_call(f"c{i}", status=CallStatus.queued)
    barrier = threading.Barrier(8)
    won: list[str | None] = [None] * 8

    def race(n: int) -> None:
        barrier.wait()
        call = leases.claim(app.state.engine, f"mac-{n}")
        won[n] = call.id if call else None

    threads = [threading.Thread(target=race, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    claimed = [c for c in won if c]
    assert sorted(claimed) == ["c0", "c1", "c2"]          # each call exactly once
    with Session(app.state.engine) as s:
        assert all(s.get(Call, f"c{i}").attempts == 1 for i in range(3))


# ---- worker auth vs user sessions ----------------------------------------------------

def test_worker_routes_reject_session_cookies_and_accept_only_the_token(settings: Settings, make_wav) -> None:
    app = _remote(settings, start_worker=False)
    with TestClient(app, base_url="https://testserver") as c:
        _upload(sign_in(c, app), make_wav)                           # c now carries a session cookie
        assert c.post("/api/worker/claim", json={"worker_id": "x"}).status_code == 401
        assert c.post("/api/worker/claim", json={"worker_id": "x"},
                      headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.post("/api/worker/claim", json={"worker_id": "mac-1"}, headers=MAC).status_code == 200
    with TestClient(app, base_url="https://testserver") as bare:     # token, no cookie: fine
        assert bare.post("/api/worker/claim", json={"worker_id": "mac-1"}, headers=MAC).status_code == 204


def test_the_worker_token_doesnt_open_user_routes(settings: Settings) -> None:
    with TestClient(_remote(settings, start_worker=False), base_url="https://testserver") as c:
        assert c.get("/api/calls", headers=MAC).status_code == 401


# ---- the remote flow, through the real scoring thread ---------------------------------

@pytest.fixture
def live_remote(settings: Settings) -> Iterator[tuple[TestClient, FastAPI]]:
    app = _remote(settings, start_worker=True, make_scorer=FakeScorer)
    with TestClient(app, base_url="https://testserver") as c:
        yield sign_in(c, app), app


def test_remote_call_end_to_end(live_remote: tuple[TestClient, FastAPI], make_wav) -> None:
    client, app = live_remote
    call_id = _upload(client, make_wav)
    assert client.get("/api/calls/in-progress").json()[0]["status"] == "queued"

    job = client.post("/api/worker/claim", json={"worker_id": "mac-1"}, headers=MAC).json()
    assert job["call_id"] == call_id
    audio = client.get(f"/api/worker/jobs/{call_id}/audio", headers=MAC)
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    client.post(f"/api/worker/jobs/{call_id}/heartbeat", json={"stage": "transcribing", "progress": 0.5}, headers=MAC)
    assert client.get(f"/api/calls/{call_id}").json()["progress"] == 0.5
    r = client.post(f"/api/worker/jobs/{call_id}/transcript", headers=MAC,
                    json=_transcript(call_id, "my social is 123-45-6789, call 202-555-0147"))
    assert r.status_code == 200

    _wait_for(lambda: _status(app, call_id) == CallStatus.done)    # scored on the server
    detail = client.get(f"/api/calls/{call_id}").json()
    assert detail["overall_score"] == 72 and detail["agent"] == "Jblake"
    assert detail["transcript"]["segments"][0]["text"] == "my social is [SSN], call [PHONE]"
    assert not re.search(r"\d{3}-\d{2}-\d{4}", json.dumps(detail))
    assert client.get("/api/worker/status").json()["online"] is True
    assert client.get(f"/api/calls/{call_id}/transcript.txt").status_code == 200


def test_dead_worker_lease_expires_and_the_call_completes_later(live_remote: tuple[TestClient, FastAPI],
                                                                 make_wav) -> None:
    client, app = live_remote
    call_id = _upload(client, make_wav)
    client.post("/api/worker/claim", json={"worker_id": "mac-1"}, headers=MAC)   # …and then the Mac dies

    later = utcnow() + leases.LEASE + timedelta(seconds=1)
    assert leases.requeue_expired(app.state.engine, now=later) == 1
    assert _status(app, call_id) == CallStatus.queued

    job = client.post("/api/worker/claim", json={"worker_id": "mac-1"}, headers=MAC).json()   # restarted
    assert job["call_id"] == call_id
    client.post(f"/api/worker/jobs/{call_id}/transcript", headers=MAC, json=_transcript(call_id, "hello"))
    _wait_for(lambda: _status(app, call_id) == CallStatus.done)
    with Session(app.state.engine) as s:
        assert s.get(Call, call_id).attempts == 2


# ---- local dev is unchanged ----------------------------------------------------------

def test_local_dev_transcribes_in_process_without_sign_in(settings: Settings, make_wav) -> None:
    # conftest settings are local dev: WORKER_MODE=local, AUTH_DISABLED=1
    pipeline = FakePipeline(make_engine(settings.db_path))   # stands in for pipeline.process_call
    app = create_app(settings, start_worker=True, make_scorer=FakeScorer, process_call=pipeline)
    with TestClient(app) as c:                                # plain http, no cookie
        call_id = _upload(c, make_wav)
        _wait_for(lambda: c.get(f"/api/calls/{call_id}").json()["status"] == "done")
        assert pipeline.kwargs[0]["redact"] is not None
        assert c.post("/api/worker/claim", json={"worker_id": "x"}, headers=MAC).status_code == 404
