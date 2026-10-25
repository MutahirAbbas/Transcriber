from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.config import Settings

INDEX = "<!doctype html><div id=root></div>"


@pytest.fixture
def spa(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX)
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside dist")
    with TestClient(create_app(settings, start_worker=False, web_dist=dist)) as c:
        yield c


def test_root_serves_index(spa: TestClient) -> None:
    r = spa.get("/")
    assert r.status_code == 200 and r.text == INDEX
    assert r.headers["cache-control"] == "no-cache"


def test_built_files_are_served(spa: TestClient) -> None:
    r = spa.get("/assets/index-abc123.js")
    assert r.status_code == 200 and r.text == "console.log(1)"
    assert "javascript" in r.headers["content-type"]


@pytest.mark.parametrize("path", ["/calls/some-call-id", "/anything/else", "/assets/missing.js"])
def test_client_routes_fall_back_to_index(spa: TestClient, path: str) -> None:
    r = spa.get(path)
    assert r.status_code == 200 and r.text == INDEX


def test_api_is_untouched(spa: TestClient) -> None:
    assert spa.get("/api/settings").json()["provider"] == "whisper_local"
    for path in ("/api", "/api/nope", "/api/calls/missing-id"):
        r = spa.get(path)
        assert r.status_code == 404 and r.headers["content-type"] == "application/json"


def test_no_escape_from_dist(spa: TestClient) -> None:
    r = spa.get("/assets/%2e%2e/%2e%2e/secret.txt")
    assert "outside dist" not in r.text


def test_no_build_no_front_end(settings: Settings, tmp_path: Path) -> None:
    with TestClient(create_app(settings, start_worker=False, web_dist=tmp_path / "missing")) as c:
        assert c.get("/").status_code == 404
        assert c.get("/api/settings").status_code == 200
