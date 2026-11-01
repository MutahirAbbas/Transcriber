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
    during: Any = None    # called mid-transcription

    def __init__(self, stereo: bool = False) -> None:
        self.stereo = stereo

    def transcribe(self, audio_path: Path, call_id: str, keywords: list[str] | None = None) -> TranscriptResult:
        FakeTranscriber.calls.append({"audio": audio_path.read_bytes(), "path": audio_path, "stereo": self.stereo,
                                      "keywords": keywords})
        if FakeTranscriber.during:
            FakeTranscriber.during()
        if FakeTranscriber.raise_:
            raise FakeTranscriber.raise_
        return TranscriptResult(provider="whisper_local", model="whisper-large-v3-turbo", call_id=call_id,
                                segments=[Segment(0.0, 2.0, "call me on 202-555-0147", "SPEAKER_0")],
                                latency_s=3.0, audio_duration_s=60.0)


@pytest.fixture(autouse=True)
def fake_transcriber(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    FakeTranscriber.calls, FakeTranscriber.raise_, FakeTranscriber.during = [], None, None
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
    for var in ("SERVER_URL", "WORKER_TOKEN", "WORKER_ID", "TRANSCRIBER", "WORKER_MODE"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit):
        rw.load_config(tmp_path / "missing.env")
    env = tmp_path / "worker.env"
    env.write_text('SERVER_URL=https://calls.example/\nWORKER_TOKEN=abc\nWORKER_ID="Mutahir\'s Mac"\n'
                   'WORKER_MODE=remote-production\n')
    cfg = rw.load_config(env)
    assert (cfg.server_url, cfg.token, cfg.worker_id, cfg.transcriber) == (
        "https://calls.example", "abc", "Mutahir's Mac", "whisper_local")


@pytest.mark.parametrize("url, mode, allowed", [
    ("http://127.0.0.1:8000", "", True),
    ("http://localhost:8000", "local-dev", True),
    ("http://[::1]:8000", "remote", True),
    ("https://calls.tqc4.ai", "remote-production", True),
    ("https://calls.tqc4.ai", "", False),
    ("https://calls.tqc4.ai", "local-dev", False),
    ("https://calls.tqc4.ai", "remote", False),
    ("https://calls.tqc4.ai", "Remote-Production", False),
    ("http://localhost.calls.tqc4.ai", "", False),
    ("http://127.0.0.1@calls.tqc4.ai", "", False),
])
def test_only_production_mode_may_leave_this_machine(url: str, mode: str, allowed: bool) -> None:
    if allowed:
        rw.assert_server_allowed(url, mode)
    else:
        with pytest.raises(SystemExit, match="must be on this machine"):
            rw.assert_server_allowed(url, mode)


def test_load_config_refuses_the_live_site_in_a_dev_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("SERVER_URL", "WORKER_TOKEN", "WORKER_ID", "TRANSCRIBER", "WORKER_MODE"):
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / "worker.env"
    env.write_text("SERVER_URL=https://calls.tqc4.ai\nWORKER_TOKEN=abc\n")
    with pytest.raises(SystemExit, match="must be on this machine"):
        rw.load_config(env)


@pytest.mark.parametrize("hint, name", [("c1.mp3", "audio.mp3"), ("c1.WAV", "audio.WAV"),
                                        ("../../etc/passwd", "audio.audio"), ("", "audio.audio")])
def test_local_name(hint: str, name: str) -> None:
    assert rw._local_name(hint) == name


def test_cancel_reaches_the_worker_on_its_next_heartbeat(
    worker: rw.RemoteWorker, app: FastAPI, queued, signed_in_api, settings: Settings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued("c1")
    monkeypatch.setattr(rw, "HEARTBEAT_S", 0.02)
    job = worker.claim()

    def during() -> None:
        assert signed_in_api("c1").json() == {"status": "cancelling"}
        assert worker.cancelled.wait(5)     # as the signal does on the Mac, this is what stops a model

    FakeTranscriber.during = during
    assert worker.process(job) == "cancelled"
    assert _get(app, "c1") is None and not (settings.uploads_dir / "c1.mp3").exists()
    assert not FakeTranscriber.calls[0]["path"].parent.exists()      # temp copy deleted
    assert app.state.jobs.empty()


def test_a_call_deleted_mid_job_stops_the_worker(worker: rw.RemoteWorker, app: FastAPI, queued, monkeypatch) -> None:
    from api.repo import purge_call
    queued("c1")
    monkeypatch.setattr(rw, "HEARTBEAT_S", 0.02)
    job = worker.claim()

    def during() -> None:
        with Session(app.state.engine) as s:
            purge_call(s, s.get(Call, "c1"), None)
            s.commit()
        assert worker.cancelled.wait(5)

    FakeTranscriber.during = during
    assert worker.process(job) == "cancelled"


def test_a_cancel_after_the_last_heartbeat_is_caught_at_the_post(
    worker: rw.RemoteWorker, app: FastAPI, queued, signed_in_api, settings: Settings,
) -> None:
    queued("c1")
    job = worker.claim()
    FakeTranscriber.during = lambda: signed_in_api("c1")
    assert worker.process(job) == "ok"     # it heard nothing, so it posted, and the server said no thanks
    assert _get(app, "c1") is None and not (settings.uploads_dir / "c1.mp3").exists() and app.state.jobs.empty()


def test_separation_stage_reaches_the_server_and_words_stay_on_the_mac(
    worker: rw.RemoteWorker, app: FastAPI, queued, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[CallStatus] = []
    posted: list[dict[str, Any]] = []

    def separate(res: TranscriptResult, _audio: Path, _stereo: bool, on_start: Any = None) -> TranscriptResult:
        on_start()
        seen.append(_get(app, "c1").status)
        res.segments[0].words = [{"start": 0.0, "end": 1.0, "text": "call"}]
        return dataclasses.replace(res, speaker_source="diarized", separation_s=2.0)

    real_post = worker.post_transcript
    monkeypatch.setattr(worker, "post_transcript", lambda cid, t: (posted.append(t), real_post(cid, t))[1])
    monkeypatch.setattr(rw, "separate_speakers", separate)
    monkeypatch.setattr(rw, "compute_waveform", lambda _p: [0.0, 1.0])
    queued("c1")
    assert worker.process(worker.claim()) == "ok"
    assert seen == [CallStatus.separating]
    assert "words" not in posted[0]["segments"][0] and posted[0]["waveform"] == [0.0, 1.0]
    call = _get(app, "c1")
    assert call.speaker_source == "diarized" and json.loads(call.waveform) == [0.0, 1.0]


@pytest.fixture
def signed_in_api(app: FastAPI):
    """The cancel button, from a signed-in browser (remote mode always has sign-in)."""
    from fastapi.testclient import TestClient

    from tests.conftest import sign_in
    browser = TestClient(app, base_url="https://testserver")
    sign_in(browser, app)
    return lambda call_id: browser.post(f"/api/calls/{call_id}/cancel")
