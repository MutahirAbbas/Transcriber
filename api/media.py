"""Upload helpers: call ids, agent-from-filename, ffprobe."""
from __future__ import annotations

import re
import secrets
import string
import subprocess
from pathlib import Path

from transcribers import audio_channels, audio_duration

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".oga", ".opus", ".aac", ".webm"}
MAX_DURATION_S = 2 * 60 * 60

_ID_ALPHABET = string.ascii_lowercase + string.digits


def looks_like_audio(filename: str, content_type: str | None) -> bool:
    return Path(filename).suffix.lower() in AUDIO_EXTS or (content_type or "").startswith("audio/")


def make_call_id(filename: str) -> str:
    """`<original stem>-<6 char random>`, stem sanitized to [A-Za-z0-9_-]."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(filename).stem).strip("-") or "call"
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(6))
    return f"{stem}-{suffix}"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def agent_slug_from_filename(filename: str) -> str | None:
    """Last `_`-separated token before the extension: 20261021-141502_jblake.wav → jblake."""
    stem = Path(filename).stem
    if "_" not in stem:
        return None
    return slugify(stem.rsplit("_", 1)[1]) or None


class UnreadableAudio(Exception):
    pass


def probe(path: Path) -> tuple[float, bool]:
    """(duration seconds, stereo) via ffprobe."""
    try:
        duration = audio_duration(path)
        channels = audio_channels(path)
    except (subprocess.CalledProcessError, ValueError) as e:
        raise UnreadableAudio("Not a readable audio file") from e
    return duration, channels == 2
