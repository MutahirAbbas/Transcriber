from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from api.app import create_app
from api.config import Settings, load_settings
from api.models import User


def _app(settings: Settings, user: str | None = "mutahir", password: str | None = "a long enough pw", **kw):
    s = dataclasses.replace(settings, auth_disabled=False, login_user=user, login_pass=password, **kw)
    return create_app(s, start_worker=False)


def _login(c: TestClient, user: str, password: str):
    return c.post("/api/auth/login", json={"email": user, "password": password})


def test_env_account_is_created_and_can_sign_in_with_a_username(settings: Settings) -> None:
    with TestClient(_app(settings), base_url="https://testserver") as c:
        assert _login(c, "Mutahir", "a long enough pw").status_code == 200
        assert c.get("/api/auth/me").json()["email"] == "mutahir"


def test_restart_with_the_same_env_changes_nothing(settings: Settings) -> None:
    app = _app(settings)
    with Session(app.state.engine) as s:
        before = s.exec(select(User)).one().password_hash
    app = _app(settings)
    with Session(app.state.engine) as s:
        assert [u.password_hash for u in s.exec(select(User)).all()] == [before]


def test_new_env_password_replaces_the_old_one_and_signs_out(settings: Settings) -> None:
    with TestClient(_app(settings), base_url="https://testserver") as old:
        _login(old, "mutahir", "a long enough pw")
        cookie = old.cookies.get("callbench_session")
    with TestClient(_app(settings, password="a different long pw"), base_url="https://testserver") as c:
        assert _login(c, "mutahir", "a long enough pw").status_code == 401
        assert _login(c, "mutahir", "a different long pw").status_code == 200
        c.cookies.clear()
        c.cookies.set("callbench_session", cookie)
        assert c.get("/api/calls").status_code == 401        # the old session is gone


@pytest.mark.parametrize("user, password, message", [
    ("mutahir", None, "both"),
    (None, "a long enough pw", "both"),
    ("mutahir", "short", "at least 10"),
    ("two words", "a long enough pw", "single word"),
])
def test_bad_env_login_stops_startup(settings: Settings, user, password, message) -> None:
    with pytest.raises(RuntimeError, match=message):
        _app(settings, user=user, password=password)


def test_env_login_is_ignored_when_sign_in_is_off(settings: Settings) -> None:
    app = create_app(dataclasses.replace(settings, login_user="mutahir", login_pass="short"), start_worker=False)
    with Session(app.state.engine) as s:   # local dev, AUTH_DISABLED=1: no account, no error
        assert s.exec(select(User)).all() == []


def test_names_come_from_login_user_and_login_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USER", "macos-account")          # always set on a Mac; must not be used
    monkeypatch.setenv("LOGIN_USER", " mutahir ")
    monkeypatch.setenv("LOGIN_PASS", "a long enough pw")
    s = load_settings()
    assert (s.login_user, s.login_pass) == ("mutahir", "a long enough pw")
