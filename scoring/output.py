"""The rules on the scorer's answer, enforced in code after the model replies.

`normalize_score` is pure: anything structurally wrong raises ScoreInvalid (the call goes to
`error` and Re-score retries). Things the scorecard decides, like which flags exist, the overall
score and the compliance cap, are decided here and never taken from the model.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Collection
from typing import Any

log = logging.getLogger(__name__)

OUTCOMES = ("sale", "callback", "no_sale", "not_interested", "wrong_number", "unclear")
PLAN_TYPES = ("level", "graded", "guaranteed_issue", "unknown")
KINDS = ("flag", "coach", "good")
MAX_COACH = 8
MAX_WORK_ITEMS = 6

_TS = re.compile(r"^(?:(\d{1,2}):)?(\d{1,3}):(\d{2})$")


class ScoreInvalid(ValueError):
    """The reason names fields only. It's stored and shown, so it never quotes the answer."""


def timestamp_s(ts: str) -> int:
    m = _TS.match(ts)
    if not m:
        raise ScoreInvalid(f"bad timestamp format ({len(ts)} chars)")
    hours, minutes, seconds = m.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)


def _mmss(ts: Any, where: str) -> str:
    """mm:ss with minutes allowed past 59, the form the transcript uses."""
    if not isinstance(ts, str):
        raise ScoreInvalid(f"{where}: timestamp missing")
    total = timestamp_s(ts.strip())
    return f"{total // 60:02d}:{total % 60:02d}"


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScoreInvalid(f"{where}: text missing")
    return value.strip()


