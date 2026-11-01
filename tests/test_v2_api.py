"""The v2 API: stages and time left, filters, details, cancel, assign. Local mode unless noted."""
from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from api import leases, repo
from api.app import create_app
from api.config import Settings
from api.db import make_engine
from api.models import Agent, Call, CallStatus, utcnow
from tests.conftest import make_score, make_transcript, sign_in

TOKEN = "test-worker-token"
WORKER = {"Authorization": f"Bearer {TOKEN}", "X-Worker-Id": "mac-1"}


def v2_score(overall: int = 36, agent_name: str | None = "Josh", first_name: str | None = "Gary", **kw: Any) -> dict[str, Any]:
    return {
        "agent_speaker": "SPEAKER_0", "agent_name": agent_name,
        "customer": {"first_name": first_name, "last_name": "Jones", "age": 75, "state": "Maryland"},
        "call_outcome": "sale",
        "sale": {"coverage_usd": 11000, "premium_monthly_usd": 113, "carrier": "Aflac", "plan_type": "level"},
        "summary": "A call.",
        "scores": {"opening": {"score": 6, "evidence": "e"}, "compliance": {"score": 1, "evidence": "e"}},
        "overall_score": overall,
        "phases": [{"phase": "opening", "start": "00:00"}],
        "findings": [
            {"kind": "flag", "flag_type": "no_recording_disclosure", "severity": "critical", "label": "L",
             "category": "compliance", "timestamp": "00:00", "text": "t"},
            {"kind": "flag", "flag_type": "pressure_tactics", "severity": "major", "label": "L",
             "category": "compliance", "timestamp": "00:10", "text": "t"},
            {"kind": "coach", "category": "discovery", "timestamp": "00:20", "text": "t", "better": "b"},
        ],
        "required_phrases": [], "work_on_next": {"intro": "i", "items": ["x"]},
        "_meta": {"scorer_version": "2.abc12345", "scorer_model": "claude-test"},
        **kw,
    }


def scored(app: FastAPI, call_id: str, score: dict[str, Any] | None = None, *, agent_id: int | None = None,
           uploaded_at=None) -> None:
    """A finished call saved the way the worker saves one."""
    with Session(app.state.engine) as s:
        call = Call(id=call_id, filename=f"{call_id}.mp3", status=CallStatus.done, duration_s=60.0, stereo=False,
                    agent_id=agent_id, uploaded_at=uploaded_at or utcnow(),
                    transcript_json=json.dumps(make_transcript([(0.0, "hi", "SPEAKER_0"), (2.0, "hello", "SPEAKER_1")])))
        repo.apply_score(s, call, score or v2_score())
        s.add(call)
        s.commit()


def agent(app: FastAPI, name: str = "Josh") -> int:
    with Session(app.state.engine) as s:
        a = repo.agent_for_name(s, name)
        s.commit()
        return a.id


def row(app: FastAPI, call_id: str) -> Call | None:
    with Session(app.state.engine) as s:
        return s.get(Call, call_id)


def audio(settings: Settings, call_id: str) -> Path:
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    path = settings.uploads_dir / f"{call_id}.mp3"
    path.write_bytes(b"audio")
    return path


# ---- list -------------------------------------------------------------------------------

def test_list_items_carry_counts_and_suggestion(client: TestClient, app: FastAPI) -> None:
    scored(app, "c1")
    item = client.get("/api/calls").json()["items"][0]
    assert (item["flag_count"], item["critical_count"]) == (2, 1)
    assert item["stage"] == "done" and item["overall_score"] == 36
    assert item["weakest_category"] == "compliance" and item["weakest_label"] == "Compliance"
    assert item["agent"] is None and item["suggested_agent"] == "Josh"


def test_a_named_agent_replaces_the_suggestion(client: TestClient, app: FastAPI) -> None:
    scored(app, "c1", agent_id=agent(app))
    item = client.get("/api/calls").json()["items"][0]
    assert item["agent"]["name"] == "Josh" and item["suggested_agent"] is None


