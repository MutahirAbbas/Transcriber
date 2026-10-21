"""Common transcript schema every provider conforms to.

The scorer and the metrics only ever see TranscriptResult — they never know
which provider produced it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
import json


@dataclass
class Segment:
    start: float            # seconds
    end: float              # seconds
    text: str
    speaker: Optional[str] = None   # "agent" / "customer" / "SPEAKER_00" ...
    confidence: Optional[float] = None


@dataclass
class TranscriptResult:
    provider: str                   # "deepgram" | "assemblyai" | "whisper_local"
    model: str                      # e.g. "nova-3", "best", "large-v3-turbo"
    call_id: str
    segments: list[Segment]
    latency_s: float                # wall-clock seconds for the API/model call
    audio_duration_s: float
    cost_usd: float = 0.0           # estimated, from provider pricing
    raw: dict = field(default_factory=dict)   # untouched provider response

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip())

    def text_for_speaker(self, speaker: str) -> str:
        return " ".join(s.text for s in self.segments if s.speaker == speaker)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d

    def save(self, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.call_id}.{self.provider}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path


class Transcriber:
    """Adapter interface. Subclasses implement transcribe()."""

    name: str = "base"
    model: str = ""
    price_per_hour_usd: float = 0.0   # fill from the provider's current pricing page

    def transcribe(self, audio_path: Path, call_id: str, keywords: list[str] | None = None) -> TranscriptResult:
        raise NotImplementedError

    def estimate_cost(self, duration_s: float) -> float:
        return round(self.price_per_hour_usd * duration_s / 3600, 5)


def audio_duration(path: Path) -> float:
    """Duration in seconds via ffprobe (ffmpeg must be installed: `brew install ffmpeg`)."""
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def audio_channels(path: Path) -> int:
    """Channel count of the first audio stream. 2 usually means one speaker per channel."""
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return int(out.stdout.strip() or 1)
