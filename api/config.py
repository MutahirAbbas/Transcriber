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


WORKER_MODES = ("local", "remote")


@dataclass(frozen=True)
class Settings:
    db_path: Path
    uploads_dir: Path              # web uploads; data/calls stays the benchmark's reference set
    provider: str                  # active transcriber, e.g. "whisper_local"
    scorecard_path: Path
    # local: the in-process worker transcribes and scores.
    # remote: never transcribes here — a Mac worker claims calls over /api/worker; we only score.
    worker_mode: str = "local"
    worker_token: str | None = None          # bearer token for /api/worker/*; unset = worker API refuses all
    keywords_path: Path = ROOT / "data" / "keywords.txt"   # sent to the worker with each claim
    session_secret: str | None = None        # signs the login cookie; required whenever auth is on
    auth_disabled: bool = False              # AUTH_DISABLED=1: honoured in local mode only

    @property
    def remote(self) -> bool:
        return self.worker_mode == "remote"

    @property
    def auth_enabled(self) -> bool:
        # never off on the server: AUTH_DISABLED is ignored in remote mode
        return not (self.auth_disabled and not self.remote)


def load_settings() -> Settings:
    mode = os.environ.get("WORKER_MODE", "local").strip().lower()
    if mode not in WORKER_MODES:
        raise ValueError(f"WORKER_MODE must be one of {WORKER_MODES}, not {mode!r}")
    return Settings(
        db_path=Path(os.environ.get("CALLBENCH_DB", ROOT / "data" / "call-bench.db")),
        uploads_dir=Path(os.environ.get("CALLBENCH_UPLOADS_DIR", ROOT / "data" / "uploads")),
        provider=os.environ.get("TRANSCRIBER", "whisper_local"),
        scorecard_path=ROOT / "scoring" / "scorecard.yaml",
        worker_mode=mode,
        worker_token=os.environ.get("WORKER_TOKEN") or None,
        keywords_path=Path(os.environ.get("CALLBENCH_KEYWORDS", ROOT / "data" / "keywords.txt")),
        session_secret=os.environ.get("SESSION_SECRET") or None,
        auth_disabled=os.environ.get("AUTH_DISABLED", "").strip().lower() in ("1", "true", "yes"),
    )


def load_scorecard(path: Path) -> Scorecard:
    card = yaml.safe_load(path.read_text())
    return Scorecard(
        categories=list(card.get("categories", {})),
        required_phrases=list(card.get("required_phrases", [])),
    )