def test_filters(client: TestClient, app: FastAPI, add_call) -> None:
    josh = agent(app)
    scored(app, "flagged-low", agent_id=josh)
    scored(app, "clean-high", {**v2_score(overall=80), "findings": [], "call_outcome": "no_sale"}, agent_id=josh)
    scored(app, "unassigned", v2_score(agent_name="Someone Else"))
    add_call("old-flagged", score=make_score(50, red_flags=1), agent_id=josh)

    def ids(name: str) -> set[str]:
        return {c["id"] for c in client.get("/api/calls", params={"filter": name}).json()["items"]}

    assert ids("all") == {"flagged-low", "clean-high", "unassigned", "old-flagged"}
    assert ids("sales") == {"flagged-low", "unassigned", "old-flagged"}
    assert ids("flagged") == {"flagged-low", "unassigned", "old-flagged"}
    assert ids("below_target") == {"flagged-low", "unassigned", "old-flagged"}
    assert ids("below60") == ids("below_target")
    assert ids("unassigned") == {"unassigned"}
    assert client.get("/api/calls", params={"filter": "nope"}).status_code == 422


def test_below_target_follows_the_scorecard(settings: Settings, tmp_path: Path) -> None:
    import yaml
    card = yaml.safe_load(settings.scorecard_path.read_text())
    card["target_score"] = 30
    path = tmp_path / "card.yaml"
    path.write_text(yaml.safe_dump(card))
    app = create_app(dataclasses.replace(settings, scorecard_path=path), start_worker=False)
    scored(app, "c1")
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/api/calls", params={"filter": "below_target"}).json()["total"] == 0
        assert client.get("/api/stats").json()["target_score"] == 30


def test_stats_count_in_progress_but_not_cancelled(client: TestClient, app: FastAPI, add_call) -> None:
    add_call("running", status=CallStatus.transcribing)
    add_call("going", status=CallStatus.transcribing)
    scored(app, "c1")
    with Session(app.state.engine) as s:
        call = s.get(Call, "going")
        call.cancelled_at = utcnow()
        s.add(call)
        s.commit()
    stats = client.get("/api/stats").json()
    assert stats["in_progress"] == 1 and stats["flagged_calls"] == 1 and stats["compliance_flags"] == 2
    assert stats["weakest_label"] == "Compliance" and stats["weakest_avg"] == 1.0


def test_time_left_counts_transcribing_and_separating(client: TestClient, app: FastAPI, add_call) -> None:
    add_call("c1", status=CallStatus.transcribing, duration_s=100.0)
    with Session(app.state.engine) as s:
        call = s.get(Call, "c1")
        call.stereo, call.progress = False, 0.5
        s.add(call)
        s.commit()
    item = client.get("/api/calls").json()["items"][0]
    expected = 100 * (repo.DEFAULT_RTF + repo.DEFAULT_SEPARATION_RTF) * 0.5
    assert item["stage"] == "transcribing" and item["eta_s"] == round(expected)


def test_stereo_calls_have_no_separation_in_their_estimate(add_call, app: FastAPI) -> None:
    t = repo.Timing(rtf_mono=0.1, rtf_stereo=0.2, separation_rtf=0.5)
    mono = Call(id="m", filename="m", status=CallStatus.queued, duration_s=100.0, stereo=False)
    stereo = Call(id="s", filename="s", status=CallStatus.queued, duration_s=100.0, stereo=True)
    assert repo.eta_s(mono, t) == 60 and repo.eta_s(stereo, t) == 20


def test_no_estimate_once_progress_hits_the_cap_or_while_scoring() -> None:
    t = repo.Timing(0.1, 0.2, 0.5)
    assert repo.eta_s(Call(id="a", filename="a", status=CallStatus.transcribing, duration_s=100.0, progress=0.9), t) is None
    assert repo.eta_s(Call(id="b", filename="b", status=CallStatus.scoring, duration_s=100.0), t) is None


def test_separation_speed_is_measured_from_diarized_calls(app: FastAPI) -> None:
    with Session(app.state.engine) as s:
        assert repo.recent_separation_rtf(s) == repo.DEFAULT_SEPARATION_RTF
        s.add(Call(id="c1", filename="c1", status=CallStatus.done, speaker_source="diarized", scored_at=utcnow(),
                   transcript_json=json.dumps({"separation_s": 30.0, "audio_duration_s": 300.0})))
        s.commit()
        assert repo.recent_separation_rtf(s) == pytest.approx(0.1)


