from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from api import auth, users
from api.app import create_app
from api.config import Settings
from api.models import User
from tests.conftest import USER_EMAIL, USER_PASSWORD, sign_in


@pytest.fixture
def app(settings: Settings) -> FastAPI:   # overrides conftest's: auth on, as on the server
    return create_app(dataclasses.replace(settings, auth_disabled=False), start_worker=False)


def _login(client: TestClient, email: str = USER_EMAIL, password: str = USER_PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


# ---- login / session -----------------------------------------------------------------

def test_login_sets_a_secure_session_cookie(client: TestClient, app: FastAPI) -> None:
    sign_in(client, app)
    cookie = next(c for c in _login(client).headers.get_list("set-cookie") if c.startswith(auth.COOKIE))
    for attr in ("HttpOnly", "Secure", "SameSite=lax", "Max-Age=1209600", "Path=/"):
        assert attr in cookie
    assert client.get("/api/auth/me").json() == {"email": USER_EMAIL, "auth_disabled": False}
    with Session(app.state.engine) as s:
        assert auth.find_user(s, USER_EMAIL).last_login is not None


def test_email_is_case_insensitive(client: TestClient, app: FastAPI) -> None:
    sign_in(client, app)
    assert _login(client, email="  Lead@Example.COM ").status_code == 200


def test_wrong_password_and_unknown_email_look_the_same(client: TestClient, app: FastAPI) -> None:
    sign_in(client, app)
    client.cookies.clear()
    wrong, unknown = _login(client, password="not the password"), _login(client, email="nobody@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Email or password is incorrect"}


@pytest.mark.parametrize("method, path", [
    ("get", "/api/calls"), ("get", "/api/calls/in-progress"), ("get", "/api/calls/x"),
    ("get", "/api/calls/x/audio"), ("get", "/api/calls/x/transcript.txt"), ("get", "/api/calls.csv"),
    ("get", "/api/stats"), ("get", "/api/agents"), ("get", "/api/settings"), ("get", "/api/worker/status"),
    ("get", "/api/auth/me"), ("post", "/api/calls"), ("post", "/api/calls/x/rescore"), ("delete", "/api/calls/x"),
])
def test_every_user_route_needs_a_session(client: TestClient, method: str, path: str) -> None:
    r = getattr(client, method)(path)
    assert r.status_code == 401 and r.json() == {"detail": "Sign in required"}


def test_logout_ends_the_session(signed_in: TestClient) -> None:
    assert signed_in.get("/api/calls").status_code == 200
    assert signed_in.post("/api/auth/logout").status_code == 204
    assert signed_in.get("/api/calls").status_code == 401


def test_password_reset_signs_out_existing_sessions(signed_in: TestClient, app: FastAPI) -> None:
    with Session(app.state.engine) as s:
        users.reset_password(s, USER_EMAIL, "a brand new password")
    assert signed_in.get("/api/calls").status_code == 401
    assert _login(signed_in, password="a brand new password").status_code == 200


def test_removed_user_is_signed_out(signed_in: TestClient, app: FastAPI) -> None:
    with Session(app.state.engine) as s:
        users.remove_user(s, USER_EMAIL)
    assert signed_in.get("/api/calls").status_code == 401


def test_tampered_and_expired_cookies_are_rejected(signed_in: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    token = signed_in.cookies[auth.COOKIE]
    signed_in.cookies.clear()
    signed_in.cookies.set(auth.COOKIE, token[:-2] + ("AA" if not token.endswith("AA") else "BB"))
    assert signed_in.get("/api/calls").status_code == 401
    signed_in.cookies.set(auth.COOKIE, token)
    assert signed_in.get("/api/calls").status_code == 200
    import itsdangerous.timed
    real = itsdangerous.timed.time.time
    monkeypatch.setattr(itsdangerous.timed.time, "time", lambda: real() + auth.SESSION_MAX_AGE_S + 60)
    assert signed_in.get("/api/calls").status_code == 401


# ---- lockout -------------------------------------------------------------------------

def test_ten_failures_lock_the_ip_out(client: TestClient, app: FastAPI) -> None:
    sign_in(client, app)
    client.cookies.clear()
    assert [_login(client, password=f"wrong {i}").status_code for i in range(10)] == [401] * 10
    r = _login(client)   # even the right password, for the next 15 minutes
    assert r.status_code == 429 and "15 min" in r.json()["detail"]


def test_lockout_expires_and_success_resets_the_count() -> None:
    now = [0.0]
    lock = auth.Lockout(clock=lambda: now[0])
    for _ in range(9):
        lock.failure("1.2.3.4")
    lock.clear("1.2.3.4")                     # a successful sign-in
    for _ in range(9):
        lock.failure("1.2.3.4")
    assert lock.seconds_left("1.2.3.4") == 0
    lock.failure("1.2.3.4")
    assert lock.seconds_left("1.2.3.4") == auth.LOCKOUT_S
    assert lock.seconds_left("5.6.7.8") == 0  # other IPs unaffected
    now[0] += auth.LOCKOUT_S + 1
    assert lock.seconds_left("1.2.3.4") == 0


# ---- modes ---------------------------------------------------------------------------

def test_auth_disabled_works_only_in_local_mode(settings: Settings) -> None:
    with TestClient(create_app(settings, start_worker=False), base_url="https://testserver") as c:   # local + AUTH_DISABLED
        assert c.get("/api/calls").status_code == 200
        assert c.get("/api/auth/me").json() == {"email": None, "auth_disabled": True}
    remote = dataclasses.replace(settings, worker_mode="remote")   # AUTH_DISABLED still set: ignored
    with TestClient(create_app(remote, start_worker=False), base_url="https://testserver") as c:
        assert c.get("/api/calls").status_code == 401


def test_session_secret_is_required_when_auth_is_on(settings: Settings) -> None:
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        create_app(dataclasses.replace(settings, auth_disabled=False, session_secret=None), start_worker=False)


# ---- pages ---------------------------------------------------------------------------

@pytest.fixture
def pages(settings: Settings, tmp_path: Path) -> Iterator[tuple[TestClient, FastAPI]]:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>")
    (dist / "assets" / "app.js").write_text("1")
    app = create_app(dataclasses.replace(settings, auth_disabled=False), start_worker=False, web_dist=dist)
    with TestClient(app, base_url="https://testserver", follow_redirects=False) as c:
        yield c, app


def test_pages_redirect_to_login_when_signed_out(pages: tuple[TestClient, FastAPI]) -> None:
    client, app = pages
    assert client.get("/").headers["location"] == "/login?next=%2F"
    r = client.get("/calls/abc", params={"tab": "2"})
    assert r.status_code == 302 and r.headers["location"] == "/login?next=%2Fcalls%2Fabc%3Ftab%3D2"
    assert client.get("/login").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    sign_in(client, app)
    assert client.get("/calls/abc").status_code == 200


# ---- CLI -----------------------------------------------------------------------------

@pytest.fixture
def cli_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "cli.db"
    monkeypatch.setenv("CALLBENCH_DB", str(db))
    return db


def _answers(*replies: str):
    it = iter(replies)
    return lambda _prompt: next(it)


def test_cli_add_list_reset_remove(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def run(argv: list[str], *replies: str) -> str:
        assert users.main(argv, ask=_answers(*replies)) == 0
        return capsys.readouterr().out

    assert run(["add", "Lead@Example.com"], "long enough pw", "long enough pw") == "Added lead@example.com\n"
    assert run(["list"]).startswith("lead@example.com\tcreated ")
    assert "signed out" in run(["reset", "lead@example.com"], "another long pw", "another long pw")
    assert run(["remove", "lead@example.com"]) == "Removed lead@example.com\n"
    assert run(["list"]) == ""


@pytest.mark.parametrize("argv, replies, message", [
    (["add", "not-an-email"], ("long enough pw", "long enough pw"), "Not an email address"),
    (["add", "a@b.co"], ("short", "short"), "at least 10 characters"),
    (["add", "a@b.co"], ("long enough pw", "different pw!!"), "don't match"),
    (["remove", "ghost@b.co"], (), "No such user"),
    (["reset", "ghost@b.co"], ("long enough pw", "long enough pw"), "No such user"),
])
def test_cli_errors(cli_db: Path, capsys: pytest.CaptureFixture[str], argv, replies, message) -> None:
    assert users.main(argv, ask=_answers(*replies)) == 1
    assert message in capsys.readouterr().err


def test_cli_refuses_duplicates(cli_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    users.main(["add", "a@b.co"], ask=_answers("long enough pw", "long enough pw"))
    assert users.main(["add", "A@B.co"], ask=_answers("long enough pw", "long enough pw")) == 1
    assert "already exists" in capsys.readouterr().err


def test_passwords_are_argon2_hashed(app: FastAPI) -> None:
    with Session(app.state.engine) as s:
        user: User = users.add_user(s, "a@b.co", "long enough pw")
        assert user.password_hash.startswith("$argon2") and "long enough pw" not in user.password_hash
