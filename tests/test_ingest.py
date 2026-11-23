"""The door another system (TQC) uses: hand over a call, read reviews back."""
from __future__ import annotations

import dataclasses
import json
import queue
import re
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from api import leases
from api.app import create_app
from api.config import Settings
from api.db import make_engine
from api.models import Call, CallStatus, utcnow
from api.ratelimit import RateLimiter
from api.worker import MAX_SCORE_ATTEMPTS, Worker
from tests.conftest import make_transcript
from tests.test_v2_api import v2_score
from transcribers.base import TranscriptResult

TOKEN = "test-ingest-token"
WORKER_TOKEN = "test-worker-token"
H = {"Authorization": f"Bearer {TOKEN}"}
MAC = {"Authorization": f"Bearer {WORKER_TOKEN}", "X-Worker-Id": "mac-1"}


@pytest.fixture
def app(settings: Settings) -> FastAPI:   # the server as deployed: remote mode, both tokens set
    return create_app(dataclasses.replace(settings, worker_mode="remote", worker_token=WORKER_TOKEN,
                                          ingest_token=TOKEN), start_worker=False)


@pytest.fixture
def hand_over(client: TestClient, make_wav) -> Callable[..., Any]:
    def _send(external_id: str = "opp1:rec1", seconds: float = 2.0, headers: dict[str, str] = H, **form: Any):
        wav = make_wav(f"{re.sub(r'[^A-Za-z0-9]+', '-', external_id)[:40] or 'x'}.wav", seconds=seconds)
        with wav.open("rb") as f:
            return client.post("/api/ingest/calls", headers=headers, files={"file": ("recording.wav", f, "audio/wav")},
                               data={"external_id": external_id, **{k: str(v) for k, v in form.items()}})
    return _send


def _get(app: FastAPI, call_id: str) -> Call:
    with Session(app.state.engine) as s:
        return s.get(Call, call_id)


def _rows(app: FastAPI) -> list[Call]:
    with Session(app.state.engine) as s:
        return list(s.exec(select(Call)).all())


