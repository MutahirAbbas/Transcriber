"""Colours live in the Tailwind config as named tokens, never in components."""
from __future__ import annotations

import re

from api.config import ROOT

SRC = ROOT / "web" / "src"
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
V1_CLASSES = re.compile(r"\b(?:text|bg|border)-(?:accent|red|green|amber|sale|badge|moment|control|dropzone|nav|idle)\b")


def _sources() -> list:
    return [p for p in SRC.rglob("*") if p.suffix in (".ts", ".tsx", ".css") and ".test." not in p.name]


def test_no_raw_hexes_in_components() -> None:
    assert _sources()
    assert [f"{p.name}: {m}" for p in _sources() for m in HEX.findall(p.read_text())] == []


def test_no_classes_left_from_the_v1_tokens() -> None:
    # Tailwind drops an unknown class silently, so a stale one just loses its colour
    assert [f"{p.name}: {m}" for p in _sources() for m in V1_CLASSES.findall(p.read_text())] == []
