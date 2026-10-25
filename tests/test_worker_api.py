from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from api import leases
from api.app import create_app
from api.config import Settings
from api.models import Call, CallStatus, WorkerSeen, utcnow
from api.ratelimit import RateLimiter

TOKEN = "test-worker-token"
H = {"Authorization": f"Bearer {TOKEN}", "X-Worker-Id": "mac-1"}
OTHER = {"Authorization": f"Bearer {TOKEN}", "X-Worker-Id": "mac-2"}


@pytest.fixture
def app(settings: Settings) -> FastAPI:   # overrides conftest's: remote mode
    return create_app(dataclasses.replace(settings, worker_mode="remote", worker_token=TOKEN), start_worker=False)


@pytest.fixture
def queued(settings: Settings, add_call) -> Callable[..., Call]:
    def _add(call_id: str, **kw: Any) -> Call:
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        (settings.uploads_dir / f"{call_id}.wav").write_bytes(b"RIFF-audio-" + call_id.encode())
        return add_call(call_id, status=kw.pop("status", CallStatus.queued), **kw)
    return _add


def _get(app: FastAPI, call_id: str) -> Call:
    with Session(app.state.engine) as s:
        return s.get(Call, call_id)


def _claim(client: TestClient, headers: dict[str, str] = H):
    return client.post("/api/worker/claim", json={"worker_id": headers["X-Worker-Id"]}, headers=headers)


def _transcript(text: str = "hello there", call_id: str = "c1") -> dict[str, Any]:
    return {
        "provider": "whisper_local", "model": "whisper-large-v3-turbo", "call_id": call_id,
        "segments": [{"start": 0.0, "end": 2.0, "text": text, "speaker": None, "confidence": None}],
        "latency_s": 7.2, "audio_duration_s": 120.0, "cost_usd": 0.0,
    }


def _drain(app: FastAPI) -> list[tuple[str, str]]:
    out = []
    while not app.state.jobs.empty():
        out.append(app.state.jobs.get_nowait())
    return out


# ---- auth ----------------------------------------------------------------------------

@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}])
def test_token_required(client: TestClient, headers: dict[str, str]) -> None:
    r = client.post("/api/worker/claim", json={"worker_id": "x"}, headers=headers)
    assert r.status_code == 401
    assert client.get("/api/worker/jobs/c1/audio", headers=headers).status_code == 401


def test_worker_api_refuses_everything_without_a_configured_token(settings: Settings) -> None:
    app = create_app(dataclasses.replace(settings, worker_mode="remote", worker_token=None), start_worker=False)
    with TestClient(app) as c:
        assert c.post("/api/worker/claim", json={"worker_id": "x"}, headers={"Authorization": "Bearer "}).status_code == 401


def test_worker_api_only_exists_in_remote_mode(settings: Settings) -> None:
    with TestClient(create_app(settings, start_worker=False)) as c:
        assert _claim(c).status_code == 404


def test_rate_limited(client: TestClient, app: FastAPI) -> None:
    app.state.worker_limiter = RateLimiter(rate=0.001, burst=2)
    assert [_claim(client).status_code for _ in range(3)] == [204, 204, 429]


# ---- claim ---------------------------------------------------------------------------

def test_claim_leases_the_oldest_queued_call(client: TestClient, app: FastAPI, queued) -> None:
    now = utcnow()
    queued("newer", uploaded_at=now)
    queued("older", uploaded_at=now - timedelta(minutes=5), duration_s=95.0)
    r = _claim(client)
    assert r.status_code == 200
    job = r.json()
    assert job["call_id"] == "older" and job["audio_path_hint"] == "older.wav"
    assert job["stereo"] is True and job["duration_s"] == 95.0 and job["rtf"] == pytest.approx(0.15)
    assert "final expense" in job["keywords"]
    call = _get(app, "older")
    assert (call.status, call.attempts, call.worker_id) == (CallStatus.transcribing, 1, "mac-1")
    assert timedelta(minutes=14) < call.lease_until - utcnow() <= leases.LEASE


def test_claim_skips_calls_that_already_have_a_transcript(client: TestClient, queued) -> None:
    queued("has-tx", transcript=_transcript())
    assert _claim(client).status_code == 204


def test_last_seen_and_status(signed_in: TestClient, app: FastAPI) -> None:
    client = signed_in   # status is a user route
    assert client.get("/api/worker/status").json() == {"online": False, "last_seen": None, "worker_id": None, "model": None}
    client.post("/api/worker/claim", json={"worker_id": "Mutahir's Mac", "model": "whisper-large-v3-turbo"},
                headers={**H, "X-Worker-Id": "Mutahir%27s%20Mac"})
    status = client.get("/api/worker/status").json()
    assert status["online"] and status["worker_id"] == "Mutahir's Mac" and status["model"] == "whisper-large-v3-turbo"
    with Session(app.state.engine) as s:
        row = s.get(WorkerSeen, "Mutahir's Mac")
        row.last_seen = utcnow() - timedelta(hours=2)
        s.add(row)
        s.commit()
    assert client.get("/api/worker/status").json()["online"] is False


# ---- a leased job --------------------------------------------------------------------

def test_audio_only_for_the_lease_holder(client: TestClient, queued) -> None:
    queued("c1")
    _claim(client)
    assert client.get("/api/worker/jobs/c1/audio", headers=OTHER).status_code == 409
    r = client.get("/api/worker/jobs/c1/audio", headers=H)
    assert r.status_code == 200 and r.content == b"RIFF-audio-c1" and r.headers["content-type"] == "audio/wav"


