"""Sign-in: argon2 password hashes, a signed session cookie, per-IP lockout.

The cookie carries the user id and a fingerprint of their password hash, so
resetting a password signs out every existing session, and removing a user
does too (the id no longer resolves).
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlmodel import Session, select

from api.models import User

COOKIE = "callbench_session"
SESSION_MAX_AGE_S = 14 * 24 * 3600
LOCKOUT_FAILURES = 10
LOCKOUT_S = 15 * 60
PASSWORD_MIN = 10
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

_hasher = PasswordHasher()
# verified against when the email is unknown, so a miss takes as long as a wrong password
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def valid_email(email: str) -> bool:
    return bool(_EMAIL.fullmatch(email))


def hash_password(password: str) -> str:
    if len(password) < PASSWORD_MIN:
        raise ValueError(f"Password must be at least {PASSWORD_MIN} characters")
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def _fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


class Lockout:
    """LOCKOUT_FAILURES failed sign-ins from one IP within LOCKOUT_S → that IP waits LOCKOUT_S."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def seconds_left(self, ip: str) -> float:
        with self._lock:
            left = self._locked_until.get(ip, 0.0) - self.clock()
            if left <= 0:
                self._locked_until.pop(ip, None)
            return max(0.0, left)

    def failure(self, ip: str) -> None:
        now = self.clock()
        with self._lock:
            recent = [t for t in self._failures.get(ip, []) if now - t < LOCKOUT_S] + [now]
            if len(recent) >= LOCKOUT_FAILURES:
                self._locked_until[ip] = now + LOCKOUT_S
                recent = []
            self._failures[ip] = recent

    def clear(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)


class Auth:
    """Lives on app.state.auth."""

    def __init__(self, secret: str | None, enabled: bool) -> None:
        if enabled and not secret:
            raise RuntimeError("SESSION_SECRET must be set (or AUTH_DISABLED=1 for local dev, WORKER_MODE=local only)")
        self.enabled = enabled
        self._serializer = URLSafeTimedSerializer(secret or "auth-disabled", salt="callbench-session")
        self.lockout = Lockout()

    def set_session(self, response: Response, user: User) -> None:
        assert user.id is not None
        token = self._serializer.dumps({"u": user.id, "f": _fingerprint(user.password_hash)})
        response.set_cookie(COOKIE, token, max_age=SESSION_MAX_AGE_S, path="/",
                            httponly=True, secure=True, samesite="lax")

    def clear_session(self, response: Response) -> None:
        response.delete_cookie(COOKIE, path="/", httponly=True, secure=True, samesite="lax")

    def user(self, request: Request, session: Session) -> User | None:
        token = request.cookies.get(COOKIE)
        if not token:
            return None
        try:
            data: dict[str, Any] = self._serializer.loads(token, max_age=SESSION_MAX_AGE_S)
        except BadSignature:   # includes expired
            return None
        user = session.get(User, data.get("u"))
        if user is None or data.get("f") != _fingerprint(user.password_hash):
            return None
        return user

    def signed_in(self, request: Request) -> bool:
        if not self.enabled:
            return True
        with Session(request.app.state.engine) as s:
            return self.user(request, s) is not None


def client_ip(request: Request) -> str:
    # behind Caddy, uvicorn --proxy-headers puts the real client here
    return request.client.host if request.client else "unknown"


def require_user(request: Request) -> User | None:
    """Dependency for every non-worker /api route. None when auth is off (local dev)."""
    auth: Auth = request.app.state.auth
    if not auth.enabled:
        return None
    with Session(request.app.state.engine) as s:
        user = auth.user(request, s)
    if user is None:
        raise HTTPException(401, "Sign in required")
    return user


def find_user(session: Session, email: str) -> User | None:
    return session.exec(select(User).where(User.email == normalize_email(email))).first()
