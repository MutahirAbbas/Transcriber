from __future__ import annotations

import dataclasses
import queue
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from api.app import create_app
from api.config import Settings, load_settings
from api.models import Call, CallStatus
from api.worker import Worker
from tests.conftest import make_score, make_transcript
from tests.test_worker import FakeScorer


@pytest.fixture
def remote_settings(settings: Settings) -> Settings:
    return dataclasses.replace(settings, worker_mode="remote")


@pytest.fixture
def remote_app(remote_settings: Settings) -> FastAPI:
    return create_app(remote_settings, start_worker=False)


@pytest.fixture
def remote_client(remote_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(remote_app) as c:
        yield c


def test_worker_mode_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_MODE", "remote")
    assert load_settings().remote
    monkeypatch.setenv("WORKER_MODE", "local")
    assert not load_settings().remote
    monkeypatch.setenv("WORKER_MODE", "cloud")
    with pytest.raises(ValueError):
        load_settings()


def test_remote_upload_waits_for_a_transcriber(remote_client: TestClient, remote_app: FastAPI, make_wav) -> None:
    r = remote_client.post("/api/calls", files=[("files", ("a_jblake.wav", make_wav().read_bytes(), "audio/wav"))])
    assert r.status_code == 201 and r.json()[0]["status"] == "queued"
    assert remote_app.state.jobs.empty()          # nothing for the in-process worker
    assert remote_client.get("/api/settings").json()["worker_mode"] == "remote"


def test_remote_recovery_only_resumes_scoring(remote_app: FastAPI, add_call, remote_settings: Settings) -> None:
    tx = make_transcript([(0, "hi", "SPEAKER_0")])
    add_call("q", status=CallStatus.queued)
    add_call("t", status=CallStatus.transcribing)
    add_call("s-with-tx", status=CallStatus.scoring, transcript=tx)
    add_call("s-no-tx", status=CallStatus.scoring)
    add_call("d", status=CallStatus.done, score=make_score(70), transcript=tx)
    jobs: queue.Queue = queue.Queue()
    w = Worker(remote_app.state.engine, jobs, remote_settings.uploads_dir, "whisper_local",
               None, FakeScorer, remote=True)
    assert w.recover() == 1
    assert jobs.get_nowait() == ("s-with-tx", "rescore")
    with Session(remote_app.state.engine) as s:
        assert s.get(Call, "q").status == CallStatus.queued
        assert s.get(Call, "t").status == CallStatus.transcribing      # its lease decides
        assert s.get(Call, "s-no-tx").status == CallStatus.queued


def test_remote_worker_never_transcribes(remote_app: FastAPI, add_call, remote_settings: Settings) -> None:
    add_call("c1", status=CallStatus.queued)
    jobs: queue.Queue = queue.Queue()
    w = Worker(remote_app.state.engine, jobs, remote_settings.uploads_dir, "whisper_local",
               None, FakeScorer, remote=True)
    w.start()
    jobs.put(("c1", "full"))
    jobs.join()
    w.stop(timeout=5)
    with Session(remote_app.state.engine) as s:
        assert "doesn't transcribe" in s.get(Call, "c1").error