def test_heartbeat_extends_the_lease_and_moves_the_bar(client: TestClient, app: FastAPI, queued) -> None:
    queued("c1")
    _claim(client)
    with Session(app.state.engine) as s:
        call = s.get(Call, "c1")
        call.lease_until = utcnow() + timedelta(seconds=5)
        s.add(call)
        s.commit()
    r = client.post("/api/worker/jobs/c1/heartbeat", json={"stage": "transcribing", "progress": 0.97}, headers=H)
    assert r.status_code == 200
    call = _get(app, "c1")
    assert call.lease_until - utcnow() > timedelta(minutes=14)
    assert call.progress == 0.9
    assert client.post("/api/worker/jobs/c1/heartbeat", json={"progress": 0.5}, headers=OTHER).status_code == 409


def test_transcript_is_redacted_stored_and_scored(client: TestClient, app: FastAPI, queued) -> None:
    queued("c1")
    _claim(client)
    r = client.post("/api/worker/jobs/c1/transcript", json=_transcript("my social is 123-45-6789"), headers=H)
    assert r.status_code == 200 and r.json() == {"status": "scoring"}
    call = _get(app, "c1")
    assert call.status == CallStatus.scoring and call.lease_until is None
    assert (call.provider, call.model) == ("whisper_local", "whisper-large-v3-turbo")
    assert json.loads(call.transcript_json)["segments"][0]["text"] == "my social is [SSN]"
    assert _drain(app) == [("c1", "rescore")]


def test_transcript_post_is_idempotent(client: TestClient, app: FastAPI, queued) -> None:
    queued("c1")
    _claim(client)
    client.post("/api/worker/jobs/c1/transcript", json=_transcript("first"), headers=H)
    stored = _get(app, "c1").transcript_json
    _drain(app)
    r = client.post("/api/worker/jobs/c1/transcript", json=_transcript("second"), headers=H)
    assert r.status_code == 200 and r.json() == {"status": "already received"}
    assert _get(app, "c1").transcript_json == stored
    assert _drain(app) == []


def test_transcript_needs_the_lease(client: TestClient, queued) -> None:
    queued("c1")
    _claim(client)
    assert client.post("/api/worker/jobs/c1/transcript", json=_transcript(), headers=OTHER).status_code == 409


def test_bad_transcript_is_rejected_without_echoing_it(client: TestClient, app: FastAPI, queued) -> None:
    queued("c1")
    _claim(client)
    r = client.post("/api/worker/jobs/c1/transcript", headers=H,
                    json={"segments": [{"text": "card 4111 1111 1111 1111"}], "provider": "x"})
    assert r.status_code == 400
    assert "4111" not in r.text and "segments.0.start" in r.text
    assert _get(app, "c1").transcript_json is None


def test_fail_requeues_until_three_attempts(client: TestClient, app: FastAPI, queued) -> None:
    queued("c1")
    for attempt in (1, 2):
        assert _claim(client).json()["call_id"] == "c1"
        r = client.post("/api/worker/jobs/c1/fail", json={"error": "ffmpeg exploded"}, headers=H)
        assert r.json() == {"status": "queued", "attempts": attempt}
    _claim(client)
    assert client.post("/api/worker/jobs/c1/fail", json={"error": "ffmpeg exploded"}, headers=H).json()["status"] == "error"
    call = _get(app, "c1")
    assert call.status == CallStatus.error and call.error == "Transcription failed after 3 attempts: ffmpeg exploded"
    assert _claim(client).status_code == 204


def test_expired_lease_goes_back_to_the_queue(app: FastAPI, queued) -> None:
    queued("c1", status=CallStatus.transcribing)
    queued("c2", status=CallStatus.transcribing)
    with Session(app.state.engine) as s:
        for call_id, attempts in (("c1", 1), ("c2", 3)):
            call = s.get(Call, call_id)
            call.lease_until, call.attempts, call.worker_id = utcnow() - timedelta(seconds=1), attempts, "mac-1"
            s.add(call)
        s.commit()
    assert leases.requeue_expired(app.state.engine) == 2
    assert _get(app, "c1").status == CallStatus.queued and _get(app, "c1").worker_id is None
    assert _get(app, "c2").status == CallStatus.error
    assert "stopped responding" in _get(app, "c2").error


def test_unexpired_leases_are_left_alone(app: FastAPI, client: TestClient, queued) -> None:
    queued("c1")
    _claim(client)
    assert leases.requeue_expired(app.state.engine) == 0
    assert _get(app, "c1").status == CallStatus.transcribing


def test_no_ssn_shaped_digits_reach_the_database(client: TestClient, app: FastAPI, queued, settings: Settings) -> None:
    queued("c1")
    _claim(client)
    tx = _transcript("ssn 123-45-6789, again 123 45 6789, social 123456789")
    client.post("/api/worker/jobs/c1/transcript", json=tx, headers=H)
    raw = settings.db_path.read_bytes() + (settings.db_path.with_name(settings.db_path.name + "-wal").read_bytes()
                                            if settings.db_path.with_name(settings.db_path.name + "-wal").exists() else b"")
    assert not re.search(rb"\d{3}[- ]?\d{2}[- ]?\d{4}", raw)