@pytest.fixture
def remote_app(settings: Settings) -> FastAPI:
    return create_app(dataclasses.replace(settings, worker_mode="remote", worker_token=TOKEN), start_worker=False)


@pytest.fixture
def remote(remote_app: FastAPI) -> TestClient:
    with TestClient(remote_app, base_url="https://testserver") as c:
        sign_in(c, remote_app)
        yield c


def test_queued_is_waiting_only_while_the_remote_worker_is_offline(remote: TestClient, remote_app: FastAPI) -> None:
    with Session(remote_app.state.engine) as s:
        s.add(Call(id="c1", filename="c1", status=CallStatus.queued))
        s.commit()
    assert remote.get("/api/calls").json()["items"][0]["stage"] == "waiting"
    leases.record_seen(remote_app.state.engine, "mac-1")
    assert remote.get("/api/calls").json()["items"][0]["stage"] == "queued"


# ---- one call ---------------------------------------------------------------------------

def test_detail_has_everything_the_page_needs(client: TestClient, app: FastAPI) -> None:
    scored(app, "c1", agent_id=agent(app))
    with Session(app.state.engine) as s:
        call = s.get(Call, "c1")
        call.waveform, call.speaker_source = "[0.0, 1.0]", "diarized"
        s.add(call)
        s.commit()
    d = client.get("/api/calls/c1").json()
    assert d["waveform"] == [0.0, 1.0] and d["speaker_source"] == "diarized" and d["scored_v2"] is True
    assert d["findings"][0]["flag_type"] == "no_recording_disclosure"
    assert d["phases"] == [{"phase": "opening", "start": "00:00"}]
    assert d["customer"]["first_name"] == "Gary" and d["sale"]["coverage_usd"] == 11000
    assert d["scorer_version"] == "2.abc12345" and d["score_history"] == []
    assert d["scorecard"]["target_score"] == 60 and d["scorecard"]["labels"]["value_presentation"] == "Value"
    assert d["speakers"] == {"SPEAKER_0": "Josh", "SPEAKER_1": "Gary"}


@pytest.mark.parametrize("score, agent_named, expected", [
    (v2_score(first_name=None), True, {"SPEAKER_0": "Josh", "SPEAKER_1": "Customer"}),
    (v2_score(agent_name="Heard Name"), False, {"SPEAKER_0": "Heard Name", "SPEAKER_1": "Gary"}),
    (v2_score(agent_name=None, first_name=None), False, {"SPEAKER_0": "Agent", "SPEAKER_1": "Customer"}),
    ({**v2_score(), "agent_speaker": None}, False, {"SPEAKER_0": "Speaker 1", "SPEAKER_1": "Speaker 2"}),
])
def test_speaker_names_fall_back_in_order(client: TestClient, app: FastAPI, score, agent_named, expected) -> None:
    scored(app, "c1", score, agent_id=agent(app) if agent_named else None)
    assert client.get("/api/calls/c1").json()["speakers"] == expected


def test_download_uses_display_names(client: TestClient, app: FastAPI) -> None:
    scored(app, "c1", agent_id=agent(app))
    body = client.get("/api/calls/c1/transcript.txt").text
    assert "[00:00] Josh: hi" in body and "[00:02] Gary: hello" in body


def test_prev_next_follow_the_list_order(client: TestClient, app: FastAPI) -> None:
    now = utcnow()
    for i, name in enumerate(("oldest", "middle", "newest")):
        scored(app, name, uploaded_at=now + timedelta(minutes=i))
    get = lambda c: client.get(f"/api/calls/{c}").json()
    assert (get("newest")["prev_id"], get("newest")["next_id"]) == (None, "middle")
    assert (get("middle")["prev_id"], get("middle")["next_id"]) == ("newest", "oldest")
    assert (get("oldest")["prev_id"], get("oldest")["next_id"]) == ("middle", None)


