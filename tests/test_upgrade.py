"""scripts/upgrade_calls_v2.py: the dry run changes nothing and counts right; the real run requeues,
scores, keeps old scores, and can be run twice."""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import Session

from api.config import ROOT, Settings
from api.db import make_engine
from api.models import Call, CallStatus
from tests.conftest import make_score, make_transcript
from transcribers.base import TranscriptResult

spec = importlib.util.spec_from_file_location("upgrade_calls_v2", ROOT / "scripts" / "upgrade_calls_v2.py")
up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(up)

MONO = make_transcript([(0.0, "hello", None)])
LABELLED = make_transcript([(0.0, "hello", "SPEAKER_0")])
V2 = {**make_score(50), "findings": [], "phases": [], "_meta": {"scorer_version": "2.new"}}


def row(**kw: Any) -> dict[str, Any]:
    return {"id": "c", "status": "done", "transcript_json": json.dumps(MONO), "score_json": None, **kw}


@pytest.mark.parametrize("fields, has_audio, expected", [
    ({}, True, up.REQUEUE),                                                       # mono, audio, never tried
    ({"speaker_source": "none"}, True, up.REQUEUE),
    ({"speaker_source": "none", "waveform": "[0.1]", "scorer_version": "2.x"}, True, up.CURRENT),   # v2 tried already
    ({"speaker_source": "none", "waveform": "[0.1]"}, True, up.RESCORE),
    ({}, False, up.RESCORE),                                                      # no audio: score what's there
    ({"transcript_json": json.dumps(LABELLED)}, True, up.RESCORE),                # has speakers
    ({"transcript_json": json.dumps(LABELLED), "scorer_version": "2.x"}, True, up.CURRENT),
    ({"transcript_json": None, "status": "error"}, True, up.REQUEUE),             # failed before transcribing
    ({"transcript_json": None, "status": "error"}, False, up.CANT),
    ({"status": "queued", "transcript_json": None}, True, up.IN_PROGRESS),
    ({"status": "scoring"}, True, up.IN_PROGRESS),
    ({"status": "separating"}, True, up.IN_PROGRESS),
    ({"cancelled_at": "2026-11-01"}, True, up.IN_PROGRESS),
    ({"status": "error"}, False, up.RESCORE),                                     # scoring failed: has a transcript
])
def test_classify(fields: dict[str, Any], has_audio: bool, expected: str) -> None:
    assert up.classify(row(**fields), has_audio) == expected


@pytest.fixture
def cfg(tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    (tmp_path / "uploads").mkdir(exist_ok=True)
    s = dataclasses.replace(settings, db_path=tmp_path / "calls.db", uploads_dir=tmp_path / "uploads")
    monkeypatch.setattr(up, "load_settings", lambda: s)
    return s


def seed(cfg: Settings, call_id: str, *, audio: bool = True, **kw: Any) -> None:
    if audio:
        (cfg.uploads_dir / f"{call_id}.mp3").write_bytes(b"audio")
    with Session(make_engine(cfg.db_path)) as s:
        s.add(Call(id=call_id, filename=f"{call_id}.mp3", status=kw.pop("status", CallStatus.done), duration_s=3600.0, **kw))
        s.commit()


def seed_all(cfg: Settings) -> None:
    score = json.dumps(make_score(39))
    seed(cfg, "mono", transcript_json=json.dumps(MONO), score_json=score)
    seed(cfg, "labelled", transcript_json=json.dumps(LABELLED), score_json=score)
    seed(cfg, "no-audio", audio=False, transcript_json=json.dumps(MONO), score_json=score)
    seed(cfg, "current", transcript_json=json.dumps(LABELLED), score_json=json.dumps(V2), scorer_version="2.old")
    seed(cfg, "running", status=CallStatus.transcribing)
    seed(cfg, "lost", audio=False, status=CallStatus.error)


class FakeScorer:
    def __init__(self, fail: bool = False) -> None:
        self.seen: list[str] = []
        self.fail = fail

    def score(self, res: TranscriptResult) -> dict:
        self.seen.append(res.call_id)
        return {"parse_error": True, "reason": "nope"} if self.fail else {**V2, "summary": "SSN 123-45-6789"}


def _hashes(cfg: Settings) -> dict[str, str]:
    # not -shm: that's SQLite's lock file, which any reader touches and which holds no data
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in cfg.db_path.parent.glob("calls*")
            if not p.name.endswith("-shm")}


