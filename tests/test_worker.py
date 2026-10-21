from __future__ import annotations

import json
import queue
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlmodel import Session

from api.config import Settings
from api.models import Agent, Call, CallStatus
from api.worker import DEFAULT_RTF, Worker
from tests.conftest import make_score, make_transcript
from transcribers.base import Segment, TranscriptResult


class FakeScorer:
    def __init__(self, result: dict | None = None) -> None:
        self.result = result
        self.seen: list[TranscriptResult] = []

    def score(self, res: TranscriptResult) -> dict:
        self.seen.append(res)
        return self.result or {**make_score(72, agent_speaker="SPEAKER_0"), "agent_name": "Dana Reyes",
                               "summary": "Customer read out 202-555-0147."}


class FakePipeline:
    """Stands in for pipeline.process_call with the same keyword interface."""

    def __init__(self, engine: Any, fail: set[str] | None = None, delay: float = 0.0) -> None:
        self.engine, self.fail, self.delay = engine, fail or set(), delay
        self.kwargs: list[dict] = []
        self.progress_seen: list[float] = []

    def __call__(self, audio: Path, **kw: Any) -> dict:
        self.kwargs.append({"audio": audio, **kw})
        call_id = kw["call_id"]
        if call_id in self.fail:
            raise RuntimeError("ffmpeg exploded")
        kw["on_status"]("transcribing")
        time.sleep(self.delay)
        with Session(self.engine) as s:
            self.progress_seen.append(s.get(Call, call_id).progress)
        res = TranscriptResult(
            provider="whisper_local", model="whisper-large-v3-turbo", call_id=call_id,
            segments=[Segment(0.0, 2.0, "my number is 202-555-0147", "SPEAKER_1"),
                      Segment(2.0, 4.0, "on a recorded line", "SPEAKER_0")],
            latency_s=3.0, audio_duration_s=60.0,
        )
        res = kw["redact"](res)
        kw["on_status"]("scoring")
        score = kw["scorer"].score(res)
        kw["on_status"]("done")
        return {"call_id": call_id, "stereo": kw["stereo"], "transcript": res.to_dict(), "score": score}


@pytest.fixture
def jobs() -> queue.Queue:
    return queue.Queue()


@pytest.fixture
def make_worker(app: FastAPI, settings: Settings, jobs: queue.Queue) -> Iterator[Callable[..., Worker]]:
    started: list[Worker] = []

    def _make(pipeline: Callable[..., dict] | None = None, scorer: Any = None, start: bool = True, **kw: Any) -> Worker:
        w = Worker(app.state.engine, jobs, settings.uploads_dir, "whisper_local",
                   pipeline or FakePipeline(app.state.engine), lambda: scorer or FakeScorer(), **kw)
        if start:
            w.start()
            started.append(w)
        return w

    yield _make
    for w in started:
        w.stop(timeout=5)


@pytest.fixture
def with_audio(settings: Settings, add_call) -> Callable[..., Call]:
    def _add(call_id: str, **kw: Any) -> Call:
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        (settings.uploads_dir / f"{call_id}.wav").write_bytes(b"RIFF")
        return add_call(call_id, status=kw.pop("status", CallStatus.queued), **kw)
    return _add


def _get(app: FastAPI, call_id: str) -> Call:
    with Session(app.state.engine) as s:
        return s.get(Call, call_id)


def test_full_job_redacts_scores_and_saves(app: FastAPI, jobs, make_worker, with_audio) -> None:
    with_audio("c1", duration_s=60.0)
    pipeline, scorer = FakePipeline(app.state.engine), FakeScorer()
    make_worker(pipeline, scorer)
    jobs.put(("c1", "full"))
    jobs.join()

    call = _get(app, "c1")
    assert call.status == CallStatus.done and call.progress == 1.0 and call.scored_at
    assert (call.provider, call.model) == ("whisper_local", "whisper-large-v3-turbo")
    kw = pipeline.kwargs[0]
    assert kw["out_dir"] is None and kw["provider"] == "whisper_local" and kw["stereo"] is True
    # the scorer only ever saw redacted text, and what's stored is redacted
    assert scorer.seen[0].segments[0].text == "my number is [PHONE]"
    tx = json.loads(call.transcript_json)
    assert tx["segments"][0]["text"] == "my number is [PHONE]"
    assert json.loads(call.score_json)["summary"] == "Customer read out [PHONE]."


def test_agent_falls_back_to_scorer_agent_name(app: FastAPI, jobs, make_worker, with_audio) -> None:
    with_audio("c1")
    make_worker()
    jobs.put(("c1", "full"))
    jobs.join()
    with Session(app.state.engine) as s:
        agent = s.get(Agent, _get(app, "c1").agent_id)
    assert (agent.name, agent.slug) == ("Dana Reyes", "dana-reyes")


