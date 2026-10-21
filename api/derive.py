"""Fields derived from the stored scorer JSON on read. Never stored.

Pure functions over parsed dicts, so the routes, the CSV and the stats all
agree on what "flagged" or "weakest" means.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from api.models import Call


OUTCOME_LABELS = {
    "sale": "Sale", "callback": "Callback", "no_sale": "No sale",
    "not_interested": "Not interested", "wrong_number": "Wrong number", "unclear": "Unclear",
}


def parse_json(text: str | None) -> dict | None:
    return json.loads(text) if text else None


def usable_score(score: dict | None) -> dict | None:
    """None when there's no score yet or the scorer's output didn't parse."""
    if not score or score.get("parse_error"):
        return None
    return score


def overall_score(score: dict | None) -> int | None:
    s = usable_score(score)
    v = s.get("overall_score") if s else None
    return round(v) if isinstance(v, (int, float)) else None


def outcome(score: dict | None) -> str | None:
    s = usable_score(score)
    return s.get("call_outcome") if s else None


def flag_count(score: dict | None) -> int:
    s = usable_score(score)
    comp = (s or {}).get("compliance") or {}
    return len(comp.get("red_flags") or []) + len(comp.get("missing") or [])


def category_scores(score: dict | None) -> dict[str, float]:
    s = usable_score(score)
    out: dict[str, float] = {}
    for cat, v in ((s or {}).get("scores") or {}).items():
        val = v.get("score") if isinstance(v, dict) else v
        if isinstance(val, (int, float)):
            out[cat] = float(val)
    return out


def lowest_category(scores: dict[str, float], order: Sequence[str]) -> str | None:
    """Lowest score; ties go to the first in scorecard order, then anything unknown."""
    if not scores:
        return None
    rank = {cat: i for i, cat in enumerate(order)}
    return min(scores, key=lambda c: (scores[c], rank.get(c, len(rank))))


def weakest_category(score: dict | None, order: Sequence[str]) -> str | None:
    return lowest_category(category_scores(score), order)


def category_label(cat: str | None) -> str:
    return cat.replace("_", " ").capitalize() if cat else ""


def speaker_map(score: dict | None, transcript: dict | None) -> dict[str, str]:
    """{agent label: "Agent", every other label: "Customer"}; empty before scoring."""
    s = usable_score(score)
    agent = s.get("agent_speaker") if s else None
    if not agent:
        return {}
    labels = {seg.get("speaker") for seg in (transcript or {}).get("segments", []) if seg.get("speaker")}
    return {label: ("Agent" if label == agent else "Customer") for label in labels | {agent}}


def realtime_factor(transcript: dict | None) -> float | None:
    if not transcript:
        return None
    dur = transcript.get("audio_duration_s") or 0
    lat = transcript.get("latency_s")
    return lat / dur if dur and lat is not None else None


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def call_summary(call: Call, agent_name: str | None, order: Sequence[str]) -> dict[str, Any]:
    score = parse_json(call.score_json)
    return {
        "id": call.id,
        "filename": call.filename,
        "agent_id": call.agent_id,
        "agent": agent_name,
        "uploaded_at": iso(call.uploaded_at),
        "duration_s": call.duration_s,
        "stereo": call.stereo,
        "status": call.status.value,
        "progress": call.progress,
        "error": call.error,
        "provider": call.provider,
        "model": call.model,
        "scored_at": iso(call.scored_at),
        "overall_score": overall_score(score),
        "outcome": outcome(score),
        "flag_count": flag_count(score),
        "weakest_category": weakest_category(score, order),
    }


def fmt_ts(seconds: float) -> str:
    """mm:ss with minutes allowed past 59 — same as the scorer's timestamps."""
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def transcript_lines(transcript: dict | None, speakers: dict[str, str]) -> list[str]:
    lines = []
    for seg in (transcript or {}).get("segments", []):
        label = seg.get("speaker")
        who = speakers.get(label, label) if label else "Speaker"
        lines.append(f"[{fmt_ts(seg.get('start') or 0)}] {who}: {(seg.get('text') or '').strip()}")
    return lines
