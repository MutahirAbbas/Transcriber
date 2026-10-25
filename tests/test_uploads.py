from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import media
from api.routes import calls as calls_routes


class ReadSpy(io.BytesIO):
    """Records the biggest read, to prove uploads are copied in bounded chunks."""

    biggest = 0

    def read(self, size: int | None = -1) -> bytes:   # type: ignore[override]
        chunk = super().read(size)
        ReadSpy.biggest = max(ReadSpy.biggest, len(chunk))
        return chunk


def test_save_upload_copies_in_1mb_chunks(tmp_path: Path) -> None:
    ReadSpy.biggest = 0
    data = b"x" * (3 * 1024 * 1024 + 17)
    assert media.save_upload(ReadSpy(data), tmp_path / "a.wav") == len(data)
    assert (tmp_path / "a.wav").read_bytes() == data
    assert ReadSpy.biggest <= 1024 * 1024


def test_save_upload_stops_at_the_limit(tmp_path: Path) -> None:
    with pytest.raises(media.UploadTooLarge):
        media.save_upload(io.BytesIO(b"x" * 3000), tmp_path / "a.wav", limit=2000)


def test_limit_is_500mb() -> None:
    assert media.MAX_UPLOAD_BYTES == 500_000_000


def test_oversized_upload_is_refused_and_nothing_kept(client: TestClient, make_wav, settings,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(calls_routes, "MAX_UPLOAD_BYTES", 1000)
    big = make_wav("big_jblake.wav", seconds=1)   # 16 KB
    r = client.post("/api/calls", files=[("files", (big.name, big.read_bytes(), "audio/wav"))])
    assert r.status_code == 413 and "larger than" in r.json()["detail"]
    assert not settings.uploads_dir.exists() or not any(settings.uploads_dir.iterdir())
    assert client.get("/api/calls").json()["total"] == 0


def test_upload_under_the_limit_is_saved_whole(client: TestClient, make_wav, settings) -> None:
    wav = make_wav("ok_jblake.wav", seconds=2)
    r = client.post("/api/calls", files=[("files", (wav.name, wav.read_bytes(), "audio/wav"))])
    assert r.status_code == 201
    saved = settings.uploads_dir / f"{r.json()[0]['id']}.wav"
    assert saved.read_bytes() == wav.read_bytes()
