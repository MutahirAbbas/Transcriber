"""The Mac worker against the real worker API over loopback HTTP, with a fake transcriber."""
from __future__ import annotations

import dataclasses
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlmodel import Session

from api.app import create_app
from api.config import Settings
from api.models import Call, CallStatus
from transcribers.base import Segment, Transcriber, TranscriptResult
from worker import remote_worker as rw

TOKEN = "test-worker-token"


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(dataclasses.replace(settings, worker_mode="remote", worker_token=TOKEN), start_worker=False)


class FakeTranscriber(Transcriber):
    name = "whisper_local"
    model = "mlx-community/whisper-large-v3-turbo"
    calls: list[dict[str, Any]] = []
    raise_: BaseException | None = None

    def __init__(self, stereo: bool = False) -> None:
        self.stereo = stereo

    def transcribe(self, audio_path: Path, call_id: str, keywords: list[str] | None = None) -> TranscriptResult:
        FakeTranscriber.calls.append({"audio": audio_path.read_bytes(), "path": audio_path, "stereo": self.stereo,
                                      "keywords": keywords})
        if FakeTranscriber.raise_:
            raise FakeTranscriber.raise_
        return TranscriptResult(provider="whisper_local", model="whisper-large-v3-turbo", call_id=call_id,
                                segments=[Segment(0.0, 2.0, "call me on 202-555-0147", "SPEAKER_0")],
                                latency_s=3.0, audio_duration_s=60.0)


@pytest.fixture(autouse=True)
def fake_transcriber(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    FakeTranscriber.calls, FakeTranscriber.raise_ = [], None
    monkeypatch.setattr(rw, "make_transcriber", lambda provider, stereo: FakeTranscriber(stereo))
    yield


@pytest.fixture
def server_url(app: FastAPI) -> Iterator[str]:
    """The app on a real loopback port: the worker uses a real httpx client, as on the Mac."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def _client(url: str, token: str, worker_id: str) -> httpx.Client:
    # the same headers main() sends
    return httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}",
                                               "X-Worker-Id": rw.quote(worker_id, safe="")})


@pytest.fixture
def worker(server_url: str) -> Iterator[rw.RemoteWorker]:
    cfg = rw.Config(server_url=server_url, token=TOKEN, worker_id="Mutahir's Mac", transcriber="whisper_local")
    with _client(server_url, TOKEN, cfg.worker_id) as client:
        yield rw.RemoteWorker(cfg, client, threading.Event())


@pytest.fixture
def queued(settings: Settings, add_call):
    def _add(call_id: str, **kw: Any) -> Call:
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        (settings.uploads_dir / f"{call_id}.mp3").write_bytes(b"ID3-" + call_id.encode())
        return add_call(call_id, status=CallStatus.queued, **kw)
    return _add


def _get(app: FastAPI, call_id: str) -> Call:
    with Session(app.state.engine) as s:
        return s.get(Call, call_id)


def test_job_end_to_end(worker: rw.RemoteWorker, app: FastAPI, queued) -> None:
    queued("c1")
    job = worker.claim()
    assert job and job["call_id"] == "c1"
    assert _get(app, "c1").worker_id == "Mutahir's Mac"
    assert worker.process(job) == "ok"
    seen = FakeTranscriber.calls[0]
    assert seen["audio"] == b"ID3-c1" and seen["path"].suffix == ".mp3" and seen["stereo"] is True
    assert "final expense" in seen["keywords"]
    assert not seen["path"].parent.exists()                       # temp copy deleted
    call = _get(app, "c1")
    assert call.status == CallStatus.scoring
    assert json.loads(call.transcript_json)["segments"][0]["text"] == "call me on [PHONE]"
    assert app.state.jobs.get_nowait() == ("c1", "rescore")
    assert worker.claim() is None


def test_transcription_error_is_reported_and_requeued(worker: rw.RemoteWorker, app: FastAPI, queued) -> None:
    queued("c1")
    FakeTranscriber.raise_ = RuntimeError("model blew up")
    assert worker.process(worker.claim()) == "failed (RuntimeError)"
    call = _get(app, "c1")
    assert (call.status, call.attempts, call.error) == (CallStatus.queued, 1, "RuntimeError: model blew up")
    assert not FakeTranscriber.calls[0]["path"].parent.exists()


def test_stop_mid_job_gives_the_call_back(worker: rw.RemoteWorker, app: FastAPI, queued) -> None:
    queued("c1")
    FakeTranscriber.raise_ = rw.Stop()
    assert worker.process(worker.claim()) == "stopped"
    assert _get(app, "c1").status == CallStatus.queued


def test_lost_lease_drops_the_job_quietly(worker: rw.RemoteWorker, app: FastAPI, queued) -> None:
    queued("c1")
    job = worker.claim()
    with Session(app.state.engine) as s:   # someone else holds it now
        call = s.get(Call, "c1")
        call.worker_id = "another-mac"
        s.add(call)
        s.commit()
    assert worker.process(job) == "lease lost"
    assert FakeTranscriber.calls == []
    assert _get(app, "c1").transcript_json is None


def test_run_polls_until_stopped(worker: rw.RemoteWorker, app: FastAPI, queued, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rw, "IDLE_SLEEP_S", 0.01)
    queued("c1")
    queued("c2")
    t = threading.Thread(target=worker.run)
    t.start()
    for _ in range(500):
        if _get(app, "c2").status == CallStatus.scoring:
            break
        threading.Event().wait(0.01)
    worker.stop.set()
    t.join(5)
    assert not t.is_alive()
    assert [_get(app, c).status for c in ("c1", "c2")] == [CallStatus.scoring, CallStatus.scoring]


def test_run_backs_off_when_the_server_rejects_the_token(server_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []
    cfg = rw.Config(server_url, "wrong", "mac", "whisper_local")
    with _client(server_url, "wrong", "mac") as client:
        w = rw.RemoteWorker(cfg, client, threading.Event())
        monkeypatch.setattr(w.stop, "wait", lambda s: waits.append(s) or len(waits) >= 4)
        monkeypatch.setattr(w.stop, "is_set", lambda: len(waits) >= 4)
        w.run()
    assert waits == [5.0, 10.0, 20.0, 40.0]


def test_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("SERVER_URL", "WORKER_TOKEN", "WORKER_ID", "TRANSCRIBER"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit):
        rw.load_config(tmp_path / "missing.env")
    env = tmp_path / "worker.env"
    env.write_text('SERVER_URL=https://calls.example/\nWORKER_TOKEN=abc\nWORKER_ID="Mutahir\'s Mac"\n')
    cfg = rw.load_config(env)
    assert (cfg.server_url, cfg.token, cfg.worker_id, cfg.transcriber) == (
        "https://calls.example", "abc", "Mutahir's Mac", "whisper_local")


@pytest.mark.parametrize("hint, name", [("c1.mp3", "audio.mp3"), ("c1.WAV", "audio.WAV"),
                                        ("../../etc/passwd", "audio.audio"), ("", "audio.audio")])
def test_local_name(hint: str, name: str) -> None:
    assert rw._local_name(hint) == name
