from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from api.config import ROOT, load_scorecard
from scoring.scorecard import load_card

REAL = ROOT / "scoring" / "scorecard.yaml"


def _broken(tmp_path: Path, change) -> Path:
    card = yaml.safe_load(REAL.read_text())
    change(card)
    path = tmp_path / "card.yaml"
    path.write_text(yaml.safe_dump(card))
    return path


def test_the_real_scorecard_is_v2_and_loads() -> None:
    card = load_scorecard(REAL)
    assert card.version == 2 and card.target_score == 60
    assert card.categories == ["opening", "discovery", "value_presentation", "objection_handling", "compliance", "close"]
    assert card.labels["value_presentation"] == "Value" and card.labels["objection_handling"] == "Objections"
    assert card.required_phrases == ["recorded line", "licensed agent"]
    assert [p["id"] for p in card.phases] == ["opening", "discovery", "value", "application", "objections", "close"]
    assert {p["id"]: p["category"] for p in card.phases}["application"] == "compliance"


@pytest.mark.parametrize("change, problem", [
    (lambda c: c["categories"]["close"].update(weight=11), "sum to 100"),
    (lambda c: c["categories"]["close"].pop("label"), "no label"),
    (lambda c: c["flag_types"][0].update(severity="minor"), "unknown severity"),
    (lambda c: c["flag_types"].append(dict(c["flag_types"][0])), "unique"),
    (lambda c: c["required_phrases"][0].update(flag="nope"), "unknown flag"),
    (lambda c: c["phases"][0].update(category="nope"), "unknown category"),
    (lambda c: c.pop("compliance_cap"), "compliance_cap"),
    (lambda c: c.update(target_score="sixty"), "target_score"),
])
def test_a_bad_scorecard_is_refused_at_load(tmp_path: Path, change, problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        load_card(_broken(tmp_path, change))
