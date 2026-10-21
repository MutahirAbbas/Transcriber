"""Production-shaped pipeline: one audio file in → transcript + score out.

This is what the web backend, the TQC watcher, and the public tool all call.
Provider is a string, so swapping Deepgram for local Whisper later is a config change.

    python pipeline.py data/calls/12345.wav --provider whisper_local
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Callable, Optional

from transcribers import audio_channels, get_transcriber
from scoring.rate_call import CallScorer

ROOT = Path(__file__).resolve().parent
DEFAULT_PROVIDER = os.environ.get("TRANSCRIBER", "whisper_local")

# Stage names the UI shows. on_status(stage) is called as each one begins.
STAGES = ("transcribing", "scoring", "done")

StatusCallback = Optional[Callable[[str], None]]


def load_keywords(path: Path = ROOT / "data" / "keywords.txt") -> list[str]:
    if not path.exists():
        return []
    return [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")]


def process_call(
    audio: Path,
    provider: str = DEFAULT_PROVIDER,
    stereo: bool | None = None,          # None → auto-detect from channel count
    keywords: list[str] | None = None,
    call_id: str | None = None,
    on_status: StatusCallback = None,
    scorer: CallScorer | None = None,    # pass one in to reuse across calls
    out_dir: Path | None = ROOT / "results" / "processed",
) -> dict:
    call_id = call_id or audio.stem
    if stereo is None:
        stereo = audio_channels(audio) == 2
    if keywords is None:
        keywords = load_keywords()

    kw = {}
    if provider == "whisper_local":
        kw["stereo_split"] = stereo
    if provider == "deepgram":
        kw["multichannel"] = stereo
    tx = get_transcriber(provider, **kw)

    if on_status:
        on_status("transcribing")
    res = tx.transcribe(audio, call_id, keywords=keywords)

    if on_status:
        on_status("scoring")
    score = (scorer or CallScorer()).score(res)

    record = {"call_id": call_id, "stereo": stereo, "transcript": res.to_dict(), "score": score}
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{call_id}.json").write_text(json.dumps(record, indent=2))
    if on_status:
        on_status("done")
    return record


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--provider", default=DEFAULT_PROVIDER)
    ap.add_argument("--stereo", action="store_true", help="force stereo split (default: auto-detect)")
    args = ap.parse_args()
    rec = process_call(Path(args.audio), args.provider, stereo=True if args.stereo else None,
                       on_status=lambda s: print(f"… {s}"))
    print(json.dumps(rec["score"], indent=2))
