from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from api.models import Agent, CallStatus, utcnow
from api.repo import find_or_create_agent
from tests.conftest import make_score, make_transcript


def _drain(app: FastAPI) -> list[tuple[str, str]]:
    jobs = []
    while not app.state.jobs.empty():
        jobs.append(app.state.jobs.get_nowait())
    return jobs


def _upload(client: TestClient, *paths: Path, agent: str = "", names: list[str] | None = None):
    files = [("files", ((names[i] if names else p.name), p.read_bytes(), "audio/wav")) for i, p in enumerate(paths)]
    return client.post("/api/calls", files=files, data={"agent": agent})


# ---- upload ------------------------------------------------------------------------

def test_upload_probes_saves_and_enqueues(client: TestClient, app: FastAPI, make_wav, settings) -> None:
    mono = make_wav("20261021-141502_jblake.wav", seconds=2, channels=1)
    stereo = make_wav("20261021-141840_kmonroe.wav", seconds=1, channels=2)
    r = _upload(client, mono, stereo)
    assert r.status_code == 201, r.text
    a, b = r.json()
    assert a["status"] == "queued" and a["stereo"] is False and round(a["duration_s"]) == 2
    assert b["stereo"] is True
    assert a["agent"] == "Jblake" and b["agent"] == "Kmonroe"
    assert a["id"].startswith("20261021-141502_jblake-")
    assert (settings.calls_dir / f"{a['id']}.wav").exists()
    assert _drain(app) == [(a["id"], "full"), (b["id"], "full")]


def test_upload_assigns_chosen_agent(client: TestClient, app: FastAPI, make_wav) -> None:
    with Session(app.state.engine) as s:
        agent = find_or_create_agent(s, "luis-ortega", "Luis Ortega")
        s.commit()
        agent_id = agent.id
    r = _upload(client, make_wav("x_jblake.wav"), agent=str(agent_id))
    assert r.json()[0]["agent"] == "Luis Ortega"


def test_upload_rejects_non_audio_and_saves_nothing(client: TestClient, make_wav, settings) -> None:
    r = client.post("/api/calls", files=[
        ("files", ("ok_jblake.wav", make_wav().read_bytes(), "audio/wav")),
        ("files", ("notes.txt", b"hello", "text/plain")),
    ])
    assert r.status_code == 400
    assert "not an audio file" in r.json()["detail"]
    assert not settings.calls_dir.exists() or not any(settings.calls_dir.iterdir())
    assert client.get("/api/calls").json()["total"] == 0


def test_upload_judges_by_extension_not_content_type(client: TestClient, settings) -> None:
    r = client.post("/api/calls", files=[("files", ("x.html", b"<script>alert(1)</script>", "audio/wav"))])
    assert r.status_code == 400
    assert not settings.calls_dir.exists() or not any(settings.calls_dir.iterdir())


def test_upload_unreadable_audio_becomes_error_row(client: TestClient, app: FastAPI, make_wav, settings) -> None:
    r = client.post("/api/calls", files=[
        ("files", ("broken.wav", b"this is a text file", "audio/wav")),
        ("files", ("good_jblake.wav", make_wav().read_bytes(), "audio/wav")),
    ])
    assert r.status_code == 201
    broken, good = r.json()
    assert broken["status"] == "error" and broken["error"]
    assert not list(settings.calls_dir.glob(f"{broken['id']}.*"))   # error row kept, file not
    assert good["status"] == "queued"
    assert _drain(app) == [(good["id"], "full")]
    strip = client.get("/api/calls/in-progress").json()
    assert [c["id"] for c in strip] == [broken["id"], good["id"]]


def test_upload_limits(client: TestClient, make_wav) -> None:
    wav = make_wav()
    assert _upload(client, *([wav] * 21)).status_code == 400
    assert _upload(client, wav, agent="999").status_code == 400
    assert client.post("/api/calls", data={"agent": ""}).status_code == 422


# ---- list / filter / search / page -------------------------------------------------

def _seed(add_call, app: FastAPI) -> None:
    with Session(app.state.engine) as s:
        jb = find_or_create_agent(s, "jblake", "Jerome Blake").id
        km = find_or_create_agent(s, "kmonroe", "Keisha Monroe").id
        s.commit()
    now = utcnow()
    add_call("c-sale", agent_id=jb, uploaded_at=now - timedelta(minutes=3),
             score=make_score(84, "sale"), transcript=make_transcript([(0, "level benefit plan", "SPEAKER_0")]))
    add_call("c-nosale", agent_id=km, uploaded_at=now - timedelta(minutes=2),
             score=make_score(41, "no_sale", missing=["licensed"], red_flags=1),
             transcript=make_transcript([(0, "graded benefit", "SPEAKER_0")]))
    add_call("c-callback", agent_id=jb, uploaded_at=now - timedelta(minutes=1),
             score=make_score(67, "callback"), transcript=make_transcript([(0, "call me back", "SPEAKER_0")]))
    add_call("c-processing", status=CallStatus.transcribing, uploaded_at=now)


def _ids(client: TestClient, **params) -> list[str]:
    return [c["id"] for c in client.get("/api/calls", params=params).json()["items"]]


def test_list_sorted_newest_first_with_derived_fields(client: TestClient, app: FastAPI, add_call) -> None:
    _seed(add_call, app)
    body = client.get("/api/calls").json()
    assert body["total"] == 4
    assert [c["id"] for c in body["items"]] == ["c-processing", "c-callback", "c-nosale", "c-sale"]
    nosale = body["items"][2]
    assert (nosale["overall_score"], nosale["outcome"], nosale["flag_count"], nosale["agent"]) == (
        41, "no_sale", 2, "Keisha Monroe")
    assert nosale["weakest_category"] == "objection_handling"