def test_failure_marks_error_and_next_job_runs(app: FastAPI, jobs, make_worker, with_audio) -> None:
    with_audio("bad")
    with_audio("good")
    make_worker(FakePipeline(app.state.engine, fail={"bad"}))
    jobs.put(("bad", "full"))
    jobs.put(("good", "full"))
    jobs.join()
    bad = _get(app, "bad")
    assert bad.status == CallStatus.error and bad.error == "ffmpeg exploded"
    assert _get(app, "good").status == CallStatus.done


def test_missing_audio_is_an_error(app: FastAPI, jobs, make_worker, add_call) -> None:
    add_call("c1", status=CallStatus.queued)
    make_worker()
    jobs.put(("c1", "full"))
    jobs.join()
    assert _get(app, "c1").error == "Audio file is missing"


def test_scorer_parse_error_keeps_transcript(app: FastAPI, jobs, make_worker, with_audio) -> None:
    with_audio("c1")
    make_worker(scorer=FakeScorer({"parse_error": True, "reason": "output truncated at max_tokens", "raw": "{"}))
    jobs.put(("c1", "full"))
    jobs.join()
    call = _get(app, "c1")
    assert call.status == CallStatus.error
    assert call.error == "Scoring failed: output truncated at max_tokens"
    assert call.transcript_json and call.score_json is None


def test_scorer_that_cannot_start_fails_each_job(app: FastAPI, jobs, settings, with_audio) -> None:
    def broken() -> FakeScorer:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    with_audio("c1")
    w = Worker(app.state.engine, jobs, settings.uploads_dir, "whisper_local", FakePipeline(app.state.engine), broken)
    w.start()
    jobs.put(("c1", "full"))
    jobs.join()
    w.stop(timeout=5)
    assert _get(app, "c1").error == "Scorer unavailable: ANTHROPIC_API_KEY not set"


def test_rescore_uses_stored_transcript(app: FastAPI, jobs, make_worker, add_call) -> None:
    tx = make_transcript([(0, "hello", "SPEAKER_0")])
    add_call("c1", status=CallStatus.scoring, transcript=tx, score=make_score(40))
    scorer = FakeScorer(make_score(88, "sale"))
    make_worker(scorer=scorer)
    jobs.put(("c1", "rescore"))
    jobs.join()
    assert scorer.seen[0].segments[0].text == "hello"
    call = _get(app, "c1")
    assert call.status == CallStatus.done and json.loads(call.score_json)["overall_score"] == 88


def test_deleted_call_is_skipped(app: FastAPI, jobs, make_worker) -> None:
    pipeline = FakePipeline(app.state.engine)
    make_worker(pipeline)
    jobs.put(("gone", "full"))
    jobs.put(("gone", "rescore"))
    jobs.join()
    assert pipeline.kwargs == []


def test_recover_requeues_unfinished(app: FastAPI, jobs, make_worker, add_call) -> None:
    tx = make_transcript([(0, "hi", "SPEAKER_0")])
    add_call("q", status=CallStatus.queued)
    add_call("t", status=CallStatus.transcribing)
    add_call("s-with-tx", status=CallStatus.scoring, transcript=tx)
    add_call("s-no-tx", status=CallStatus.scoring)
    add_call("d", status=CallStatus.done, score=make_score(70), transcript=tx)
    w = make_worker(start=False)
    assert w.recover() == 4
    queued = [jobs.get_nowait() for _ in range(4)]
    assert sorted(queued) == [("q", "full"), ("s-no-tx", "full"), ("s-with-tx", "rescore"), ("t", "full")]
    assert _get(app, "t").status == CallStatus.queued
    assert _get(app, "s-with-tx").status == CallStatus.scoring


def test_rtf_defaults_then_learns_from_finished_calls(app: FastAPI, add_call, make_worker) -> None:
    w = make_worker(start=False)
    assert w.current_rtf(stereo=False) == DEFAULT_RTF
    add_call("mono", score=make_score(70), transcript={**make_transcript([]), "latency_s": 6.0, "audio_duration_s": 100.0})
    with Session(app.state.engine) as s:
        call = s.get(Call, "mono")
        call.stereo, call.provider = False, "whisper_local"
        s.add(call)
        s.commit()
    assert w.current_rtf(stereo=False) == pytest.approx(0.06)
    assert w.current_rtf(stereo=True) == pytest.approx(0.06)   # no stereo samples yet → any layout


def test_progress_moves_while_transcribing(app: FastAPI, jobs, make_worker, with_audio) -> None:
    with_audio("c1", duration_s=1.0)                      # expected ≈ 1 s × 0.15 RTF
    pipeline = FakePipeline(app.state.engine, delay=0.3)
    make_worker(pipeline, tick_s=0.02)
    jobs.put(("c1", "full"))
    jobs.join()
    assert pipeline.progress_seen[0] == pytest.approx(0.9)   # capped
