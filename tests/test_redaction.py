from __future__ import annotations

import pytest

from api.redaction import redact_result, redact_score, redact_text, redact_transcript_dict
from transcribers.base import Segment, TranscriptResult


@pytest.mark.parametrize(
    "text, expected",
    [
        # SSN: dashed shape, or 9 digits with "social" nearby
        ("my social is 123-45-6789 okay", "my social is [SSN] okay"),
        ("social security number 123456789", "social security number [SSN]"),
        ("it's 123 45 6789", "it's [SSN]"),
        # cards: 13–19 digits that pass Luhn
        ("card 4111 1111 1111 1111 exp", "card [CARD] exp"),
        ("the number is 4111-1111-1111-1111.", "the number is [CARD]."),
        # phones
        ("call me at (202) 555-0147 tomorrow", "call me at [PHONE] tomorrow"),
        ("202-555-0147", "[PHONE]"),
        ("1 202 555 0147", "[PHONE]"),
        ("it's 555-0147", "it's [PHONE]"),
        # other long digit runs: routing, bank account, policy numbers
        ("routing 021000021 and account 12345678", "routing [ACCOUNT] and account [ACCOUNT]"),
        ("policy number 87654321", "policy number [ACCOUNT]"),
        # spoken digit sequences
        ("it's two zero two, five five five, zero one four seven", "it's [PHONE]"),
        ("one two three four five six seven", "[ACCOUNT]"),
        ("double five, five one two three four", "[ACCOUNT]"),
        # Medicare Beneficiary Identifier
        ("my medicare number is 1EG4-TE5-MK73", "my medicare number is [MEDICARE_ID]"),
        ("1eg4te5mk73", "[MEDICARE_ID]"),
        # dates of birth (full dates with a birth-range year)
        ("born 03/14/1951 in Ohio", "born [DOB] in Ohio"),
        ("March 14th, 1951", "[DOB]"),
        ("14 March 1951", "[DOB]"),
        ("the 14th of March, 1951", "the [DOB]"),
        ("Sept. 3 1948", "[DOB]"),
        ("March 14, nineteen fifty-one", "[DOB]"),
        # email, typed and spoken
        ("send it to jane.doe@example.com please", "send it to [EMAIL] please"),
        ("it's jane doe at gmail dot com", "it's jane [EMAIL]"),
    ],
)
def test_masks_identifiers(text: str, expected: str) -> None:
    assert redact_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "that's $52 a month",
        "for 10,000 in coverage",
        "fifty-two dollars a month",
        "a $1,500,000 policy",
        "I'm seventy-one years old",
        "I'm 71 and my husband is 74",
        "let's call back October 24th, 2026 at 10:30",
        "on 10/24/2026",
        "on 10-24-2026 or 10.25.2026",
        "I was born in 1951",
        "plan 2 of 3, option 4",
        "Oh, one more thing",
        "Mutual of Omaha, level benefit, graded benefit",
        "",
    ],
)
def test_leaves_ordinary_speech_alone(text: str) -> None:
    assert redact_text(text) == text


def test_idempotent() -> None:
    once = redact_text("ssn 123-45-6789, phone 202-555-0147, born March 14th, 1951")
    assert redact_text(once) == once


def test_redact_result_copies_and_clears_raw() -> None:
    res = TranscriptResult(
        provider="whisper_local", model="m", call_id="c",
        segments=[Segment(0.0, 2.0, "my number is 202-555-0147", speaker="SPEAKER_1")],
        latency_s=1.0, audio_duration_s=2.0, raw={"text": "my number is 202-555-0147"},
    )
    out = redact_result(res)
    assert out.segments[0].text == "my number is [PHONE]"
    assert out.segments[0].speaker == "SPEAKER_1"
    assert out.raw == {}
    # original untouched
    assert res.segments[0].text == "my number is 202-555-0147"
    assert res.raw


def test_redact_transcript_dict() -> None:
    d = {
        "provider": "whisper_local", "model": "m", "call_id": "c", "latency_s": 1.0,
        "audio_duration_s": 2.0, "cost_usd": 0.0,
        "segments": [{"start": 0.0, "end": 2.0, "text": "ssn 123-45-6789", "speaker": None, "confidence": None}],
    }
    out = redact_transcript_dict(d)
    assert out["segments"][0]["text"] == "ssn [SSN]"
    assert d["segments"][0]["text"] == "ssn 123-45-6789"


def test_redact_score_walks_text_and_skips_structure() -> None:
    score = {
        "agent_speaker": "SPEAKER_0",
        "call_outcome": "sale",
        "scores": {"close": {"score": 7, "evidence": "Read back card 4111 1111 1111 1111 (12:01)."}},
        "overall_score": 71,
        "strengths": ["Confirmed DOB 03/14/1951 accurately."],
        "coachable_moments": [{"timestamp": "12:01", "what_happened": "Said SSN 123-45-6789 aloud.", "better_approach": "x"}],
        "compliance": {"required_phrases_present": True, "missing": [], "red_flags": [{"timestamp": "03:10", "flag": "f"}]},
        "summary": "Customer at 202-555-0147.",
        "_meta": {"scorer_model": "claude-x", "input_tokens": 12345678},
    }
    out = redact_score(score)
    assert out["scores"]["close"]["evidence"] == "Read back card [CARD] (12:01)."
    assert out["strengths"] == ["Confirmed DOB [DOB] accurately."]
    assert out["coachable_moments"][0]["what_happened"] == "Said SSN [SSN] aloud."
    assert out["coachable_moments"][0]["timestamp"] == "12:01"
    assert out["summary"] == "Customer at [PHONE]."
    assert out["_meta"] == score["_meta"]
    assert out["overall_score"] == 71
    assert score["summary"] == "Customer at 202-555-0147."
