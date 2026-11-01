from __future__ import annotations

import struct
import wave
from pathlib import Path

from transcribers.waveform import BINS, compute_waveform


def _wav(path: Path, samples: list[int], rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def test_peaks_are_400_bins_scaled_to_one_and_in_the_right_place(tmp_path: Path) -> None:
    # a second of near-silence, then a second at half volume, then a second at full volume
    audio = _wav(tmp_path / "a.wav", [50] * 8000 + [16000] * 8000 + [32000] * 8000)
    peaks = compute_waveform(audio)
    assert len(peaks) == BINS == 400
    assert all(0.0 <= p <= 1.0 for p in peaks)
    assert max(peaks) == 1.0
    assert peaks[60] < 0.01 and 0.4 < peaks[200] < 0.6 and peaks[340] == 1.0


def test_silence_is_all_zeros_not_a_division_error(tmp_path: Path) -> None:
    assert compute_waveform(_wav(tmp_path / "s.wav", [0] * 8000)) == [0.0] * BINS


def test_a_clip_shorter_than_the_bin_count_still_gives_every_bin(tmp_path: Path) -> None:
    assert len(compute_waveform(_wav(tmp_path / "t.wav", [1000] * 50))) == BINS


def test_an_unreadable_file_gives_nothing_rather_than_raising(tmp_path: Path) -> None:
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio")
    assert compute_waveform(bad) == []