def test_dry_run_counts_and_changes_nothing(cfg: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    seed_all(cfg)
    before = _hashes(cfg)
    scorer = FakeScorer()
    assert up.main(["--dry-run"], make_scorer=lambda: scorer) == 0
    out = capsys.readouterr().out
    assert "Calls in the database:                   6" in out
    assert "Re-transcribe (no speakers), then score: 1" in out
    assert "Score from the stored transcript:        2" in out
    assert "Already on scorecard v2, left alone:     1" in out
    assert "In progress, left alone (v2 on finish):  1" in out
    assert "No transcript or audio, can't upgrade:   1" in out
    assert "Estimated Claude calls:                  3" in out
    assert "Audio those calls cover:                 3.0 h" in out
    assert "mono" not in out and "labelled" not in out      # no ids: they hold phone numbers
    assert _hashes(cfg) == before and not scorer.seen


def test_dry_run_reads_a_database_the_v2_app_has_never_opened(cfg: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    db = sqlite3.connect(cfg.db_path)
    db.execute("CREATE TABLE calls (id VARCHAR PRIMARY KEY, status VARCHAR, duration_s FLOAT, transcript_json VARCHAR)")
    db.execute("INSERT INTO calls VALUES ('old', 'done', 1800.0, ?)", (json.dumps(MONO),))
    db.commit()
    db.close()
    (cfg.uploads_dir / "old.mp3").write_bytes(b"audio")
    assert up.main(["--dry-run"]) == 0
    assert "Re-transcribe (no speakers), then score: 1" in capsys.readouterr().out
    columns = [r[1] for r in sqlite3.connect(cfg.db_path).execute("PRAGMA table_info(calls)")]
    assert "findings" not in columns       # still the old shape: nothing was migrated


def test_a_real_run_needs_asking_for(cfg: Settings) -> None:
    seed_all(cfg)
    with pytest.raises(SystemExit):
        up.main([])


def test_no_database_is_a_clear_error(cfg: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert up.main(["--dry-run"]) == 1
    assert "No database" in capsys.readouterr().err


def _call(cfg: Settings, call_id: str) -> Call:
    with Session(make_engine(cfg.db_path)) as s:
        return s.get(Call, call_id)


def test_real_run_requeues_scores_and_keeps_the_old_score(cfg: Settings) -> None:
    seed_all(cfg)
    scorer = FakeScorer()
    assert up.main(["--yes"], make_scorer=lambda: scorer) == 0
    assert len(scorer.seen) == 2     # labelled and no-audio

    mono = _call(cfg, "mono")
    assert (mono.status, mono.transcript_json, mono.attempts) == (CallStatus.queued, None, 0)
    assert mono.score_json is not None                                  # still shows until replaced
    assert json.loads(mono.score_history)[0]["transcript"]["segments"][0]["text"] == "hello"

    done = _call(cfg, "labelled")
    assert done.status == CallStatus.done and done.overall_score == 50 and done.scorer_version == "2.new"
    history = json.loads(done.score_history)
    assert history[0]["overall_score"] == 39 and history[0]["score"]["overall_score"] == 39
    assert "123-45-6789" not in done.score_json                         # redacted like any other score

    assert _call(cfg, "current").scorer_version == "2.old"              # left alone
    assert _call(cfg, "running").status == CallStatus.transcribing
    assert list(cfg.db_path.parent.glob("calls-before-v2-*.db"))        # backed up first


def test_a_requeued_call_can_be_claimed(cfg: Settings) -> None:
    from api import leases
    seed(cfg, "mono", transcript_json=json.dumps(MONO), score_json=json.dumps(make_score(39)))
    up.main(["--yes"], make_scorer=FakeScorer)
    assert leases.claim(make_engine(cfg.db_path), "mac-1").id == "mono"


def test_running_it_again_scores_nothing_new(cfg: Settings) -> None:
    seed_all(cfg)
    up.main(["--yes"], make_scorer=FakeScorer)
    again = FakeScorer()
    up.main(["--yes"], make_scorer=lambda: again)
    assert again.seen == []


def test_a_failed_score_keeps_the_old_one_and_is_retried(cfg: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    seed(cfg, "labelled", transcript_json=json.dumps(LABELLED), score_json=json.dumps(make_score(39)))
    assert up.main(["--yes"], make_scorer=lambda: FakeScorer(fail=True)) == 1
    call = _call(cfg, "labelled")
    assert json.loads(call.score_json)["overall_score"] == 39 and call.status == CallStatus.done
    retry = FakeScorer()
    assert up.main(["--yes"], make_scorer=lambda: retry) == 0
    assert len(retry.seen) == 1
