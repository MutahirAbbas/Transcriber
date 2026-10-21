"""PII redaction for transcript and score text.

Regex-only, so it catches structured identifiers: SSNs, card numbers, phone
numbers, other long digit runs (routing / bank account / policy numbers),
Medicare IDs, full dates of birth and emails — typed or spoken as words.
Names and street addresses need NER and are NOT covered. Money amounts, ages,
times and current-year dates are left alone.

Never log what a pattern matched: it's customer data.
"""
from __future__ import annotations

import copy
import dataclasses
import re
from typing import Any

from transcribers.base import TranscriptResult

# ---- emails --------------------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SPOKEN_EMAIL = re.compile(
    r"\b[\w.]+\s+at\s+(?:gmail|yahoo|aol|hotmail|outlook|icloud|comcast|att|verizon|msn|live|me)"
    r"\s+dot\s+(?:com|net|org)\b",
    re.I,
)

# ---- Medicare Beneficiary Identifier: 1EG4-TE5-MK73 ------------------------------
# letters exclude S L O I B Z

_L = "[AC-HJKMNP-RT-Y]"
_MBI = re.compile(rf"\b[1-9]{_L}[{_L[1:-1]}\d]\d-?{_L}[{_L[1:-1]}\d]\d-?{_L}{{2}}\d{{2}}\b", re.I)

# ---- dates of birth: full dates whose year is 1900–2019 --------------------------

_MONTH = (
    r"(?:January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\.?"
)
_UNITS = "one|two|three|four|five|six|seven|eight|nine"
_TEENS = "ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
_TENS = "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
_ORDINALS = (
    "first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth"
    "|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|thirtieth"
)
_DAY = (
    rf"(?:\d{{1,2}}(?:st|nd|rd|th)?|(?:twenty|thirty)[\s-]+(?:{_ORDINALS}|{_UNITS})|{_ORDINALS})"
)
_SPOKEN_YEAR = (
    rf"(?:nineteen[\s-]+(?:hundred[\s-]+(?:and[\s-]+)?)?"
    rf"(?:(?:{_TENS})(?:[\s-]+(?:{_UNITS}))?|{_TEENS}|oh[\s-]+(?:{_UNITS}))"
    rf"|two[\s-]+thousand(?:[\s-]+and)?(?:[\s-]+(?:{_UNITS}|{_TEENS}))?)"
)
_YEAR = rf"(?:19\d\d|20[01]\d|{_SPOKEN_YEAR})"
_DOB = re.compile(
    r"\b(?:"
    rf"(?:0?[1-9]|1[0-2])[/.-](?:0?[1-9]|[12]\d|3[01])[/.-](?:19\d\d|20[01]\d)"   # 03/14/1951
    rf"|(?:19\d\d|20[01]\d)-\d\d-\d\d"                                             # 1951-03-14
    rf"|{_MONTH}\s+{_DAY},?\s+{_YEAR}"                                             # March 14th, 1951
    rf"|{_DAY}\s+(?:of\s+)?{_MONTH},?\s+{_YEAR}"                                   # 14th of March 1951
    r")\b",
    re.I,
)

# ---- digit runs, typed or spoken: SSN / card / phone / account -------------------

_WORD_DIGITS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16",
    "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90",
}
_REPEAT = {"double": 2, "triple": 3}
_TOKEN = r"(?:\d+|" + "|".join(sorted({*_WORD_DIGITS, *_REPEAT}, key=len, reverse=True)) + r")"
# "(" only opens a run (area code); inside a run it would swallow "(12:01)" timestamps
_SEP = r"(?:[ \t]*[-.][ \t]*|,[ \t]+|\)[ \t]*|[ \t]+)"
_RUN = re.compile(rf"(?<![\w$])(?<!\d,)\(?{_TOKEN}(?:{_SEP}{_TOKEN})*(?!\w)", re.I)
_MIN_DIGITS = 7

_MONEY_AFTER = re.compile(r"\s*(?:dollars|bucks|cents|percent|%)", re.I)
_SSN_SHAPE = re.compile(r"\d{3}[ -]\d{2}[ -]\d{4}")
_SSN_CONTEXT = re.compile(r"social|\bssn\b|s\.s\.n", re.I)
_PHONE7_SHAPE = re.compile(r"\d{3}[ .-]\d{4}")
# a full date that _DOB didn't take is a recent date (callback, draft date), not an ID
_DATE_SHAPE = re.compile(r"(?:0?[1-9]|1[0-2])([.-])(?:0?[1-9]|[12]\d|3[01])\1\d{4}")


def _digits_of(run: str) -> str:
    out: list[str] = []
    repeat = 1
    for tok in re.findall(_TOKEN, run, re.I):
        low = tok.lower()
        if low in _REPEAT:
            repeat = _REPEAT[low]
            continue
        out.append((_WORD_DIGITS.get(low) or tok) * repeat)
        repeat = 1
    return "".join(out)


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _classify(digits: str, raw: str, before: str) -> str:
    n = len(digits)
    if 13 <= n <= 19 and _luhn(digits):
        return "CARD"
    if n == 9 and (_SSN_SHAPE.fullmatch(raw) or _SSN_CONTEXT.search(before)):
        return "SSN"
    if n == 10 or (n == 11 and digits[0] == "1") or (n == 7 and _PHONE7_SHAPE.fullmatch(raw)):
        return "PHONE"
    return "ACCOUNT"


def _mask_run(m: re.Match[str]) -> str:
    raw = m.group(0)
    digits = _digits_of(raw)
    if len(digits) < _MIN_DIGITS or _MONEY_AFTER.match(m.string, m.end()) or _DATE_SHAPE.fullmatch(raw):
        return raw
    before = m.string[max(0, m.start() - 40):m.start()]
    return f"[{_classify(digits, raw.lstrip('('), before)}]"


# ---- public API ----------------------------------------------------------------

def redact_text(text: str) -> str:
    if not text:
        return text
    text = _EMAIL.sub("[EMAIL]", text)
    text = _SPOKEN_EMAIL.sub("[EMAIL]", text)
    text = _MBI.sub("[MEDICARE_ID]", text)
    text = _DOB.sub("[DOB]", text)
    return _RUN.sub(_mask_run, text)


def redact_result(res: TranscriptResult) -> TranscriptResult:
    """Copy with every segment redacted and the raw provider response dropped.

    Shaped for `pipeline.process_call(redact=...)`, so the scorer never sees PII."""
    segments = [dataclasses.replace(s, text=redact_text(s.text)) for s in res.segments]
    return dataclasses.replace(res, segments=segments, raw={})


def redact_transcript_dict(d: dict) -> dict:
    """Same as redact_result, for a stored `TranscriptResult.to_dict()`."""
    out = copy.deepcopy(d)
    out.pop("raw", None)
    for seg in out.get("segments", []):
        seg["text"] = redact_text(seg.get("text") or "")
    return out


# Structural fields the UI relies on verbatim; _meta is ours, not call content.
_SCORE_KEEP = {"timestamp", "agent_speaker", "agent_name", "call_outcome", "_meta"}


def redact_score(score: dict) -> dict:
    """Every free-text string in the scorer output, at any depth."""
    def walk(v: Any) -> Any:
        if isinstance(v, str):
            return redact_text(v)
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: (copy.deepcopy(x) if k in _SCORE_KEEP else walk(x)) for k, x in v.items()}
        return v
    return walk(score)
