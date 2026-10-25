from __future__ import annotations

import json
import wave
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from api.app import create_app
from api.config import ROOT, Settings
from api.models import Call, CallStatus, utcnow


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # local dev defaults: AUTH_DISABLED=1 (ignored by remote-mode apps, which then need the secret)
    return Settings(
        db_path=tmp_path / "test.db",
        uploads_dir=tmp_path / "uploads",
        provider="whisper_local",
        scorecard_path=ROOT / "scoring" / "scorecard.yaml",
        session_secret="test-session-secret",
        auth_disabled=True,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings, start_worker=False)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # https: the session cookie is Secure, so it's only sent back over https
    with TestClient(app, base_url="https://testserver") as c:
        yield c


USER_EMAIL, USER_PASSWORD = "lead@example.com", "correct horse battery"


def sign_in(client: TestClient, app: FastAPI) -> TestClient:
    """Create the test user and sign `client` in (its base_url must be https)."""
    from api.users import add_user
    with Session(app.state.engine) as s:
        add_user(s, USER_EMAIL, USER_PASSWORD)
    r = client.post("/api/auth/login", json={"email": USER_EMAIL, "password": USER_PASSWORD})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
def signed_in(client: TestClient, app: FastAPI) -> TestClient:
    return sign_in(client, app)


@pytest.fixture
def make_wav(tmp_path: Path) -> Callable[..., Path]:
    def _make(name: str = "call.wav", seconds: float = 1.0, channels: int = 1) -> Path:
        path = tmp_path / "src" / name
        path.parent.mkdir(exist_ok=True)
        rate = 8000
        with wave.open(str(path), "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"\x00\x00" * channels * int(rate * seconds))
        return path
    return _make


def make_transcript(segments: list[tuple[float, str, str | None]], duration: float = 120.0) -> dict[str, Any]:
    return {
        "provider": "whisper_local", "model": "whisper-large-v3-turbo", "call_id": "x",
        "segments": [{"start": s, "end": s + 2, "text": t, "speaker": spk, "confidence": None}
                     for s, t, spk in segments],
        "latency_s": 6.0, "audio_duration_s": duration, "cost_usd": 0.0,
    }


def make_score(
    overall: int, outcome: str = "sale", cats: dict[str, int] | None = None,
    missing: list[str] | None = None, red_flags: int = 0, agent_speaker: str | None = "SPEAKER_0",
) -> dict[str, Any]:
    cats = cats or {"opening": 8, "discovery": 7, "value_presentation": 7,
                    "objection_handling": 6, "compliance": 9, "close": 6}
    return {
        "agent_speaker": agent_speaker, "agent_name": None, "call_outcome": outcome,
        "scores": {c: {"score": v, "evidence": f"{c} evidence"} for c, v in cats.items()},
        "overall_score": overall, "strengths": ["s1"], "improvements": ["i1"],
        "coachable_moments": [{"timestamp": "00:04", "what_happened": "Skipped the close", "better_approach": "Ask"}],
        "compliance": {"required_phrases_present": not missing, "missing": missing or [],
                       "red_flags": [{"timestamp": "00:10", "flag": f"flag {i}"} for i in range(red_flags)]},
        "summary": "A call.",
        "_meta": {"scorer_model": "claude-test", "transcriber": "whisper_local/whisper-large-v3-turbo"},
    }


@pytest.fixture
def add_call(app: FastAPI) -> Callable[..., Call]:
    def _add(
        call_id: str, *, score: dict | None = None, transcript: dict | None = None,
        status: CallStatus = CallStatus.done, agent_id: int | None = None,
        uploaded_at: datetime | None = None, scored_at: datetime | None = None, duration_s: float = 120.0,
    ) -> Call:
        call = Call(
            id=call_id, filename=f"{call_id}.wav", agent_id=agent_id, status=status,
            uploaded_at=uploaded_at or utcnow(), duration_s=duration_s, stereo=True,
            progress=1.0 if status == CallStatus.done else 0.0,
            transcript_json=json.dumps(transcript) if transcript else None,
            score_json=json.dumps(score) if score else None,
            scored_at=scored_at or (utcnow() if score else None),
        )
        with Session(app.state.engine) as s:
            s.add(call)
            s.commit()
            s.refresh(call)
        return call
    return _add
