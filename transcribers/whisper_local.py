"""Local Whisper via mlx-whisper (Apple Silicon).

`pip install mlx-whisper`  — runs on the M4 Pro GPU, no API key, $0.
Diarization is NOT included here. Two options:
  1. If Vici records stereo (agent on one channel, customer on the other), set
     stereo_split=True and we transcribe each channel separately — free, exact diarization.
  2. Otherwise add pyannote later (needs a HF token) and merge by timestamp.
"""
from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

from .base import Segment, Transcriber, TranscriptResult, audio_duration


class WhisperLocalTranscriber(Transcriber):
    name = "whisper_local"

    def __init__(self, model: str = "mlx-community/whisper-large-v3-turbo", stereo_split: bool = False):
        self.model = model
        self.stereo_split = stereo_split
        self.price_per_hour_usd = 0.0

    def _run(self, path: Path, prompt: str | None) -> list[Segment]:
        import mlx_whisper
        out = mlx_whisper.transcribe(
            str(path),
            path_or_hf_repo=self.model,
            language="en",
            initial_prompt=prompt,
            word_timestamps=False,
        )
        return [Segment(start=s["start"], end=s["end"], text=s["text"].strip()) for s in out["segments"]]

    def transcribe(self, audio_path: Path, call_id: str, keywords: list[str] | None = None) -> TranscriptResult:
        # Whisper has no keyword boosting; seeding the prompt with domain terms is the usual trick.
        prompt = ", ".join(keywords) if keywords else None
        dur = audio_duration(audio_path)
        t0 = time.perf_counter()

        if self.stereo_split:
            segments: list[Segment] = []
            with tempfile.TemporaryDirectory() as td:
                for ch, label in ((0, "SPEAKER_0"), (1, "SPEAKER_1")):
                    mono = Path(td) / f"ch{ch}.wav"
                    subprocess.run(
                        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(audio_path),
                         "-filter_complex", f"[0:a]pan=mono|c0=c{ch}[a]", "-map", "[a]", str(mono)],
                        check=True,
                    )
                    for s in self._run(mono, prompt):
                        s.speaker = label
                        segments.append(s)
            segments.sort(key=lambda s: s.start)
        else:
            segments = self._run(audio_path, prompt)

        latency = time.perf_counter() - t0
        return TranscriptResult(
            provider=self.name, model=self.model.split("/")[-1], call_id=call_id,
            segments=segments, latency_s=latency, audio_duration_s=dur, cost_usd=0.0,
        )
