"""Upload helpers: call ids, agent-from-filename, ffprobe."""
from __future__ import annotations

import re
import secrets
import string
import subprocess
from pathlib import Path

from transcribers import audio_channels, audio_duration

# Extension → the only media types /audio will ever serve. Uploads are accepted by
# extension alone (never the client's Content-Type), so nothing lands on disk that
# a browser would render as a page.
AUDIO_TYPES = {
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".flac": "audio/flac", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
    ".webm": "audio/webm",
}
MAX_DURATION_S = 2 * 60 * 60

_ID_ALPHABET = string.ascii_lowercase + string.digits


def audio_ext(filename: str) -> str | None:
    ext = Path(filename).suffix.lower()
    return ext if ext in AUDIO_TYPES else None


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
