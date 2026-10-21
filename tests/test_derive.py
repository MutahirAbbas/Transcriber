from __future__ import annotations

from datetime import date

import pytest

from api import derive
from api.media import agent_slug_from_filename, make_call_id
from api.repo import week_bounds
from tests.conftest import make_score, make_transcript

ORDER = ["opening", "discovery", "value_presentation", "objection_handling", "compliance", "close"]


def test_weakest_takes_lowest_and_breaks_ties_in_scorecard_order() -> None:
    cats = {"opening": 8, "discovery": 4, "value_presentation": 7,
            "objection_handling": 4, "compliance": 9, "close": 5}
    assert derive.weakest_category(make_score(60, cats=cats), ORDER) == "discovery"


def test_flags_count_red_flags_and_missing_phrases() -> None:
    assert derive.flag_count(make_score(50, missing=["recorded"], red_flags=2)) == 3
    assert derive.flag_count(make_score(90)) == 0
    assert derive.flag_count(None) == 0


def test_parse_error_scores_derive_nothing() -> None:
    bad = {"parse_error": True, "reason": "invalid JSON", "raw": "{", "_meta": {}}
    assert derive.overall_score(bad) is None
    assert derive.outcome(bad) is None
    assert derive.weakest_category(bad, ORDER) is None


def test_speaker_map() -> None:
    tx = make_transcript([(0, "hi", "SPEAKER_0"), (2, "hello", "SPEAKER_1")])
    assert derive.speaker_map(make_score(70, agent_speaker="SPEAKER_1"), tx) == {
        "SPEAKER_1": "Agent", "SPEAKER_0": "Customer"}
    assert derive.speaker_map(None, tx) == {}
    assert derive.speaker_map(make_score(70, agent_speaker=None), tx) == {}


def test_transcript_lines_map_speakers_and_label_unlabelled_turns() -> None:
    tx = make_transcript([(2, "Hi there", "SPEAKER_0"), (65, "Hello", "SPEAKER_1"), (3700, "Bye", None)])
    lines = derive.transcript_lines(tx, {"SPEAKER_0": "Agent", "SPEAKER_1": "Customer"})
    assert lines == ["[00:02] Agent: Hi there", "[01:05] Customer: Hello", "[61:40] Speaker: Bye"]


@pytest.mark.parametrize("name, slug", [
    ("20261021-141502_jblake.wav", "jblake"),
    ("20261021-141502_J.Blake.mp3", "j-blake"),
    ("20261021-141502_jblake-in.wav", "jblake"),
    ("20261021-141502_jblake-OUT.wav", "jblake"),
    ("20261021-141502_jblake-all.wav", "jblake"),
    # Vici puts the customer's phone number there: no agent
    ("20261017-014852_2022625450-all.mp3", None),
    ("20261017-014852_2022625450.wav", None),
    ("20261017-014852_2022625450-out.wav", None),
    ("recording.wav", None),
    ("20261021_.wav", None),
    ("20261021_-all.wav", None),
])
def test_agent_slug_from_filename(name: str, slug: str | None) -> None:
    assert agent_slug_from_filename(name) == slug


def test_call_id_sanitizes_stem() -> None:
    cid = make_call_id("my call (1).wav")
    stem, suffix = cid.rsplit("-", 1)
    assert stem == "my-call-1"
    assert len(suffix) == 6 and suffix.isalnum()


def test_week_runs_monday_to_monday() -> None:
    start, end = week_bounds(date(2026, 10, 21))   # a Wednesday
    assert (start.date(), end.date()) == (date(2026, 10, 19), date(2026, 10, 26))
    assert start.hour == 0 and start.tzinfo is not None
