"""Run every provider over every call, score against references, dump a CSV + summary.

Usage:
    python -m eval.run_benchmark --providers whisper_local deepgram assemblyai
    python -m eval.run_benchmark --providers whisper_local --calls data/calls --score   # also run Claude scorer

Layout:
    data/calls/<call_id>.wav|mp3        audio
    data/references/<call_id>.txt       human-corrected transcript (plain text, optional per call)
    data/keywords.txt                   one domain term per line
    results/transcripts/                raw per-provider transcripts (json)
    results/benchmark_<ts>.csv          one row per call × provider
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
import traceback
from pathlib import Path

from transcribers import get_transcriber
from eval.metrics import keyword_recall, speaker_turn_count, wer

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}

ROOT = Path(__file__).resolve().parent.parent


def load_keywords(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", nargs="+", required=True,
                    choices=["deepgram", "assemblyai", "whisper_local"])
    ap.add_argument("--calls", default=str(ROOT / "data" / "calls"))
    ap.add_argument("--references", default=str(ROOT / "data" / "references"))
    ap.add_argument("--keywords", default=str(ROOT / "data" / "keywords.txt"))
    ap.add_argument("--out", default=str(ROOT / "results"))
    ap.add_argument("--stereo", action="store_true", help="audio is stereo with one speaker per channel")
    ap.add_argument("--score", action="store_true", help="also run the Claude call scorer on each transcript")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    calls_dir, refs_dir, out_dir = Path(args.calls), Path(args.references), Path(args.out)
    tx_dir = out_dir / "transcripts"
    tx_dir.mkdir(parents=True, exist_ok=True)
    keywords = load_keywords(Path(args.keywords))

    audio_files = sorted(p for p in calls_dir.iterdir() if p.suffix.lower() in AUDIO_EXTS)
    if args.limit:
        audio_files = audio_files[: args.limit]
    if not audio_files:
        print(f"no audio in {calls_dir}", file=sys.stderr)
        return 1

    transcribers = {}
    for name in args.providers:
        kw = {}
        if name == "whisper_local":
            kw["stereo_split"] = args.stereo
        if name == "deepgram":
            kw["multichannel"] = args.stereo
        transcribers[name] = get_transcriber(name, **kw)

    scorer = None
    if args.score:
        from scoring.rate_call import CallScorer
        scorer = CallScorer()

    rows: list[dict] = []
    ts = time.strftime("%Y%m%d_%H%M%S")

    for audio in audio_files:
        call_id = audio.stem
        ref_path = refs_dir / f"{call_id}.txt"
        reference = ref_path.read_text() if ref_path.exists() else None

        for name, tx in transcribers.items():
            print(f"[{call_id}] {name} ...", end=" ", flush=True)
            row = {"call_id": call_id, "provider": name, "model": tx.model, "has_reference": bool(reference)}
            try:
                res = tx.transcribe(audio, call_id, keywords=keywords)
                res.save(tx_dir)
                row.update({
                    "audio_s": round(res.audio_duration_s, 1),
                    "latency_s": round(res.latency_s, 2),
                    "rtf": round(res.latency_s / res.audio_duration_s, 3) if res.audio_duration_s else None,
                    "cost_usd": res.cost_usd,
                    "words": len(res.text.split()),
                    "speaker_turns": speaker_turn_count(res.segments),
                })
                if reference:
                    row.update(wer(reference, res.text))
                    kr = keyword_recall(reference, res.text, keywords)
                    row["keyword_recall"] = kr["keyword_recall"]
                    row["keyword_misses"] = json.dumps(kr["keyword_misses"])
                if scorer:
                    score = scorer.score(res)
                    row["overall_score"] = score.get("overall_score")
                    row["score_json"] = json.dumps(score)
                print(f"ok  wer={row.get('wer')}  latency={row['latency_s']}s")
            except Exception as e:  # keep going; one provider failing shouldn't kill the run
                row["error"] = f"{type(e).__name__}: {e}"
                print(f"FAILED {row['error']}")
                traceback.print_exc(file=sys.stderr)
            rows.append(row)

    # ---- write CSV
    fields = sorted({k for r in rows for k in r})
    csv_path = out_dir / f"benchmark_{ts}.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # ---- summary
    print("\n=== summary (mean over calls) ===")
    print(f"{'provider':<14}{'wer':>8}{'kw_recall':>11}{'rtf':>8}{'$/call':>9}{'score':>8}{'n':>5}")
    for name in transcribers:
        rs = [r for r in rows if r["provider"] == name and "error" not in r]
        def mean(key):
            vals = [r[key] for r in rs if r.get(key) is not None]
            return f"{statistics.mean(vals):.3f}" if vals else "  -"
        print(f"{name:<14}{mean('wer'):>8}{mean('keyword_recall'):>11}{mean('rtf'):>8}"
              f"{mean('cost_usd'):>9}{mean('overall_score'):>8}{len(rs):>5}")
    print(f"\nwrote {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
