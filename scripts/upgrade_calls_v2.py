#!/usr/bin/env python
"""Bring existing calls up to scorecard v2.

    python scripts/upgrade_calls_v2.py --dry-run     counts only: opens the database read-only
    python scripts/upgrade_calls_v2.py --yes         does it

Calls with audio and no speakers go back in the queue: the transcriber downloads the audio as
usual, which also gives them speakers and a waveform, and the server scores the new transcript
when it arrives. Every other call with a transcript is scored here, one at a time. Old scores
stay in score_history. Safe to run again: anything already on scorecard v2 is left alone.

The real run backs the database up first. Stop nothing: it's fine with the app running, but in
local mode restart the app afterwards so its worker picks up the queue.

Prints counts only, never ids or text: ids hold phone numbers.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from api import repo  # noqa: E402
from api.config import Settings, load_settings  # noqa: E402  (loads .env)
from api.db import make_engine  # noqa: E402
from api.media import find_audio  # noqa: E402
from api.models import Call, CallStatus, utcnow  # noqa: E402
from api.redaction import redact_score  # noqa: E402
from api.worker import Scorer, result_from_dict  # noqa: E402

REQUEUE = "requeue"            # re-transcribe, then scored when the transcript comes back
RESCORE = "rescore"            # scored here from the stored transcript
CURRENT = "current"            # already on scorecard v2
IN_PROGRESS = "in_progress"    # left alone: it gets v2 anyway when it finishes
CANT = "cant"                  # no transcript and no audio: nothing to work from

IN_PROGRESS_STATUSES = ("queued", "transcribing", "separating", "scoring")
COLUMNS = ("id", "status", "duration_s", "transcript_json", "scorer_version", "speaker_source", "waveform", "cancelled_at")


def _has_speakers(transcript_json: str | None) -> bool:
    if not transcript_json:
        return False
    return any(s.get("speaker") for s in json.loads(transcript_json).get("segments", []))


def classify(row: dict[str, Any], has_audio: bool) -> str:
    """What this call needs. `row` may come from a database that predates the v2 columns."""
    if row.get("cancelled_at") or row["status"] in IN_PROGRESS_STATUSES:
        return IN_PROGRESS
    transcript = row.get("transcript_json")
    speakers = _has_speakers(transcript)
    # speaker_source "none" with a waveform means a v2 worker already tried and found no labels
    tried = row.get("speaker_source") == "none" and row.get("waveform")
    if has_audio and not speakers and not tried and row.get("speaker_source") in (None, "none"):
        return REQUEUE
    if not transcript:
        return CANT
    return CURRENT if str(row.get("scorer_version") or "").startswith("2.") else RESCORE


def _plan(rows: Iterable[dict[str, Any]], uploads_dir: Path) -> tuple[Counter[str], float]:
    counts: Counter[str] = Counter()
    seconds = 0.0
    for row in rows:
        action = classify(row, find_audio(uploads_dir, row["id"]) is not None)
        counts[action] += 1
        if action in (REQUEUE, RESCORE):
            seconds += row.get("duration_s") or 0.0
    return counts, seconds


def read_rows(db_path: Path) -> list[dict[str, Any]]:
    """Read-only, and tolerant of a database the v2 app has never opened: make_engine would add
    the new columns, and a dry run must change nothing."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        db.row_factory = sqlite3.Row
        have = {r["name"] for r in db.execute("PRAGMA table_info(calls)")}
        picked = ", ".join(c if c in have else f"NULL AS {c}" for c in COLUMNS)
        return [dict(r) for r in db.execute(f"SELECT {picked} FROM calls")]
    finally:
        db.close()


