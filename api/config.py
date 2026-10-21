"""Settings from env. Import this before `pipeline` / `scoring`: they read env vars
at import time, so .env has to be loaded first."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Scorecard:
    categories: list[str]          # scorecard order — tie-break for "weakest"
    required_phrases: list[str]


@dataclass(frozen=True)
class Settings:
    db_path: Path
    calls_dir: Path
    provider: str                  # active transcriber, e.g. "whisper_local"
    scorecard_path: Path


def load_settings() -> Settings:
    return Settings(
        db_path=Path(os.environ.get("CALLBENCH_DB", ROOT / "data" / "call-bench.db")),
        calls_dir=Path(os.environ.get("CALLBENCH_CALLS_DIR", ROOT / "data" / "calls")),
        provider=os.environ.get("TRANSCRIBER", "whisper_local"),
        scorecard_path=ROOT / "scoring" / "scorecard.yaml",
    )


def load_scorecard(path: Path) -> Scorecard:
    card = yaml.safe_load(path.read_text())
    return Scorecard(
        categories=list(card.get("categories", {})),
        required_phrases=list(card.get("required_phrases", [])),
    )