class Scorer:
    def __init__(self, results: list[Any] | None = None) -> None:
        self.results, self.calls = results, 0

    def score(self, res: TranscriptResult) -> dict:
        self.calls += 1
        if self.results is None:
            return v2_score()
        result = self.results[min(self.calls - 1, len(self.results) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


def _finish(app: FastAPI, settings: Settings, call_id: str, scorer: Scorer | None = None) -> Scorer:
    """The Mac posts a transcript, then this server's worker thread scores it."""
    scorer = scorer or Scorer()
    with Session(app.state.engine) as s:
        call = s.get(Call, call_id)
        call.transcript_json = json.dumps(make_transcript([(0.0, "Hello, this is Josh.", "SPEAKER_0")]))
        call.provider, call.model, call.status = "whisper_local", "whisper-large-v3-turbo", CallStatus.scoring
        s.add(call)
        s.commit()
    jobs: queue.Queue = queue.Queue()
    w = Worker(app.state.engine, jobs, settings.uploads_dir, "whisper_local", None, lambda: scorer, remote=True)
    w._thread.start()
    jobs.put((call_id, "rescore"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _get(app, call_id).status not in (CallStatus.done, CallStatus.error):
        time.sleep(0.02)
    w.stop(timeout=2)
    return scorer


# ---- the key -------------------------------------------------------------------------

@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN},
                                     {"Authorization": f"Bearer {WORKER_TOKEN}"}])
def test_every_route_needs_the_ingest_token(client: TestClient, hand_over, headers: dict[str, str]) -> None:
    assert hand_over(headers=headers).status_code == 401
    assert client.get("/api/ingest/changes", headers=headers).status_code == 401
    assert client.get("/api/ingest/calls/opp1:rec1", headers=headers).status_code == 401
    assert client.get("/api/ingest/status", headers=headers).status_code == 401


def test_refuses_everything_when_no_token_is_configured(settings: Settings, make_wav) -> None:
    app = create_app(dataclasses.replace(settings, ingest_token=None), start_worker=False)
    with TestClient(app, base_url="https://testserver") as c:
        assert c.get("/api/ingest/changes", headers={"Authorization": "Bearer "}).status_code == 401
        assert c.get("/api/ingest/changes", headers={"Authorization": "Bearer None"}).status_code == 401


def test_the_ingest_token_does_not_open_the_worker_or_the_app(client: TestClient) -> None:
    assert client.post("/api/worker/claim", json={"worker_id": "x"}, headers=H).status_code == 401
    assert client.get("/api/calls", headers=H).status_code == 401


def test_rate_limited(client: TestClient, app: FastAPI) -> None:
    app.state.ingest_limiter = RateLimiter(rate=0.0, burst=2)
    codes = [client.get("/api/ingest/changes", headers=H).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ---- handing a call over -------------------------------------------------------------

def test_a_call_is_received_marked_and_queued_for_the_mac(app: FastAPI, settings: Settings, hand_over) -> None:
    r = hand_over("opp1:rec1", agent_name="Josh Miller", meta='{"opportunity_id": "abc", "agent_user_id": "u1"}')
    assert r.status_code == 201, r.text
    assert r.json() == {"call_id": "tqc-opp1-rec1", "external_id": "opp1:rec1", "status": "queued", "created": True}
    call = _get(app, "tqc-opp1-rec1")
    assert (call.source, call.external_id, call.is_test, call.backlog) == ("tqc", "opp1:rec1", False, False)
    assert call.duration_s == pytest.approx(2.0, abs=0.1)
    assert call.agent_id is not None
    assert (settings.uploads_dir / "tqc-opp1-rec1.wav").is_file()
    assert not list(settings.uploads_dir.glob(".incoming-*"))
    assert app.state.jobs.empty()            # remote mode: it waits for the Mac, nothing transcribes here


def test_the_same_call_twice_is_one_review(app: FastAPI, hand_over) -> None:
    first = hand_over("opp1:rec1")
    again = hand_over("opp1:rec1", agent_name="Someone Else", is_test="true")
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == {**first.json(), "created": False}
    rows = _rows(app)
    assert len(rows) == 1 and rows[0].is_test is False


def test_the_database_itself_refuses_a_second_row_for_one_external_id(app: FastAPI) -> None:
    from sqlalchemy.exc import IntegrityError
    with Session(app.state.engine) as s:
        s.add(Call(id="a", filename="a.wav", source="tqc", external_id="same"))
        s.commit()
        s.add(Call(id="b", filename="b.wav", source="tqc", external_id="same"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_hand_uploads_are_untouched_by_the_new_columns(signed_in: TestClient, app: FastAPI, make_wav) -> None:
    with make_wav("20261021-141502_jblake.wav").open("rb") as f:
        r = signed_in.post("/api/calls", files={"files": ("20261021-141502_jblake.wav", f, "audio/wav")})
    assert r.status_code == 201, r.text
    call = _rows(app)[0]
    assert (call.source, call.external_id, call.is_test, call.backlog, call.score_attempts) == ("upload", None, False, False, 0)
    with Session(app.state.engine) as s:    # many hand uploads share "no external id" without clashing
        s.add(Call(id="x1", filename="x1.wav"))
        s.add(Call(id="x2", filename="x2.wav"))
        s.commit()


@pytest.mark.parametrize("external_id", ["", " ", "has space", "a/b", "../x", "x" * 121, "-leading"])
def test_bad_external_ids_are_refused(hand_over, external_id: str, app: FastAPI) -> None:
    assert hand_over(external_id).status_code in (400, 422)
    assert _rows(app) == []


def test_bad_meta_and_bad_clip_are_refused(hand_over, app: FastAPI) -> None:
    assert hand_over("a", meta="not json").status_code == 400
    assert hand_over("b", meta="[1, 2]").status_code == 400
    assert hand_over("c", meta='{"k": "' + "x" * 5000 + '"}').status_code == 400
    assert hand_over("d", clip_s=1).status_code == 400
    assert hand_over("e", clip_s=99999).status_code == 400
    assert _rows(app) == []


def test_only_audio_is_accepted(client: TestClient, app: FastAPI, tmp_path: Path) -> None:
    r = client.post("/api/ingest/calls", headers=H, files={"file": ("page.html", b"<html>", "text/html")},
                    data={"external_id": "x"})
    assert r.status_code == 400
    assert _rows(app) == []


def test_an_unreadable_file_becomes_a_failed_review_the_sender_can_read(client: TestClient, app: FastAPI,
                                                                         settings: Settings) -> None:
    r = client.post("/api/ingest/calls", headers=H, files={"file": ("broken.mp3", b"not audio at all", "audio/mpeg")},
                    data={"external_id": "opp9:rec9"})
    assert r.status_code == 201 and r.json()["status"] == "error"
    assert not list(settings.uploads_dir.glob("*"))            # the file is not kept
    items = client.get("/api/ingest/changes", headers=H).json()["items"]
    assert [(i["external_id"], i["status"], i["error"]) for i in items] == [("opp9:rec9", "error", "Not a readable audio file")]


def test_an_old_hour_long_file_is_cut_to_the_length_asked_for(app: FastAPI, hand_over) -> None:
    r = hand_over("old:1", seconds=30.0, clip_s=10)
    assert r.status_code == 201, r.text
    assert _get(app, "tqc-old-1").duration_s == pytest.approx(10.0, abs=0.2)


def test_the_stored_file_is_one_we_wrote_not_the_raw_upload(client: TestClient, app: FastAPI, settings: Settings,
                                                           make_wav) -> None:
    raw = make_wav("tagged.wav", seconds=2.0).read_bytes() + b"LIST\x10\x00\x00\x00INFOtrailing-junk-from-sender"
    r = client.post("/api/ingest/calls", headers=H, files={"file": ("tagged.wav", raw, "audio/wav")},
                    data={"external_id": "raw:1"})
    assert r.status_code == 201 and r.json()["status"] == "queued", r.text
    stored = (settings.uploads_dir / "tqc-raw-1.wav").read_bytes()
    assert stored != raw and b"trailing-junk-from-sender" not in stored
    assert _get(app, "tqc-raw-1").duration_s == pytest.approx(2.0, abs=0.1)


def test_a_file_shorter_than_the_cut_keeps_its_full_length(app: FastAPI, hand_over) -> None:
    hand_over("short:1", seconds=6.0, clip_s=600)
    assert _get(app, "tqc-short-1").duration_s == pytest.approx(6.0, abs=0.1)


def test_a_playlist_dressed_as_audio_is_never_followed(client: TestClient, app: FastAPI, settings: Settings,
                                                      tmp_path: Path) -> None:
    secret = tmp_path / "secret.wav"
    secret.write_bytes(b"RIFF....WAVEfmt ")
    for name, body in [
        ("list.mp3", f"#EXTM3U\n#EXTINF:10,\nfile://{secret}\n".encode()),
        ("list.wav", b"#EXTM3U\n#EXTINF:10,\nhttp://127.0.0.1:9/never.ts\n"),
        ("script.ogg", f"ffconcat version 1.0\nfile '{secret}'\n".encode()),
    ]:
        r = client.post("/api/ingest/calls", headers=H, files={"file": (name, body, "audio/mpeg")},
                        data={"external_id": name.replace(".", "-"), "clip_s": "10"})
        assert r.status_code == 201 and r.json()["status"] == "error", name
    assert not list(settings.uploads_dir.glob("*"))


@pytest.mark.parametrize("ext", ["mp3", "m4a", "ogg", "flac", "aac", "webm", "wav"])
def test_every_accepted_format_is_rewritten_and_cut_to_length(tmp_path: Path, ext: str) -> None:
    import subprocess
    from api.media import probe_strict, rewrite_audio
    path = tmp_path / f"tone.{ext}"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
                    "-ac", "1", "-ar", "8000", str(path)], check=True)
    rewrite_audio(path)
    assert probe_strict(path)[0] == pytest.approx(20.0, abs=0.3)
    rewrite_audio(path, 8.0)
    assert probe_strict(path)[0] == pytest.approx(8.0, abs=0.3)


# ---- the queue -----------------------------------------------------------------------

def test_new_calls_go_before_backlog_calls_whatever_their_age(client: TestClient, app: FastAPI, hand_over) -> None:
    hand_over("backlog:old", backlog="true")
    hand_over("backlog:older", backlog="true")
    hand_over("new:1")
    with Session(app.state.engine) as s:     # the backlog rows arrived first
        for i, cid in enumerate(["tqc-backlog-old", "tqc-backlog-older", "tqc-new-1"]):
            row = s.get(Call, cid)
            row.uploaded_at = utcnow() - timedelta(hours=3 - i)
            s.add(row)
        s.commit()
    order = [client.post("/api/worker/claim", json={"worker_id": "mac-1"}, headers=MAC).json()["call_id"] for _ in range(3)]
    assert order == ["tqc-new-1", "tqc-backlog-old", "tqc-backlog-older"]


def test_transcription_gives_up_after_three_tries_and_the_sender_hears(client: TestClient, app: FastAPI, hand_over) -> None:
    hand_over("opp1:rec1")
    for _ in range(leases.MAX_ATTEMPTS):
        assert client.post("/api/worker/claim", json={"worker_id": "mac-1"}, headers=MAC).status_code == 200
        client.post("/api/worker/jobs/tqc-opp1-rec1/fail", json={"error": "ffmpeg exploded"}, headers=MAC)
    assert client.post("/api/worker/claim", json={"worker_id": "mac-1"}, headers=MAC).status_code == 204
    item = client.get("/api/ingest/calls/opp1:rec1", headers=H).json()
    assert item["status"] == "error" and "after 3 attempts" in item["error"]
    assert "overall_score" not in item


# ---- scoring -------------------------------------------------------------------------

def test_a_finished_review_carries_everything_and_the_audio_is_gone(client: TestClient, app: FastAPI,
                                                                    settings: Settings, hand_over) -> None:
    hand_over("opp1:rec1", agent_name="Josh", meta='{"opportunity_id": "abc"}', is_test="true")
    _finish(app, settings, "tqc-opp1-rec1")
    assert not list(settings.uploads_dir.glob("tqc-opp1-rec1.*"))      # only the masked text is kept
    item = client.get("/api/ingest/calls/opp1:rec1", headers=H).json()
    assert item["status"] == "done" and item["error"] is None
    assert (item["external_id"], item["is_test"], item["meta"], item["agent"]) == ("opp1:rec1", True, {"opportunity_id": "abc"}, "Josh")
    assert item["overall_score"] == _get(app, "tqc-opp1-rec1").overall_score
    assert item["agent_name_heard"] == "Josh"
    assert {f["kind"] for f in item["findings"]} == {"flag", "coach"}
    assert item["work_on_next"] == {"intro": "i", "items": ["x"]}
    assert item["transcript"]["segments"][0]["text"] == "Hello, this is Josh."
    assert item["speaker_map"]["SPEAKER_0"] == "Agent"
    assert item["engine"]["scorer_version"] == "2.abc12345" and item["engine"]["scorer_model"] == "claude-test"
    assert item["engine"]["transcriber"] == "whisper_local/whisper-large-v3-turbo"
    card = item["scorecard"]
    assert card["version"] == app.state.scorecard.version
    assert {"id": "compliance", "label": app.state.scorecard.labels["compliance"], "weight": 15} in card["categories"]
    assert item["finished_at"].endswith("Z")


def test_a_hand_upload_keeps_its_audio_after_scoring(signed_in: TestClient, app: FastAPI, settings: Settings, make_wav) -> None:
    with make_wav("mine.wav").open("rb") as f:
        call_id = signed_in.post("/api/calls", files={"files": ("mine.wav", f, "audio/wav")}).json()[0]["id"]
    _finish(app, settings, call_id)
    assert _get(app, call_id).status == CallStatus.done
    assert list(settings.uploads_dir.glob(f"{call_id}.*"))


def test_scoring_is_tried_again_then_succeeds(app: FastAPI, settings: Settings, hand_over) -> None:
    hand_over("opp1:rec1")
    scorer = _finish(app, settings, "tqc-opp1-rec1", Scorer([{"parse_error": True, "reason": "invalid JSON"}, v2_score()]))
    call = _get(app, "tqc-opp1-rec1")
    assert (scorer.calls, call.status, call.error, call.score_attempts) == (2, CallStatus.done, None, 1)


@pytest.mark.parametrize("bad", [{"parse_error": True, "reason": "invalid JSON"}, RuntimeError("API is down")])
def test_scoring_gives_up_after_a_few_tries(client: TestClient, app: FastAPI, settings: Settings, hand_over, bad: Any) -> None:
    hand_over("opp1:rec1")
    scorer = _finish(app, settings, "tqc-opp1-rec1", Scorer([bad]))
    call = _get(app, "tqc-opp1-rec1")
    assert scorer.calls == MAX_SCORE_ATTEMPTS
    assert call.status == CallStatus.error and call.score_attempts == MAX_SCORE_ATTEMPTS
    assert client.get("/api/ingest/calls/opp1:rec1", headers=H).json()["status"] == "error"


def test_a_hand_upload_still_fails_on_the_first_bad_score(signed_in: TestClient, app: FastAPI, settings: Settings, make_wav) -> None:
    with make_wav("mine.wav").open("rb") as f:
        call_id = signed_in.post("/api/calls", files={"files": ("mine.wav", f, "audio/wav")}).json()[0]["id"]
    scorer = _finish(app, settings, call_id, Scorer([{"parse_error": True, "reason": "invalid JSON"}]))
    assert scorer.calls == 1 and _get(app, call_id).status == CallStatus.error


# ---- what changed since --------------------------------------------------------------

def _stamp(app: FastAPI, call_id: str, when) -> None:
    from sqlalchemy import update
    with Session(app.state.engine) as s:     # a bulk update: the stamp is set by hand, not by the listener
        s.exec(update(Call).where(Call.id == call_id).values(finished_at=when))
        s.commit()


def test_changes_lists_finished_calls_oldest_first_and_pages_by_cursor(client: TestClient, app: FastAPI,
                                                                       settings: Settings, hand_over) -> None:
    base = utcnow() - timedelta(hours=1)
    for i in range(5):
        hand_over(f"opp:{i}")
        _finish(app, settings, f"tqc-opp-{i}")
        _stamp(app, f"tqc-opp-{i}", base + timedelta(minutes=i))
    hand_over("opp:waiting")                                  # not finished: never listed

    first = client.get("/api/ingest/changes", params={"limit": 2}, headers=H).json()
    assert [i["external_id"] for i in first["items"]] == ["opp:0", "opp:1"] and first["more"] is True
    second = client.get("/api/ingest/changes", params={"limit": 2, "since": first["cursor"]}, headers=H).json()
    assert [i["external_id"] for i in second["items"]] == ["opp:2", "opp:3"]
    third = client.get("/api/ingest/changes", params={"limit": 2, "since": second["cursor"]}, headers=H).json()
    assert [i["external_id"] for i in third["items"]] == ["opp:4"] and third["more"] is False
    done = client.get("/api/ingest/changes", params={"since": third["cursor"]}, headers=H).json()
    assert done == {"items": [], "cursor": third["cursor"], "more": False}


def test_a_page_never_splits_calls_that_finished_at_the_same_instant(client: TestClient, app: FastAPI,
                                                                    settings: Settings, hand_over) -> None:
    when = utcnow() - timedelta(minutes=5)
    for i in range(4):
        hand_over(f"same:{i}")
        _finish(app, settings, f"tqc-same-{i}")
        _stamp(app, f"tqc-same-{i}", when)
    page = client.get("/api/ingest/changes", params={"limit": 2}, headers=H).json()
    assert sorted(i["external_id"] for i in page["items"]) == ["same:0", "same:1", "same:2", "same:3"]
    assert client.get("/api/ingest/changes", params={"since": page["cursor"]}, headers=H).json()["items"] == []


def test_a_call_scored_again_shows_up_again(client: TestClient, app: FastAPI, settings: Settings, hand_over) -> None:
    hand_over("opp1:rec1")
    _finish(app, settings, "tqc-opp1-rec1")
    cursor = client.get("/api/ingest/changes", headers=H).json()["cursor"]
    assert client.get("/api/ingest/changes", params={"since": cursor}, headers=H).json()["items"] == []
    time.sleep(0.01)
    _finish(app, settings, "tqc-opp1-rec1", Scorer([v2_score(overall=80)]))
    again = client.get("/api/ingest/changes", params={"since": cursor}, headers=H).json()["items"]
    assert [i["external_id"] for i in again] == ["opp1:rec1"]


def test_hand_uploads_never_appear_in_changes(signed_in: TestClient, client: TestClient, app: FastAPI,
                                              settings: Settings, make_wav) -> None:
    with make_wav("mine.wav").open("rb") as f:
        call_id = signed_in.post("/api/calls", files={"files": ("mine.wav", f, "audio/wav")}).json()[0]["id"]
    _finish(app, settings, call_id)
    assert client.get("/api/ingest/changes", headers=H).json()["items"] == []
    assert client.get(f"/api/ingest/calls/{call_id}", headers=H).status_code == 404


def test_status_says_how_deep_the_queue_is_and_whether_the_mac_is_there(client: TestClient, app: FastAPI, hand_over) -> None:
    assert client.get("/api/ingest/status", headers=H).json() == {
        "waiting": 0, "waiting_backlog": 0, "oldest_waiting_since": None,
        "worker": {"online": False, "last_seen": None, "worker_id": None, "model": None}}
    hand_over("a:1")
    hand_over("a:2", backlog="true")
    client.post("/api/worker/claim", json={"worker_id": "mac-1"}, headers=MAC)
    body = client.get("/api/ingest/status", headers=H).json()
    assert (body["waiting"], body["waiting_backlog"], body["worker"]["online"]) == (2, 1, True)
    assert body["oldest_waiting_since"].endswith("Z")


# ---- an existing database ------------------------------------------------------------

def test_a_database_from_before_this_change_is_upgraded_in_place(tmp_path: Path) -> None:
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE calls (id VARCHAR PRIMARY KEY, filename VARCHAR NOT NULL, agent_id INTEGER, "
                "uploaded_at DATETIME, duration_s FLOAT, stereo BOOLEAN, status VARCHAR, progress FLOAT, error VARCHAR, "
                "provider VARCHAR, model VARCHAR, transcript_json VARCHAR, score_json VARCHAR, scored_at DATETIME)")
    con.execute("INSERT INTO calls (id, filename, status, progress) VALUES ('old-1', 'old-1.wav', 'done', 1.0)")
    con.commit()
    con.close()
    engine = make_engine(db)
    with Session(engine) as s:
        old = s.get(Call, "old-1")
        assert (old.source, old.external_id, old.is_test, old.backlog, old.score_attempts, old.finished_at) == (
            "upload", None, False, False, 0, None)
    make_engine(db)       # starting again changes nothing and does not fail


# ---- in progress ---------------------------------------------------------------------

def test_in_progress_shows_the_stage_the_start_time_and_the_place_in_line(client: TestClient, app: FastAPI,
                                                                          signed_in: TestClient, make_wav, hand_over) -> None:
    hand_over("b:1", backlog="true")
    with make_wav("mine.wav").open("rb") as f:            # a hand upload waits in the same line
        signed_in.post("/api/calls", files={"files": ("mine.wav", f, "audio/wav")})
    hand_over("n:1")
    hand_over("n:2")
    with Session(app.state.engine) as s:
        for i, row in enumerate(s.exec(select(Call).order_by(Call.id)).all()):
            row.uploaded_at = utcnow() - timedelta(minutes=30 - i)
            s.add(row)
        s.commit()
    assert client.get("/api/ingest/in-progress", headers={}).status_code == 401
    before = {i["external_id"]: i for i in client.get("/api/ingest/in-progress", headers=H).json()["items"]}
    assert all(i["status"] == "queued" and i["started_at"] is None for i in before.values())
    assert before["b:1"]["ahead"] == 3                     # every new call, the hand upload included
    assert sorted(i["ahead"] for k, i in before.items() if k != "b:1") != [None, None]

    claimed = client.post("/api/worker/claim", json={"worker_id": "mac-1"}, headers=MAC).json()["call_id"]
    after = client.get("/api/ingest/in-progress", headers=H).json()["items"]
    now_running = [i for i in after if i["status"] == "transcribing"]
    if claimed.startswith("tqc-"):
        assert len(now_running) == 1 and now_running[0]["started_at"].endswith("Z") and now_running[0]["ahead"] is None
    assert {i["external_id"]: i for i in after}["b:1"]["ahead"] == 2


def test_a_finished_call_leaves_in_progress(client: TestClient, app: FastAPI, settings: Settings, hand_over) -> None:
    hand_over("opp1:rec1")
    _finish(app, settings, "tqc-opp1-rec1")
    assert client.get("/api/ingest/in-progress", headers=H).json() == {"items": []}


# ---- coaching types ------------------------------------------------------------------

def test_coaching_carries_a_habit_from_the_scorecard_and_unknown_ones_become_other() -> None:
    from scoring.output import normalize_score
    from scoring.scorecard import load_card
    from api.config import ROOT
    card = load_card(ROOT / "scoring" / "scorecard.yaml")
    answer = v2_score()
    answer["scores"] = {key: {"score": 6, "evidence": "e"} for key in card["categories"]}
    answer["required_phrases"] = [{"phrase": r["phrase"], "present": True, "timestamp": "00:05"} for r in card["required_phrases"]]
    answer["findings"] = [
        {"kind": "coach", "coach_type": "quoted_before_budget", "category": "close", "timestamp": "09:30", "text": "t", "better": "b"},
        {"kind": "coach", "coach_type": "made_up_habit", "category": "discovery", "timestamp": "10:00", "text": "t", "better": "b"},
        {"kind": "coach", "category": "discovery", "timestamp": "11:00", "text": "t", "better": "b"},
    ]
    coach = [f for f in normalize_score(answer, card, speakers={"SPEAKER_0"})["findings"] if f["kind"] == "coach"]
    assert [(f["coach_type"], f["category"]) for f in coach] == [
        ("quoted_before_budget", "discovery"),       # the scorecard's category, not the model's
        ("other", "discovery"), ("other", "discovery")]
    assert coach[0]["label"] == "Ask about budget before you quote"


def test_the_scorer_is_told_the_habits_and_no_try_line_reaches_it() -> None:
    from scoring.rate_call import CallScorer
    import os
    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    system = CallScorer().system
    assert "quoted_before_budget (discovery): Ask about budget before you quote" in system
    assert "what would feel comfortable each month" not in system     # approved lines are shown, not generated


def test_a_review_carries_the_habit_list_with_its_approved_lines_and_the_weights(client: TestClient, app: FastAPI,
                                                                                 settings: Settings, hand_over) -> None:
    hand_over("opp1:rec1")
    _finish(app, settings, "tqc-opp1-rec1")
    card = client.get("/api/ingest/calls/opp1:rec1", headers=H).json()["scorecard"]
    assert card["version"] == app.state.scorecard.version
    assert {"id": "knows_carrier", "label": "The customer was told the carrier's name and acknowledged it"} in card["retention_signals"]
    habit = next(c for c in card["coach_types"] if c["id"] == "callback_without_time")
    assert habit["label"] == "Give every callback a day and a time" and habit["try"].startswith("Does Thursday")
    assert sum(c["weight"] for c in card["categories"]) == 100
    assert card["compliance_cap"] == {"if_any": "critical", "max_score": 3}
    assert {"id": "ignored_end_request", "label": "Continued after a request to end the call", "severity": "critical"} in card["flag_types"]


@pytest.mark.parametrize("broken, problem", [
    ([{"id": "a", "category": "close", "label": "A"}], "needs an 'other' type"),
    ([{"id": "other", "category": "general", "label": "O"}, {"id": "other", "category": "general", "label": "O"}], "must be unique"),
    ([{"id": "other", "category": "general", "label": "O"}, {"id": "a", "category": "nope", "label": "A"}], "unknown category"),
    ([{"id": "other", "category": "general", "label": ""}], "has no label"),
])
def test_a_bad_habit_list_stops_the_app_at_startup(tmp_path: Path, broken: list, problem: str) -> None:
    import yaml
    from scoring.scorecard import load_card
    from api.config import ROOT
    card = yaml.safe_load((ROOT / "scoring" / "scorecard.yaml").read_text())
    card["coach_types"] = broken
    path = tmp_path / "scorecard.yaml"
    path.write_text(yaml.safe_dump(card))
    with pytest.raises(ValueError, match=problem):
        load_card(path)


# ---- the sale check ------------------------------------------------------------------

def _answer(card: dict, **over: Any) -> dict:
    out = v2_score()
    out["scores"] = {key: {"score": 6, "evidence": "e"} for key in card["categories"]}
    out["required_phrases"] = [{"phrase": r["phrase"], "present": True, "timestamp": "00:05"} for r in card["required_phrases"]]
    out["findings"] = []
    return {**out, **over}


def test_a_sale_gets_one_answer_per_signal_and_anything_odd_is_unclear() -> None:
    from scoring.output import normalize_score
    from scoring.scorecard import load_card
    from api.config import ROOT
    card = load_card(ROOT / "scoring" / "scorecard.yaml")
    given = [{"signal": "knows_carrier", "answer": "yes", "timestamp": "12:36"},
             {"signal": "unsure_at_close", "answer": "maybe", "timestamp": "20:00"},
             {"signal": "confirmed_amount", "answer": "no", "timestamp": "not a time"},
             {"signal": "made_up", "answer": "yes", "timestamp": "01:00"}]
    out = normalize_score(_answer(card, retention=given), card, speakers={"SPEAKER_0"})["retention"]
    assert [r["signal"] for r in out] == [s["id"] for s in card["retention_signals"]]
    by = {r["signal"]: r for r in out}
    assert by["knows_carrier"] == {"signal": "knows_carrier", "answer": "yes", "timestamp": "12:36"}
    assert by["unsure_at_close"] == {"signal": "unsure_at_close", "answer": "unclear", "timestamp": None}
    assert by["confirmed_amount"] == {"signal": "confirmed_amount", "answer": "no", "timestamp": None}
    assert by["details_read_back"]["answer"] == "unclear" and "made_up" not in by


def test_a_call_that_was_not_a_sale_has_no_sale_check_whatever_the_model_says() -> None:
    from scoring.output import normalize_score
    from scoring.scorecard import load_card
    from api.config import ROOT
    card = load_card(ROOT / "scoring" / "scorecard.yaml")
    given = [{"signal": "knows_carrier", "answer": "yes", "timestamp": "12:36"}]
    out = normalize_score(_answer(card, call_outcome="callback", sale=None, retention=given), card, speakers={"SPEAKER_0"})
    assert out["retention"] is None


def test_the_sale_check_never_fails_a_review_when_it_is_missing_altogether() -> None:
    from scoring.output import normalize_score
    from scoring.scorecard import load_card
    from api.config import ROOT
    card = load_card(ROOT / "scoring" / "scorecard.yaml")
    out = normalize_score(_answer(card), card, speakers={"SPEAKER_0"})["retention"]
    assert {r["answer"] for r in out} == {"unclear"}


def test_only_the_three_serious_flags_can_cap_the_score() -> None:
    from scoring.scorecard import load_card
    from api.config import ROOT
    card = load_card(ROOT / "scoring" / "scorecard.yaml")
    assert sorted(f["id"] for f in card["flag_types"] if f["severity"] == "critical") == [
        "agent_handled_esign", "approval_before_uw", "ignored_end_request"]
