"""Load pipeline outputs (results/processed/*.json) into the web app's DB, redacted.

For building and testing the UI against real calls without re-transcribing.

    python scripts/import_processed.py              # everything in results/processed/
    python scripts/import_processed.py a.json b.json

Skips files already imported (same original filename). If the audio is in
data/calls/<call_id>.<ext> it's hard-linked to data/calls/<new id>.<ext> so the
player works. Prints ids and scores only — never transcript text.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from api import derive, repo  # noqa: E402
from api.config import ROOT, load_settings  # noqa: E402  (loads .env)
from api.db import make_engine  # noqa: E402
from api.media import AUDIO_TYPES, agent_slug_from_filename, make_call_id  # noqa: E402
from api.models import Call, CallStatus, utcnow  # noqa: E402
from api.redaction import redact_score, redact_transcript_dict  # noqa: E402


def find_audio(calls_dir: Path, stem: str) -> Path | None:
    return next((p for ext in AUDIO_TYPES if (p := calls_dir / f"{stem}{ext}").exists()), None)


def link_or_copy(src: Path, dest: Path) -> None:
    try:
        os.link(src, dest)
    except OSError:
        shutil.copy2(src, dest)


def import_file(session: Session, path: Path, calls_dir: Path) -> str | None:
    record = json.loads(path.read_text())
    transcript = redact_transcript_dict(record["transcript"])
    score = redact_score(record["score"])
    stem = record.get("call_id") or path.stem
    audio = find_audio(calls_dir, stem)
    filename = audio.name if audio else stem

    if session.exec(select(Call).where(Call.filename == filename)).first():
        print(f"skip  {path.name}: already imported")
        return None

    call = Call(
        id=make_call_id(filename), filename=filename,
        duration_s=transcript.get("audio_duration_s"), stereo=record.get("stereo"),
        provider=transcript.get("provider"), model=transcript.get("model"),
        transcript_json=json.dumps(transcript, ensure_ascii=False), progress=1.0,
    )
    if score.get("parse_error"):
        call.status, call.error = CallStatus.error, f"Scoring failed: {score.get('reason', 'unknown')}"
    else:
        call.status, call.scored_at = CallStatus.done, utcnow()
        call.score_json = json.dumps(score, ensure_ascii=False)
    if slug := agent_slug_from_filename(filename):
        call.agent_id = repo.find_or_create_agent(session, slug).id
    elif isinstance(score.get("agent_name"), str) and (agent := repo.agent_for_name(session, score["agent_name"])):
        call.agent_id = agent.id
    session.add(call)

    if audio:
        link_or_copy(audio, calls_dir / f"{call.id}{audio.suffix.lower()}")
    rtf = derive.realtime_factor(transcript)
    print(f"added {call.id}: {len(transcript.get('segments', []))} segments, "
          f"score {derive.overall_score(score)}, rtf {f'{rtf:.3f}' if rtf else '-'}"
          f"{'' if audio else ' (no audio found)'}")
    return call.id


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", type=Path)
    args = ap.parse_args()
    files = args.files or sorted((ROOT / "results" / "processed").glob("*.json"))
    if not files:
        print("nothing to import", file=sys.stderr)
        return 1

    settings = load_settings()
    settings.calls_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_path)
    with Session(engine) as session:
        for path in files:
            import_file(session, path, settings.calls_dir)
            session.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
