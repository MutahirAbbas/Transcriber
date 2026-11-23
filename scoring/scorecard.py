"""Load and check scorecard.yaml. A bad scorecard should stop the app at startup, not surface
as a strange score later."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SEVERITIES = ("critical", "major")
OTHER_COACH = "other"     # a coach finding that fits no listed habit; never counted as one


def load_card(path: Path) -> dict[str, Any]:
    card = yaml.safe_load(path.read_text())
    problems = _problems(card)
    if problems:
        raise ValueError(f"{path.name}: " + "; ".join(problems))
    return card


def _problems(card: dict[str, Any]) -> list[str]:
    out: list[str] = []
    cats = card.get("categories") or {}
    if sum(c.get("weight", 0) for c in cats.values()) != 100:
        out.append("category weights must sum to 100")
    out += [f"category {key} has no label" for key, c in cats.items() if not c.get("label")]
    flags = {f["id"]: f for f in card.get("flag_types") or []}
    if len(flags) != len(card.get("flag_types") or []):
        out.append("flag_types ids must be unique")
    out += [f"flag {f['id']} has unknown severity {f.get('severity')}"
            for f in flags.values() if f.get("severity") not in SEVERITIES]
    out += [f"required phrase {r['phrase']!r} maps to unknown flag {r.get('flag')}"
            for r in card.get("required_phrases") or [] if r.get("flag") not in flags]
    out += [f"phase {p['id']} maps to unknown category {p.get('category')}"
            for p in card.get("phases") or [] if p.get("category") not in cats]
    coach = card.get("coach_types") or []
    coach_ids = [c.get("id") for c in coach]
    if len(set(coach_ids)) != len(coach_ids):
        out.append("coach_types ids must be unique")
    if OTHER_COACH not in coach_ids:
        out.append(f"coach_types needs an '{OTHER_COACH}' type")
    out += [f"coach type {c.get('id')} has no label" for c in coach if not c.get("label")]
    out += [f"coach type {c['id']} maps to unknown category {c.get('category')}"
            for c in coach if c.get("id") != OTHER_COACH and c.get("category") not in cats]
    signals = card.get("retention_signals") or []
    if len({s.get("id") for s in signals}) != len(signals):
        out.append("retention_signals ids must be unique")
    out += [f"retention signal {s.get('id')} has no label" for s in signals if not s.get("label")]
    cap = card.get("compliance_cap") or {}
    if "compliance" not in cats or cap.get("if_any") not in SEVERITIES or "max_score" not in cap:
        out.append("compliance_cap needs a compliance category, if_any and max_score")
    if not isinstance(card.get("target_score"), int):
        out.append("target_score must be a whole number")
    return out
