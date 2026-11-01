"""Speaker separation with no model: assignment logic, and every way of it being off."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from api.redaction import redact_result, redact_transcript_dict
from transcribers import diarize
from transcribers.base import Segment, TranscriptResult
from transcribers.diarize import assign_speakers, separate_speakers


def word(start: float, end: float, text: str) -> dict:
    return {"start": start, "end": end, "text": text}


def result(*segments: Segment) -> TranscriptResult:
    return TranscriptResult(provider="whisper_local", model="m", call_id="c1", segments=list(segments),
                            latency_s=1.0, audio_duration_s=10.0)


@pytest.fixture(autouse=True)
def fresh_pipeline(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DIARIZER", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    load = diarize._load_pipeline
    load.cache_clear()
    yield
    load.cache_clear()


def test_a_segment_spanning_two_speakers_is_split() -> None:
    seg = Segment(0.0, 4.0, "hi there hello you", words=[
        word(0.0, 1.0, "hi"), word(1.0, 2.0, "there"), word(2.0, 3.0, "hello"), word(3.0, 4.0, "you")])
    out = assign_speakers([seg], [(0.0, 2.0, "SPEAKER_0"), (2.0, 4.0, "SPEAKER_1")])
    assert [(s.start, s.end, s.text, s.speaker) for s in out] == [
        (0.0, 2.0, "hi there", "SPEAKER_0"), (2.0, 4.0, "hello you", "SPEAKER_1")]


def test_a_straddling_word_goes_by_its_midpoint() -> None:
    # 1.5–2.1 straddles the change at 2.0; its midpoint 1.8 is still the first speaker's
    seg = Segment(0.0, 3.0, "one two three", words=[word(0.0, 1.5, "one"), word(1.5, 2.1, "two"), word(2.1, 3.0, "three")])
    out = assign_speakers([seg], [(0.0, 2.0, "SPEAKER_0"), (2.0, 3.0, "SPEAKER_1")])
    assert [(s.text, s.speaker) for s in out] == [("one two", "SPEAKER_0"), ("three", "SPEAKER_1")]


def test_a_word_in_a_gap_takes_the_nearest_turn() -> None:
    seg = Segment(0.0, 6.0, "a b c", words=[word(2.2, 2.4, "a"), word(4.6, 4.8, "b")])
    out = assign_speakers([seg], [(0.0, 2.0, "SPEAKER_0"), (5.0, 6.0, "SPEAKER_1")])
    assert [(s.text, s.speaker) for s in out] == [("a", "SPEAKER_0"), ("b", "SPEAKER_1")]


def test_a_segment_without_word_timings_goes_by_its_midpoint() -> None:
    out = assign_speakers([Segment(3.0, 5.0, "no words")], [(0.0, 2.0, "SPEAKER_0"), (3.5, 6.0, "SPEAKER_1")])
    assert (out[0].text, out[0].speaker) == ("no words", "SPEAKER_1")


def test_segments_with_no_text_are_dropped() -> None:
    segments = [Segment(0.0, 1.0, "   "), Segment(1.0, 2.0, "hi", words=[word(1.0, 2.0, "hi")]),
                Segment(2.0, 3.0, " ", words=[word(2.0, 3.0, " ")])]
    assert [s.text for s in assign_speakers(segments, [(0.0, 3.0, "SPEAKER_0")])] == ["hi"]


def test_no_turns_changes_nothing() -> None:
    seg = Segment(0.0, 1.0, "hi")
    assert assign_speakers([seg], []) == [seg]


def test_off_by_default() -> None:
    called: list[str] = []
    res = separate_speakers(result(Segment(0, 1, "hi")), Path("x.wav"), False, on_start=lambda: called.append("x"))
    assert res.speaker_source == "none" and res.segments[0].speaker is None and not called


def test_stereo_and_provider_labels_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIARIZER", "pyannote")
    labelled = result(Segment(0, 1, "hi", "SPEAKER_0"))
    assert separate_speakers(labelled, Path("x.wav"), True).speaker_source == "stereo"
    assert separate_speakers(labelled, Path("x.wav"), False).speaker_source == "provider"


def test_missing_token_warns_once_with_the_model_page_and_continues(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("DIARIZER", "pyannote")
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            res = separate_speakers(result(Segment(0, 1, "hi")), Path("x.wav"), False)
            assert res.speaker_source == "none"
    warnings = [r.getMessage() for r in caplog.records]
    assert len(warnings) == 1 and "HF_TOKEN" in warnings[0] and diarize.MODEL_PAGES[0] in warnings[0]


def test_a_crash_mid_call_leaves_it_unlabelled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIARIZER", "pyannote")
    monkeypatch.setattr(diarize, "_load_pipeline", lambda: object())
    def boom(_path: Path) -> list:
        raise RuntimeError("customer said something")
    monkeypatch.setattr(diarize, "diarize", boom)
    res = separate_speakers(result(Segment(0, 1, "hi")), Path("x.wav"), False)
    assert res.speaker_source == "none" and res.segments[0].speaker is None


def test_diarized_call_reports_its_stage_and_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIARIZER", "pyannote")
    monkeypatch.setattr(diarize, "_load_pipeline", lambda: object())
    monkeypatch.setattr(diarize, "diarize", lambda _p: [(0.0, 1.0, "SPEAKER_0")])
    stages: list[str] = []
    res = separate_speakers(result(Segment(0, 1, "hi")), Path("x.wav"), False, on_start=lambda: stages.append("separating"))
    assert stages == ["separating"] and res.speaker_source == "diarized" and res.segments[0].speaker == "SPEAKER_0"
    assert res.separation_s >= 0


def test_word_timings_never_survive_redaction() -> None:
    # spoken digit by digit: each word alone looks harmless, so the words can't be kept
    words = [word(i, i + 1, w) for i, w in enumerate("one two three four five six seven eight nine".split())]
    res = redact_result(result(Segment(0, 9, "my social is one two three four five six seven eight nine", words=words)))
    assert res.segments[0].words == [] and "one two" not in res.segments[0].text
    stored = redact_transcript_dict(result(Segment(0, 9, "x", words=words)).to_dict())
    assert "words" not in stored["segments"][0]


def test_pipeline_runs_separation_between_transcribing_and_scoring_on_unredacted_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import pipeline

    class Fake:
        def transcribe(self, audio: Path, call_id: str, keywords: list[str] | None = None) -> TranscriptResult:
            return result(Segment(0, 1, "hi there", words=[word(0, 0.5, "hi"), word(0.5, 1, "there")]))

    class FakeScorer:
        def score(self, res: TranscriptResult) -> dict:
            self.seen = res
            return {"overall_score": 50}

    monkeypatch.setenv("DIARIZER", "pyannote")
    monkeypatch.setattr(pipeline, "get_transcriber", lambda *_a, **_k: Fake())
    monkeypatch.setattr(diarize, "_load_pipeline", lambda: object())
    monkeypatch.setattr(diarize, "diarize", lambda _p: [(0.0, 1.0, "SPEAKER_0")])
    stages: list[str] = []
    scorer = FakeScorer()
    rec = pipeline.process_call(tmp_path / "a.wav", stereo=False, keywords=[], call_id="c1", on_status=stages.append,
                                scorer=scorer, out_dir=None, redact=redact_result)
    assert stages == ["transcribing", "separating", "scoring", "done"]
    assert rec["transcript"]["speaker_source"] == "diarized"
    assert scorer.seen.segments[0].speaker == "SPEAKER_0" and scorer.seen.segments[0].words == []
