"""Peaks for the player's waveform: ffmpeg decodes to mono 8 kHz, max-abs per bin, scaled 0–1."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

BINS = 400
SAMPLE_RATE = 8000


def compute_waveform(audio_path: Path, bins: int = BINS) -> list[float]:
    """Exactly `bins` values, or [] if the file can't be read: the call matters more than its
    picture, so this never raises."""
    try:
        import numpy as np
        pcm = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(audio_path), "-ac", "1", "-ar", str(SAMPLE_RATE),
             "-f", "f32le", "-"],
            capture_output=True, check=True,
        ).stdout
        samples = np.abs(np.frombuffer(pcm, dtype=np.float32))
        edges = np.linspace(0, len(samples), bins + 1).astype(int)
        peaks = [float(samples[a:b].max()) if b > a else 0.0 for a, b in zip(edges, edges[1:])]
        top = max(peaks, default=0.0)
        return [round(p / top, 3) if top > 0 else 0.0 for p in peaks]
    except Exception as e:
        log.warning("couldn't compute a waveform: %s", type(e).__name__)
        return []