def test_agent_average_covers_their_last_30_days(client: TestClient, app: FastAPI) -> None:
    josh, sean = agent(app, "Josh"), agent(app, "Sean")
    scored(app, "a", v2_score(overall=30), agent_id=josh)
    scored(app, "b", v2_score(overall=50), agent_id=josh)
    scored(app, "other", v2_score(overall=99), agent_id=sean)
    scored(app, "stale", v2_score(overall=99), agent_id=josh)
    with Session(app.state.engine) as s:
        call = s.get(Call, "stale")
        call.scored_at = utcnow() - timedelta(days=31)
        s.add(call)
        s.commit()
    assert client.get("/api/calls/a").json()["agent_average"] == 40
    scored(app, "nobody", v2_score(agent_name=None))
    assert client.get("/api/calls/nobody").json()["agent_average"] is None


def test_a_rescore_keeps_the_old_score(app: FastAPI) -> None:
    scored(app, "c1", v2_score(overall=36))
    with Session(app.state.engine) as s:
        call = s.get(Call, "c1")
        repo.apply_score(s, call, {**v2_score(overall=50), "_meta": {"scorer_version": "2.next"}})
        s.add(call)
        s.commit()
    call = row(app, "c1")
    history = json.loads(call.score_history)
    assert call.overall_score == 50 and call.scorer_version == "2.next"
    assert [(h["overall_score"], h["scorer_version"]) for h in history] == [(36, "2.abc12345")]
    assert history[0]["score"]["overall_score"] == 36


def test_an_old_format_score_still_reads(client: TestClient, app: FastAPI, add_call) -> None:
    add_call("old", score=make_score(41, missing=["licensed"], red_flags=1),
             transcript=make_transcript([(0.0, "hi", None)]))
    d = client.get("/api/calls/old").json()
    assert d["scored_v2"] is False and d["overall_score"] == 41 and d["flag_count"] == 2 and d["critical_count"] == 0
    assert d["findings"] is None and d["speakers"] == {"SPEAKER_0": "Agent"}


