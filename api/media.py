"""Upload helpers: call ids, agent-from-filename, ffprobe."""
from __future__ import annotations

import re
import secrets
import string
import subprocess
from pathlib import Path
from typing import BinaryIO

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
# Matches Caddy's request_body max_size 500MB (decimal). Telephony recordings are far
# smaller (8 kHz stereo WAV ≈ 115 MB/h); 44.1 kHz stereo WAV passes this at ≈ 47 min.
MAX_UPLOAD_BYTES = 500_000_000
_CHUNK = 1 << 20


class UploadTooLarge(Exception):
    pass


def save_upload(src: BinaryIO, dest: Path, limit: int = MAX_UPLOAD_BYTES) -> int:
    """Copy an upload to disk 1 MB at a time, stopping at `limit` bytes. Returns the size.

    Starlette has already spooled the multipart file to a temp file on disk (anything
    over 1 MB), so no part of a large upload sits in memory."""
    written = 0
    with dest.open("wb") as out:
        while chunk := src.read(_CHUNK):
            written += len(chunk)
            if written > limit:
                raise UploadTooLarge
            out.write(chunk)
    return written

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


_VICI_LEG = re.compile(r"-(?:all|in|out)$", re.I)


def agent_slug_from_filename(filename: str) -> str | None:
    """Last `_`-separated token before the extension: 20261021-141502_jblake.wav → jblake.

    Vici appends -all/-in/-out (which legs are recorded). If what's left is numeric
    it's the customer's phone number, not an agent → None, so the scorer's
    agent_name fallback applies.
    """
    stem = Path(filename).stem
    if "_" not in stem:
        return None
    token = _VICI_LEG.sub("", stem.rsplit("_", 1)[1])
    if not re.search(r"[A-Za-z]", token):
        return None
    return slugify(token) or None


def find_audio(directory: Path, call_id: str) -> Path | None:
    """A call's stored upload: <directory>/<call_id>.<ext>."""
    return next(iter(sorted(directory.glob(f"{call_id}.*"))), None)


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