def _opt_str(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _opt_num(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def _customer(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    age = _opt_num(value.get("age"))
    out = {
        "first_name": _opt_str(value.get("first_name")),
        "last_name": _opt_str(value.get("last_name")),
        "age": int(age) if age is not None else None,
        "state": _opt_str(value.get("state")),
    }
    return out if any(v is not None for v in out.values()) else None


def _sale(value: Any, outcome: str) -> dict[str, Any] | None:
    if outcome != "sale" or not isinstance(value, dict):
        return None
    plan = value.get("plan_type")
    return {
        "coverage_usd": _opt_num(value.get("coverage_usd")),
        "premium_monthly_usd": _opt_num(value.get("premium_monthly_usd")),
        "carrier": _opt_str(value.get("carrier")),
        "plan_type": plan if plan in PLAN_TYPES else "unknown",
    }


def _scores(value: Any, card: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ScoreInvalid("scores missing")
    out: dict[str, dict[str, Any]] = {}
    for key in card["categories"]:
        entry = value.get(key)
        score = entry.get("score") if isinstance(entry, dict) else None
        if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 10:
            raise ScoreInvalid(f"scores.{key}: must be a whole number from 1 to 10")
        out[key] = {"score": score, "evidence": _text(entry.get("evidence"), f"scores.{key}.evidence")}
    return out


def _findings(value: Any, card: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Valid findings in time order, and how many flags were dropped for an unknown type."""
    if not isinstance(value, list):
        raise ScoreInvalid("findings missing")
    flag_types = {f["id"]: f for f in card["flag_types"]}
    out: list[dict[str, Any]] = []
    dropped = coaches = 0
    for i, f in enumerate(value):
        where = f"findings[{i}]"
        if not isinstance(f, dict) or f.get("kind") not in KINDS:
            raise ScoreInvalid(f"{where}: kind must be one of {', '.join(KINDS)}")
        kind = f["kind"]
        item: dict[str, Any] = {
            "kind": kind,
            "category": _opt_str(f.get("category")) or ("compliance" if kind == "flag" else "general"),
            "timestamp": _mmss(f.get("timestamp"), where),
            "text": _text(f.get("text"), where),
        }
        if kind == "flag":
            flag = flag_types.get(f.get("flag_type"))
            if flag is None:
                dropped += 1
                continue
            item.update(flag_type=flag["id"], severity=flag["severity"], label=flag["label"])
        elif kind == "coach":
            coaches += 1
            if coaches > MAX_COACH:
                continue
            item["better"] = _text(f.get("better"), f"{where}.better")
        out.append(item)
    if dropped:
        log.warning("scorer raised %d flag(s) outside flag_types; dropped", dropped)
    return out, dropped


def _required_phrases(value: Any, card: dict[str, Any], transcript_text: str) -> list[dict[str, Any]]:
    """One entry per scorecard phrase. The model's word stands where it gave one; a phrase it
    skipped is looked up in the transcript."""
    given = {r.get("phrase"): r for r in value if isinstance(r, dict)} if isinstance(value, list) else {}
    out = []
    for req in card["required_phrases"]:
        phrase = req["phrase"]
        entry = given.get(phrase)
        if entry is not None and isinstance(entry.get("present"), bool):
            present = entry["present"]
            ts = _mmss(entry["timestamp"], f"required_phrases[{phrase}]") if present and entry.get("timestamp") else None
        else:
            present, ts = phrase.lower() in transcript_text.lower(), None
        out.append({"phrase": phrase, "present": present, "timestamp": ts})
    return out


def _missing_phrase_flags(phrases: list[dict[str, Any]], findings: list[dict[str, Any]],
                          card: dict[str, Any]) -> list[dict[str, Any]]:
    flag_types = {f["id"]: f for f in card["flag_types"]}
    raised = {f.get("flag_type") for f in findings}
    added = []
    for req, seen in zip(card["required_phrases"], phrases):
        flag = flag_types[req["flag"]]
        if not seen["present"] and flag["id"] not in raised:
            raised.add(flag["id"])
            added.append({
                "kind": "flag", "category": "compliance", "timestamp": "00:00",
                "text": f"“{req['phrase']}” was not said"
                        + (" before any personal data." if req.get("before_personal_data") else "."),
                "flag_type": flag["id"], "severity": flag["severity"], "label": flag["label"],
            })
    return added


def _phases(value: Any, card: dict[str, Any]) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ScoreInvalid("phases must be a list")
    known = {p["id"] for p in card["phases"]}
    out = []
    for i, p in enumerate(value):
        if not isinstance(p, dict) or p.get("phase") not in known:
            raise ScoreInvalid(f"phases[{i}]: unknown phase")
        out.append({"phase": p["phase"], "start": _mmss(p.get("start"), f"phases[{i}]")})
    return sorted(out, key=lambda p: timestamp_s(p["start"]))    # stable: a repeat phase keeps its place


def _work_on_next(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise ScoreInvalid("work_on_next missing")
    items = [_text(item, "work_on_next.items") for item in value["items"]][:MAX_WORK_ITEMS]
    return {"intro": _text(value.get("intro"), "work_on_next.intro"), "items": items}


def overall(scores: dict[str, dict[str, Any]], card: dict[str, Any]) -> int:
    """Sum of score/10 × weight, rounding halves up, in whole numbers so it can't drift."""
    total = sum(scores[key]["score"] * cat["weight"] for key, cat in card["categories"].items())
    return (total + 5) // 10


def apply_compliance_cap(scores: dict[str, dict[str, Any]], findings: list[dict[str, Any]],
                         card: dict[str, Any]) -> None:
    cap = card["compliance_cap"]
    if any(f["kind"] == "flag" and f.get("severity") == cap["if_any"] for f in findings):
        entry = scores["compliance"]
        if entry["score"] > cap["max_score"]:
            entry["capped_from"] = entry["score"]
            entry["score"] = cap["max_score"]


def normalize_score(data: Any, card: dict[str, Any], speakers: Collection[str] = (), transcript_text: str = "") -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ScoreInvalid("answer is not a JSON object")
    outcome = data.get("call_outcome")
    if outcome not in OUTCOMES:
        raise ScoreInvalid("call_outcome is not one of the allowed values")
    agent_speaker = _opt_str(data.get("agent_speaker"))
    scores = _scores(data.get("scores"), card)
    findings, dropped = _findings(data.get("findings"), card)
    phrases = _required_phrases(data.get("required_phrases"), card, transcript_text)
    findings += _missing_phrase_flags(phrases, findings, card)
    findings.sort(key=lambda f: timestamp_s(f["timestamp"]))
    apply_compliance_cap(scores, findings, card)
    return {
        "agent_speaker": agent_speaker if agent_speaker in speakers else None,
        "agent_name": _opt_str(data.get("agent_name")),
        "customer": _customer(data.get("customer")),
        "call_outcome": outcome,
        "sale": _sale(data.get("sale"), outcome),
        "summary": _text(data.get("summary"), "summary"),
        "scores": scores,
        "overall_score": overall(scores, card),
        "phases": _phases(data.get("phases"), card),
        "findings": findings,
        "required_phrases": phrases,
        "work_on_next": _work_on_next(data.get("work_on_next")),
        "_meta": {"dropped_flags": dropped},
    }