def test_an_older_database_gets_the_columns_and_its_scores_copied_across(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE agents (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, slug VARCHAR NOT NULL)")
    db.execute("CREATE TABLE calls (id VARCHAR PRIMARY KEY, filename VARCHAR NOT NULL, agent_id INTEGER, uploaded_at DATETIME NOT NULL,"
               " status VARCHAR NOT NULL, progress FLOAT NOT NULL, score_json VARCHAR)")
    db.execute("INSERT INTO calls VALUES ('c1', 'f', NULL, '2026-10-01 00:00:00', 'done', 1.0, ?)", (json.dumps(make_score(39)),))
    db.commit()
    db.close()
    make_engine(path)
    db = sqlite3.connect(path)
    assert db.execute("SELECT overall_score, findings, cancelled_at FROM calls").fetchone() == (39, None, None)
    assert db.execute("SELECT count(*) FROM pragma_table_info('agents') WHERE name = 'aliases'").fetchone() == (1,)


# ---- cancel -----------------------------------------------------------------------------

def test_cancel_queued_removes_it_and_its_audio(client: TestClient, app: FastAPI, settings: Settings, add_call) -> None:
    add_call("c1", status=CallStatus.queued)
    path = audio(settings, "c1")
    r = client.post("/api/calls/c1/cancel")
    assert r.status_code == 200 and r.json() == {"status": "deleted"}
    assert row(app, "c1") is None and not path.exists()


def test_cancel_a_failed_call_removes_it(client: TestClient, app: FastAPI, add_call) -> None:
    add_call("c1", status=CallStatus.error)
    assert client.post("/api/calls/c1/cancel").json() == {"status": "deleted"}
    assert row(app, "c1") is None


def test_cancel_done_is_refused(client: TestClient, app: FastAPI) -> None:
    scored(app, "c1")
    assert client.post("/api/calls/c1/cancel").status_code == 409
    assert row(app, "c1") is not None


@pytest.mark.parametrize("status", [CallStatus.scoring, CallStatus.error, CallStatus.queued])
def test_a_call_with_a_score_is_never_removed_by_cancel(client: TestClient, app: FastAPI, status: CallStatus) -> None:
    # a re-score in progress, a re-score that failed, or one queued again by the upgrade script
    scored(app, "c1")
    with Session(app.state.engine) as s:
        call = s.get(Call, "c1")
        call.status = status
        s.add(call)
        s.commit()
    assert client.post("/api/calls/c1/cancel").status_code == 409
    assert row(app, "c1").score_json


def test_cancel_unknown_is_404(client: TestClient) -> None:
    assert client.post("/api/calls/nope/cancel").status_code == 404


def test_cancel_running_hides_it_and_the_heartbeat_says_stop(remote: TestClient, remote_app: FastAPI, settings: Settings) -> None:
    with Session(remote_app.state.engine) as s:
        s.add(Call(id="c1", filename="c1.mp3", status=CallStatus.queued))
        s.commit()
    path = audio(settings, "c1")
    assert remote.post("/api/worker/claim", json={"worker_id": "mac-1"}, headers=WORKER).status_code == 200
    r = remote.post("/api/worker/jobs/c1/heartbeat", json={"progress": 0.1}, headers=WORKER)
    assert r.json()["cancel"] is False

    assert remote.post("/api/calls/c1/cancel").json() == {"status": "cancelling"}
    assert remote.get("/api/calls").json()["total"] == 0                   # gone from the page at once
    assert remote.get("/api/calls/c1").status_code == 404
    assert remote.get("/api/stats").json()["in_progress"] == 0
    assert row(remote_app, "c1") is not None and path.exists()             # worker still holds it

    assert remote.post("/api/worker/jobs/c1/heartbeat", json={"progress": 0.2}, headers=WORKER).json() == {"cancel": True}
    assert row(remote_app, "c1") is None and not path.exists()
    assert remote.post("/api/worker/jobs/c1/heartbeat", json={}, headers=WORKER).status_code == 404


def test_cancelled_call_is_removed_if_its_worker_never_returns(remote: TestClient, remote_app: FastAPI, settings: Settings) -> None:
    with Session(remote_app.state.engine) as s:
        s.add(Call(id="c1", filename="c1.mp3", status=CallStatus.queued))
        s.commit()
    path = audio(settings, "c1")
    remote.post("/api/worker/claim", json={"worker_id": "mac-1"}, headers=WORKER)
    remote.post("/api/calls/c1/cancel")
    later = utcnow() + leases.LEASE + timedelta(minutes=1)
    assert leases.requeue_expired(remote_app.state.engine, now=later, uploads_dir=settings.uploads_dir) == 1
    assert row(remote_app, "c1") is None and not path.exists()


# ---- assign / agents --------------------------------------------------------------------

def test_assign(client: TestClient, app: FastAPI) -> None:
    scored(app, "c1")
    josh = agent(app)
    assert client.get("/api/calls", params={"filter": "unassigned"}).json()["total"] == 1
    r = client.patch("/api/calls/c1", json={"agent_id": josh})
    assert r.status_code == 200 and r.json()["agent"] == {"id": josh, "name": "Josh"}
    assert client.get("/api/calls", params={"filter": "unassigned"}).json()["total"] == 0


def test_unassign(client: TestClient, app: FastAPI) -> None:
    scored(app, "c1", agent_id=agent(app))
    assert client.patch("/api/calls/c1", json={"agent_id": None}).json()["agent"] is None


def test_assign_unknown_agent_is_404(client: TestClient, app: FastAPI) -> None:
    scored(app, "c1")
    r = client.patch("/api/calls/c1", json={"agent_id": 999})
    assert r.status_code == 404 and row(app, "c1").agent_id is None


def test_assign_unknown_call_is_404(client: TestClient, app: FastAPI) -> None:
    assert client.patch("/api/calls/nope", json={"agent_id": agent(app)}).status_code == 404


def test_create_agent_finds_rather_than_duplicates(client: TestClient, app: FastAPI) -> None:
    first = client.post("/api/agents", json={"name": "Josh"})
    assert first.status_code == 201 and first.json()["name"] == "Josh"
    assert client.post("/api/agents", json={"name": " josh "}).json()["id"] == first.json()["id"]
    with Session(app.state.engine) as s:
        a = s.get(Agent, first.json()["id"])
        a.aliases = '["Joshua"]'
        s.add(a)
        s.commit()
    assert client.post("/api/agents", json={"name": "JOSHUA"}).json()["id"] == first.json()["id"]
    assert [a["aliases"] for a in client.get("/api/agents").json()] == [["Joshua"]]
    assert client.post("/api/agents", json={"name": "!!!"}).status_code == 422
    assert client.post("/api/agents", json={"name": ""}).status_code == 422