def test_filters(client: TestClient, app: FastAPI, add_call) -> None:
    _seed(add_call, app)
    assert _ids(client, filter="sales") == ["c-sale"]
    assert _ids(client, filter="flagged") == ["c-nosale"]
    assert _ids(client, filter="below60") == ["c-nosale"]
    assert client.get("/api/calls", params={"filter": "bogus"}).status_code == 422


def test_search_agent_outcome_and_transcript(client: TestClient, app: FastAPI, add_call) -> None:
    _seed(add_call, app)
    assert _ids(client, q="keisha") == ["c-nosale"]
    assert _ids(client, q="No sale") == ["c-nosale"]
    assert _ids(client, q="GRADED") == ["c-nosale"]
    assert _ids(client, q="callback") == ["c-callback"]
    assert _ids(client, q="100%") == []
    assert _ids(client, q="flagged", filter="sales") == []


def test_paging(client: TestClient, app: FastAPI, add_call) -> None:
    _seed(add_call, app)
    r = client.get("/api/calls", params={"page": 2, "page_size": 3}).json()
    assert r["total"] == 4 and [c["id"] for c in r["items"]] == ["c-sale"]


def test_csv_export_uses_filters(client: TestClient, app: FastAPI, add_call) -> None:
    _seed(add_call, app)
    r = client.get("/api/calls.csv", params={"filter": "flagged"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0] == "Call,Uploaded,Agent,Outcome,Length,Score,Flags,Weakest,Status"
    assert len(lines) == 2 and lines[1].startswith("c-nosale,")
    assert ",Keisha Monroe,No sale,02:00,41,2,Objection handling,done" in lines[1]


def test_in_progress_and_stats(client: TestClient, app: FastAPI, add_call) -> None:
    _seed(add_call, app)
    add_call("c-old-error", status=CallStatus.error, uploaded_at=utcnow() - timedelta(hours=3))
    assert [c["id"] for c in client.get("/api/calls/in-progress").json()] == ["c-processing"]
    stats = client.get("/api/stats").json()
    assert stats == {"calls_scored": 3, "avg_score": 64, "compliance_flags": 2,
                     "weakest_category": "objection_handling"}


def test_agents_and_settings(client: TestClient, app: FastAPI, add_call) -> None:
    _seed(add_call, app)
    assert [a["name"] for a in client.get("/api/agents").json()] == ["Jerome Blake", "Keisha Monroe"]
    assert client.get("/api/settings").json() == {"provider": "whisper_local", "model": "whisper-large-v3-turbo"}


# ---- one call ----------------------------------------------------------------------

def test_detail(client: TestClient, add_call) -> None:
    tx = make_transcript([(2, "Hi, recorded line", "SPEAKER_0"), (5, "Hello", "SPEAKER_1")], duration=120)
    add_call("c1", score=make_score(70, agent_speaker="SPEAKER_0"), transcript=tx)
    d = client.get("/api/calls/c1").json()
    assert d["speaker_map"] == {"SPEAKER_0": "Agent", "SPEAKER_1": "Customer"}
    assert d["rtf"] == 0.05
    assert d["scorecard"]["required_phrases"] == ["recorded", "licensed"]
    assert d["scorecard"]["categories"][0] == "opening"
    assert d["transcript"]["segments"][0]["text"] == "Hi, recorded line"
    assert client.get("/api/calls/nope").status_code == 404


def test_audio_supports_range(client: TestClient, make_wav, settings, add_call) -> None:
    add_call("c1")
    settings.calls_dir.mkdir(parents=True, exist_ok=True)
    (settings.calls_dir / "c1.wav").write_bytes(make_wav(seconds=1).read_bytes())
    full = client.get("/api/calls/c1/audio")
    assert full.status_code == 200 and full.headers["content-type"] == "audio/wav"
    assert full.headers.get("accept-ranges") == "bytes"
    assert full.headers["x-content-type-options"] == "nosniff"
    part = client.get("/api/calls/c1/audio", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100


def test_transcript_txt(client: TestClient, add_call) -> None:
    tx = make_transcript([(2, "Hi", "SPEAKER_1"), (14, "Hello", "SPEAKER_0")])
    add_call("c1", score=make_score(70, agent_speaker="SPEAKER_1"), transcript=tx)
    r = client.get("/api/calls/c1/transcript.txt")
    assert r.text == "[00:02] Agent: Hi\n[00:14] Customer: Hello\n"
    assert r.headers["content-disposition"] == 'attachment; filename="c1.txt"'
    add_call("c2", status=CallStatus.queued)
    assert client.get("/api/calls/c2/transcript.txt").status_code == 409


def test_rescore(client: TestClient, app: FastAPI, add_call) -> None:
    add_call("no-tx", status=CallStatus.error)
    assert client.post("/api/calls/no-tx/rescore").status_code == 409
    add_call("c1", score=make_score(70), transcript=make_transcript([(0, "hi", "SPEAKER_0")]))
    r = client.post("/api/calls/c1/rescore")
    assert r.status_code == 200 and r.json()["status"] == "scoring"
    assert _drain(app) == [("c1", "rescore")]
    assert client.post("/api/calls/c1/rescore").status_code == 409   # already scoring


def test_delete_removes_row_and_audio(client: TestClient, settings, add_call) -> None:
    add_call("c1")
    settings.calls_dir.mkdir(parents=True, exist_ok=True)
    audio = settings.calls_dir / "c1.mp3"
    audio.write_bytes(b"x")
    assert client.delete("/api/calls/c1").status_code == 204
    assert not audio.exists()
    assert client.get("/api/calls/c1").status_code == 404
