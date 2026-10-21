from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlmodel import Session, select

from api.models import Agent, Call
from api.repo import find_or_create_agent
from scripts.import_processed import import_file
from tests.conftest import make_score, make_transcript

STEM = "20261017-014852_2025550147-all"   # Vici: <date>-<time>_<customer phone>-all


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    calls, uploads = tmp_path / "calls", tmp_path / "uploads"
    calls.mkdir()
    uploads.mkdir()
    (calls / f"{STEM}.mp3").write_bytes(b"ID3")
    record = tmp_path / f"{STEM}.json"
    record.write_text(json.dumps({
        "call_id": STEM, "stereo": False,
        "transcript": make_transcript([(0.0, "call me on 202-555-0147", None)]),
        "score": {**make_score(39), "agent_name": "Dana"},
    }))
    return record, calls, uploads


def test_import_redacts_links_audio_and_uses_scorer_agent(app: FastAPI, dirs) -> None:
    record, calls, uploads = dirs
    with Session(app.state.engine) as s:
        call_id = import_file(s, record, calls, uploads)
        s.commit()
        call = s.get(Call, call_id)
        assert s.get(Agent, call.agent_id).name == "Dana"
        assert json.loads(call.transcript_json)["segments"][0]["text"] == "call me on [PHONE]"
    assert (uploads / f"{call_id}.mp3").exists()
    assert not list(calls.glob(f"{call_id}.*"))    # reference calls dir untouched


def test_reimport_fixes_a_phone_number_agent(app: FastAPI, dirs) -> None:
    record, calls, uploads = dirs
    with Session(app.state.engine) as s:
        call_id = import_file(s, record, calls, uploads)
        # what the old rule produced: the customer's phone number as the agent
        phone = find_or_create_agent(s, "2025550147-all")
        s.get(Call, call_id).agent_id = phone.id
        s.commit()

        assert import_file(s, record, calls, uploads) is None
        s.commit()
        assert s.exec(select(Call)).all() == [s.get(Call, call_id)]          # not imported twice
        assert s.get(Agent, s.get(Call, call_id).agent_id).name == "Dana"
        assert [a.slug for a in s.exec(select(Agent)).all()] == ["dana"]     # phone agent gone
