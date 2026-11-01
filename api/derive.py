"""Fields derived from the stored scorer JSON on read. Never stored.

Pure functions over parsed dicts, so the routes, the CSV and the stats all
agree on what "flagged" or "weakest" means.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from api.models import Call, CallStatus


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
    if s and "findings" in s:
        return sum(1 for f in s["findings"] if f.get("kind") == "flag")
    comp = (s or {}).get("compliance") or {}    # scored before scorecard v2
    return len(comp.get("red_flags") or []) + len(comp.get("missing") or [])


def critical_count(score: dict | None) -> int:
    s = usable_score(score)
    return sum(1 for f in (s or {}).get("findings") or [] if f.get("kind") == "flag" and f.get("severity") == "critical")


def is_v2(score: dict | None) -> bool:
    s = usable_score(score)
    return bool(s and "findings" in s)


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


def category_label(cat: str | None, labels: Mapping[str, str] | None = None) -> str:
    if not cat:
        return ""
    return (labels or {}).get(cat) or cat.replace("_", " ").capitalize()


def speaker_map(score: dict | None, transcript: dict | None) -> dict[str, str]:
    """{agent label: "Agent", every other label: "Customer"}; empty before scoring."""
    s = usable_score(score)
    agent = s.get("agent_speaker") if s else None
    if not agent:
        return {}
    labels = {seg.get("speaker") for seg in (transcript or {}).get("segments", []) if seg.get("speaker")}
    return {label: ("Agent" if label == agent else "Customer") for label in labels | {agent}}


def speakers(score: dict | None, transcript: dict | None, agent_name: str | None) -> dict[str, str]:
    """Speaker label → name to show: the agent's name and the customer's first name when known,
    else Agent / Customer, else Speaker 1, 2… when nobody has worked out who's who."""
    labels = sorted({seg.get("speaker") for seg in (transcript or {}).get("segments", []) if seg.get("speaker")})
    s = usable_score(score)
    agent = s.get("agent_speaker") if s else None
    if not agent:
        return {label: f"Speaker {i}" for i, label in enumerate(labels, 1)}
    agent_shown = agent_name or s.get("agent_name") or "Agent"
    customer_shown = (s.get("customer") or {}).get("first_name") or "Customer"
    return {label: agent_shown if label == agent else customer_shown for label in {*labels, agent}}


def stage(call: Call, worker_online: bool | None) -> str:
    """queued, waiting (queued with the remote transcriber offline), or the status itself."""
    if call.status == CallStatus.queued and worker_online is False:
        return "waiting"
    return call.status.value


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


def call_summary(
    call: Call, agent_name: str | None, order: Sequence[str],
    *, labels: Mapping[str, str] | None = None, worker_online: bool | None = None, eta_s: int | None = None,
) -> dict[str, Any]:
    score = parse_json(call.score_json)
    weakest = weakest_category(score, order)
    usable = usable_score(score)
    return {
        "id": call.id,
        "filename": call.filename,
        "agent_id": call.agent_id,
        "agent": {"id": call.agent_id, "name": agent_name} if call.agent_id is not None and agent_name else None,
        # the name the scorer heard, offered first when someone assigns this call
        "suggested_agent": (usable or {}).get("agent_name") if call.agent_id is None else None,
        "uploaded_at": iso(call.uploaded_at),
        "duration_s": call.duration_s,
        "stereo": call.stereo,
        "status": call.status.value,
        "stage": stage(call, worker_online),
        "progress": call.progress,
        "eta_s": eta_s,
        "error": call.error,
        "provider": call.provider,
        "model": call.model,
        "scored_at": iso(call.scored_at),
        "overall_score": call.overall_score if call.overall_score is not None else overall_score(score),
        "outcome": outcome(score),
        "flag_count": flag_count(score),
        "critical_count": critical_count(score),
        "weakest_category": weakest,
        "weakest_label": category_label(weakest, labels) or None,
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
