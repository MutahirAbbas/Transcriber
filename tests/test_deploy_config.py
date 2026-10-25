"""Guard rails for the server setup (deploy/, Dockerfile). Docker isn't run here; these pin
the constraints from docs/02-deploy.md so a later edit can't quietly break them."""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from api.app import create_app
from api.config import ROOT, Settings

COMPOSE = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text())
APP, CADDY = COMPOSE["services"]["app"], COMPOSE["services"]["caddy"]


def test_compose_is_its_own_project_and_network() -> None:
    assert COMPOSE["name"] == "call-analyzer"
    assert set(COMPOSE["services"]) == {"app", "caddy"}
    assert set(COMPOSE["networks"]) == {"call-analyzer"}
    for svc in (APP, CADDY):
        assert set(svc["networks"]) == {"call-analyzer"}
        assert svc["restart"] == "unless-stopped"


def test_only_caddy_publishes_ports() -> None:
    assert "ports" not in APP and "expose" not in APP
    assert CADDY["ports"] == ["80:80", "443:443"]


def test_app_container() -> None:
    assert APP["env_file"] == "/srv/call-analyzer/.env"
    assert "/srv/call-analyzer/data:/app/data" in APP["volumes"]
    assert "/api/health" in " ".join(APP["healthcheck"]["test"])


def test_app_trusts_forwarded_headers_only_from_caddy() -> None:
    caddy_ip = CADDY["networks"]["call-analyzer"]["ipv4_address"]
    assert APP["environment"]["FORWARDED_ALLOW_IPS"] == caddy_ip
    ipam = COMPOSE["networks"]["call-analyzer"]["ipam"]["config"][0]
    assert caddy_ip.rsplit(".", 1)[0] == ipam["subnet"].rsplit(".", 1)[0]


def test_caddyfile() -> None:
    caddy = (ROOT / "deploy" / "Caddyfile").read_text()
    assert caddy.startswith("calls.tqc4.ai {")
    for line in ("reverse_proxy app:8000", "max_size 500MB", "encode gzip", 'Strict-Transport-Security "max-age=31536000"',
                 "X-Content-Type-Options nosniff", "Referrer-Policy same-origin", "X-Frame-Options DENY"):
        assert line in caddy


def test_dockerfile() -> None:
    df = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^FROM node:\S+ AS web$", df, re.M)
    assert "FROM python:3.12-slim" in df
    assert "ffmpeg" in df and "requirements-server.txt" in df
    assert re.search(r"^USER app$", df, re.M)
    cmd = df[df.rindex("CMD"):]
    for part in ('"0.0.0.0"', '"8000"', '"--workers", "1"', '"--proxy-headers"'):
        assert part in cmd
    assert "mlx" not in df.lower()


def test_build_context_is_code_only() -> None:
    lines = [l for l in (ROOT / ".dockerignore").read_text().splitlines() if l and not l.startswith("#")]
    assert lines[0] == "**"   # allowlist: everything else stays out
    allowed = {l[1:] for l in lines if l.startswith("!")}
    assert not any(a.startswith(("data/calls", "data/uploads", "results", ".env", "worker.env")) for a in allowed)
    assert "data/keywords.txt" in allowed


def test_server_requirements_have_no_transcription_libraries() -> None:
    packages = [l.split("#")[0].strip() for l in (ROOT / "requirements-server.txt").read_text().splitlines()]
    packages = [p for p in packages if p]
    assert packages and not any(p.startswith(("mlx", "assemblyai")) for p in packages)
    union = (ROOT / "requirements.txt").read_text()
    assert "-r requirements-server.txt" in union and "-r requirements-mac.txt" in union


def test_server_env_example_lists_what_the_brief_asks_for() -> None:
    keys = {l.split("=", 1)[0] for l in (ROOT / "deploy" / ".env.example").read_text().splitlines()
            if l and not l.startswith("#")}
    assert keys == {"ANTHROPIC_API_KEY", "SCORER_MODEL", "WORKER_MODE", "WORKER_TOKEN", "SESSION_SECRET", "PUBLIC_URL"}
    assert "WORKER_MODE=remote" in (ROOT / "deploy" / ".env.example").read_text()


@pytest.mark.parametrize("script", ["deploy/deploy.sh", "deploy/backup.sh", "scripts/install_worker_mac.sh",
                                    "scripts/uninstall_worker_mac.sh", "scripts/serve.sh"])
def test_scripts_are_executable(script: str) -> None:
    assert (ROOT / script).stat().st_mode & 0o111


def test_scripts_only_touch_their_compose_project() -> None:
    for script in ("deploy/deploy.sh", "deploy/backup.sh"):
        text = (ROOT / script).read_text()
        assert "-p call-analyzer" in text
        assert "prune" not in text and " down" not in text


def test_health_needs_no_login(settings: Settings) -> None:
    app = create_app(dataclasses.replace(settings, worker_mode="remote", worker_token="t"), start_worker=False)
    with TestClient(app, base_url="https://testserver") as c:
        assert c.get("/api/health").json() == {"ok": True}
        assert c.get("/api/calls").status_code == 401
