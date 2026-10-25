"""Sign-in: argon2 password hashes, server-side sessions behind a signed cookie, per-IP lockout.

The cookie holds a random token, signed with SESSION_SECRET; the `sessions` table holds
its hash. Logout deletes that row, and a password reset or removing the user deletes
all of theirs, so a copied cookie stops working at once.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from collections.abc import Callable
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import delete
from sqlmodel import Session, col, select

from api.models import User, UserSession, utcnow

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


def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def end_sessions(session: Session, user_id: int) -> None:
    """Sign a user out everywhere (password reset, removal). Caller commits."""
    session.exec(delete(UserSession).where(col(UserSession.user_id) == user_id))


class Lockout:
    """LOCKOUT_FAILURES failed sign-ins from one IP within LOCKOUT_S → that IP waits LOCKOUT_S.

    A successful sign-in only forgets the failures against that same account, so one
    valid login can't be used to reset the count while guessing someone else's."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._failures: dict[str, list[tuple[float, str]]] = {}   # ip → [(when, email)]
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def seconds_left(self, ip: str) -> float:
        with self._lock:
            left = self._locked_until.get(ip, 0.0) - self.clock()
            if left <= 0:
                self._locked_until.pop(ip, None)
            return max(0.0, left)

    def failure(self, ip: str, email: str) -> None:
        now = self.clock()
        with self._lock:
            recent = [f for f in self._failures.get(ip, []) if now - f[0] < LOCKOUT_S] + [(now, email)]
            if len(recent) >= LOCKOUT_FAILURES:
                self._locked_until[ip] = now + LOCKOUT_S
                recent = []
            self._failures[ip] = recent

    def success(self, ip: str, email: str) -> None:
        with self._lock:
            kept = [f for f in self._failures.get(ip, []) if f[1] != email]
            if kept:
                self._failures[ip] = kept
            else:
                self._failures.pop(ip, None)


class Auth:
    """Lives on app.state.auth."""

    def __init__(self, secret: str | None, enabled: bool) -> None:
        if enabled and not secret:
            raise RuntimeError("SESSION_SECRET must be set (or AUTH_DISABLED=1 for local dev, WORKER_MODE=local only)")
        self.enabled = enabled
        self._serializer = URLSafeTimedSerializer(secret or "auth-disabled", salt="callbench-session")
        self.lockout = Lockout()

    def start_session(self, response: Response, session: Session, user: User) -> None:
        """Record a new session and set its cookie. Caller commits."""
        assert user.id is not None
        now = utcnow()
        session.exec(delete(UserSession).where(col(UserSession.expires_at) <= now))   # tidy as we go
        token = secrets.token_urlsafe(32)
        session.add(UserSession(id=_token_id(token), user_id=user.id, created_at=now,
                                expires_at=now + timedelta(seconds=SESSION_MAX_AGE_S)))
        response.set_cookie(COOKIE, self._serializer.dumps(token), max_age=SESSION_MAX_AGE_S, path="/",
                            httponly=True, secure=True, samesite="lax")

    def end_session(self, request: Request, response: Response, session: Session) -> None:
        """Revoke this browser's session (server-side) and clear its cookie. Caller commits."""
        if (token := self._token(request)) and (row := session.get(UserSession, _token_id(token))):
            session.delete(row)
        response.delete_cookie(COOKIE, path="/", httponly=True, secure=True, samesite="lax")

    def _token(self, request: Request) -> str | None:
        cookie = request.cookies.get(COOKIE)
        if not cookie:
            return None
        try:
            return self._serializer.loads(cookie, max_age=SESSION_MAX_AGE_S)
        except BadSignature:   # includes expired
            return None

    def user(self, request: Request, session: Session) -> User | None:
        token = self._token(request)
        row = session.get(UserSession, _token_id(token)) if token else None
        if row is None or row.expires_at <= utcnow():
            return None
        return session.get(User, row.user_id)

    def signed_in(self, request: Request) -> bool:
        if not self.enabled:
            return True
        with Session(request.app.state.engine) as s:
            return self.user(request, s) is not None


def client_ip(request: Request) -> str:
    # Behind Caddy this is the real client only because uvicorn runs with --proxy-headers and
    # --forwarded-allow-ips set to Caddy's fixed address (Dockerfile / deploy/docker-compose.yml).
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
