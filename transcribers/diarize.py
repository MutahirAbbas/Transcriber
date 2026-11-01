"""Speaker separation for mono recordings, with pyannote.audio on this machine.

Runs where transcription runs (the Mac worker, or the local-mode worker thread), before
redaction. Audio stays local; only the model is downloaded, using HF_TOKEN.

Optional in every way that counts: a missing token, a failed model load or a crash mid-call
all leave the call unlabelled. Diarization never fails a call.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from .base import Segment, TranscriptResult

log = logging.getLogger(__name__)

MODEL = "pyannote/speaker-diarization-community-1"
MODEL_PAGES = ("https://huggingface.co/pyannote/speaker-diarization-community-1",)
SAMPLE_RATE = 16000

Turn = tuple[float, float, str]


class DiarizationUnavailable(RuntimeError):
    """No usable pipeline; the reason has already been logged, once."""


def enabled() -> bool:
    return os.environ.get("DIARIZER", "").strip().lower() == "pyannote"


@lru_cache(maxsize=1)
def _load_pipeline() -> Any | None:
    """Once per process, whether it works or not, so a bad token warns one time."""
    terms = "accept the terms at " + ", ".join(MODEL_PAGES)
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        log.warning("speakers won't be separated: HF_TOKEN is not set (and %s)", terms)
        return None
    try:
        import torch
        from pyannote.audio import Pipeline
        pipeline = Pipeline.from_pretrained(MODEL, token=token)
        if pipeline is None:
            raise RuntimeError("no pipeline returned")
        pipeline.to(torch.device("mps" if torch.backends.mps.is_available() else "cpu"))
        return pipeline
    except Exception as e:
        log.warning("speakers won't be separated: couldn't load %s (%s); check HF_TOKEN and %s",
                    MODEL, type(e).__name__, terms)
        return None


def load() -> None:
    """Load the model up front (long-running workers call this at startup)."""
    if enabled():
        _load_pipeline()


def _read_audio(audio_path: Path) -> Any:
    """Decode to mono float samples with ffmpeg, as a (1, samples) tensor. Passing samples in
    memory means pyannote doesn't need its own decoder to agree with the installed ffmpeg."""
    import numpy as np
    import torch
    pcm = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(audio_path), "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    return torch.from_numpy(np.frombuffer(pcm, dtype=np.float32).copy()).unsqueeze(0)


def diarize(audio_path: Path) -> list[Turn]:
    """[(start, end, speaker)] in time order, speakers named SPEAKER_0, SPEAKER_1 in order of
    first appearance, the same labels a stereo split gives."""
    pipeline = _load_pipeline()
    if pipeline is None:
        raise DiarizationUnavailable
    out = pipeline({"waveform": _read_audio(audio_path), "sample_rate": SAMPLE_RATE}, num_speakers=2)
    # the "exclusive" track has no overlapping turns: it's the one meant for aligning words
    tracks = sorted((seg.start, seg.end, label)
                    for seg, _, label in out.exclusive_speaker_diarization.itertracks(yield_label=True))
    names: dict[str, str] = {}
    return [(start, end, names.setdefault(label, f"SPEAKER_{len(names)}")) for start, end, label in tracks]


def _speaker_at(t: float, turns: Sequence[Turn]) -> str:
    """The turn covering t, else the nearest one."""
    return min(turns, key=lambda turn: 0.0 if turn[0] <= t <= turn[1] else min(abs(t - turn[0]), abs(t - turn[1])))[2]


def assign_speakers(segments: Sequence[Segment], turns: Sequence[Turn]) -> list[Segment]:
    """Each word takes the speaker whose turn covers its midpoint (nearest turn if none).
    Segments are rebuilt as runs of same-speaker words, so one spanning two speakers is split.
    A segment with no word timings is treated as one long word."""
    if not turns:
        return list(segments)
    out: list[Segment] = []
    for seg in segments:
        if not seg.words:
            out.append(replace(seg, speaker=_speaker_at((seg.start + seg.end) / 2, turns)))
            continue
        run: list[dict] = []
        speaker: str | None = None
        for word in seg.words:
            who = _speaker_at((word["start"] + word["end"]) / 2, turns)
            if run and who != speaker:
                out.append(_from_words(seg, run, speaker))
                run = []
            run.append(word)
            speaker = who
        out.append(_from_words(seg, run, speaker))
    return [s for s in out if s.text.strip()]    # whisper sometimes gives a segment with no words in it


def _from_words(seg: Segment, words: list[dict], speaker: str | None) -> Segment:
    text = " ".join(w["text"].strip() for w in words if w["text"].strip())
    return replace(seg, start=words[0]["start"], end=words[-1]["end"], text=text, speaker=speaker, words=words)


def separate_speakers(
    res: TranscriptResult,
    audio_path: Path,
    stereo: bool,
    on_start: Callable[[], None] | None = None,    # called only if diarization is going to run
) -> TranscriptResult:
    """transcribe → here → redact. Sets speaker_source: stereo | diarized | provider | none."""
    if any(s.speaker for s in res.segments):
        return replace(res, speaker_source="stereo" if stereo else "provider")
    if not enabled() or not res.segments or _load_pipeline() is None:
        return replace(res, speaker_source="none")
    if on_start:
        on_start()
    t0 = time.perf_counter()
    try:
        segments = assign_speakers(res.segments, diarize(audio_path))
    except Exception as e:   # never fail a call over this (a stop or cancel is a BaseException)
        log.warning("call=%s speaker separation failed (%s); continuing unlabelled", res.call_id, type(e).__name__)
        return replace(res, speaker_source="none")
    labelled = any(s.speaker for s in segments)
    return replace(res, segments=segments, speaker_source="diarized" if labelled else "none",
                   separation_s=time.perf_counter() - t0)