def report(counts: Counter[str], seconds: float, out: Callable[[str], None] = print) -> None:
    total = sum(counts.values())
    claude = counts[REQUEUE] + counts[RESCORE]
    out(f"Calls in the database:                   {total}")
    out(f"  Re-transcribe (no speakers), then score: {counts[REQUEUE]}")
    out(f"  Score from the stored transcript:        {counts[RESCORE]}")
    out(f"  Already on scorecard v2, left alone:     {counts[CURRENT]}")
    out(f"  In progress, left alone (v2 on finish):  {counts[IN_PROGRESS]}")
    out(f"  No transcript or audio, can't upgrade:   {counts[CANT]}")
    out(f"Estimated Claude calls:                  {claude}")
    out(f"Audio those calls cover:                 {seconds / 3600:.1f} h")


def requeue(call: Call) -> None:
    """Back to the queue. The old transcript is kept in score_history until the new one replaces
    it: claiming needs transcript_json empty, and a failed re-transcription shouldn't lose it."""
    history = json.loads(call.score_history) if call.score_history else []
    history.append({"replaced_transcript_at": utcnow().isoformat(), "transcript": json.loads(call.transcript_json or "null")})
    call.score_history = json.dumps(history, ensure_ascii=False)
    call.transcript_json = None
    call.status, call.progress, call.error, call.attempts = CallStatus.queued, 0.0, None, 0
    call.lease_until, call.worker_id = None, None


def backup(db_path: Path) -> Path:
    dest = db_path.with_name(f"{db_path.stem}-before-v2-{utcnow():%Y%m%d-%H%M%S}.db")
    src = sqlite3.connect(db_path)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    return dest


def upgrade(settings: Settings, make_scorer: Callable[[], Scorer], out: Callable[[str], None] = print) -> Counter[str]:
    """The real run. A call the scorer fails on keeps its old score and is retried by running again."""
    out(f"Backed up to {backup(settings.db_path).name}")
    engine = make_engine(settings.db_path)
    done: Counter[str] = Counter()
    with Session(engine) as s:
        calls = s.exec(select(Call)).all()
        todo = {c.id: classify(c.model_dump(), find_audio(settings.uploads_dir, c.id) is not None) for c in calls}
        for call in calls:
            if todo[call.id] == REQUEUE:
                requeue(call)
                s.add(call)
                done["requeued"] += 1
        s.commit()
    to_score = [cid for cid, action in todo.items() if action == RESCORE]
    scorer = make_scorer() if to_score else None
    for cid in to_score:
        with Session(engine) as s:
            call = s.get(Call, cid)
            if call is None or not call.transcript_json:
                continue
            res = result_from_dict(json.loads(call.transcript_json), cid)
        result = scorer.score(res)   # outside the session: it can take a minute or more
        with Session(engine) as s:
            call = s.get(Call, cid)
            if call is None:
                continue
            if result.get("parse_error"):
                done["failed"] += 1
                out(f"Scoring failed ({done['scored'] + done['failed']} of {len(to_score)}): {result.get('reason', 'unknown')}")
                continue
            repo.apply_score(s, call, redact_score(result))
            call.status, call.progress, call.error = CallStatus.done, 1.0, None
            s.add(call)
            s.commit()
            done["scored"] += 1
            out(f"Scored {done['scored'] + done['failed']} of {len(to_score)}")
    return done


def main(argv: list[str] | None = None, make_scorer: Callable[[], Scorer] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print counts and change nothing")
    mode.add_argument("--yes", action="store_true", help="do the upgrade")
    args = ap.parse_args(argv)

    settings = load_settings()
    if not settings.db_path.exists():
        print(f"No database at {settings.db_path}", file=sys.stderr)
        return 1
    counts, seconds = _plan(read_rows(settings.db_path), settings.uploads_dir)
    report(counts, seconds)
    if args.dry_run:
        print("Dry run: nothing changed.")
        return 0

    def default_scorer() -> Scorer:
        from scoring.rate_call import CallScorer
        return CallScorer()

    done = upgrade(settings, make_scorer or default_scorer)
    print(f"Queued for re-transcription: {done['requeued']}. Scored: {done['scored']}. Failed: {done['failed']}.")
    return 1 if done["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
