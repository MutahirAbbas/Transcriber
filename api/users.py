"""Manage sign-in accounts. There's no sign-up page.

    python -m api.users add <email>       # prompts for the password
    python -m api.users list
    python -m api.users remove <email>
    python -m api.users reset <email>     # prompts for a new password; signs out their sessions

On the server: docker compose -p call-analyzer exec app python -m api.users add <email>
"""
from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable

from sqlmodel import Session, col, select

from api.auth import PASSWORD_MIN, end_sessions, find_user, hash_password, normalize_email, valid_email
from api.config import load_settings
from api.db import make_engine
from api.models import User


class UserError(Exception):
    pass


def add_user(session: Session, email: str, password: str) -> User:
    email = normalize_email(email)
    if not valid_email(email):
        raise UserError(f"Not an email address: {email}")
    if find_user(session, email):
        raise UserError(f"{email} already exists (use reset to change the password)")
    try:
        user = User(email=email, password_hash=hash_password(password))
    except ValueError as e:
        raise UserError(str(e)) from None
    session.add(user)
    session.commit()
    return user


def list_users(session: Session) -> list[User]:
    return list(session.exec(select(User).order_by(col(User.email))).all())


def remove_user(session: Session, email: str) -> None:
    user = find_user(session, email)
    if user is None:
        raise UserError(f"No such user: {normalize_email(email)}")
    assert user.id is not None
    end_sessions(session, user.id)
    session.delete(user)
    session.commit()


def reset_password(session: Session, email: str, password: str) -> None:
    """New password, and every existing session of theirs is signed out."""
    user = find_user(session, email)
    if user is None:
        raise UserError(f"No such user: {normalize_email(email)}")
    try:
        user.password_hash = hash_password(password)
    except ValueError as e:
        raise UserError(str(e)) from None
    assert user.id is not None
    end_sessions(session, user.id)
    session.add(user)
    session.commit()


def prompt_password(ask: Callable[[str], str] = getpass.getpass) -> str:
    password = ask(f"Password (at least {PASSWORD_MIN} characters): ")
    if ask("Again: ") != password:
        raise UserError("Passwords don't match")
    return password


def main(argv: list[str] | None = None, ask: Callable[[str], str] = getpass.getpass) -> int:
    ap = argparse.ArgumentParser(prog="python -m api.users", description="Manage sign-in accounts.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("add", "remove", "reset"):
        sub.add_parser(cmd).add_argument("email")
    sub.add_parser("list")
    args = ap.parse_args(argv)

    engine = make_engine(load_settings().db_path)
    try:
        with Session(engine) as s:
            if args.cmd == "add":
                user = add_user(s, args.email, prompt_password(ask))
                print(f"Added {user.email}")
            elif args.cmd == "list":
                for u in list_users(s):
                    last = u.last_login.strftime("%Y-%m-%d %H:%M UTC") if u.last_login else "never"
                    print(f"{u.email}\tcreated {u.created_at:%Y-%m-%d}\tlast login {last}")
            elif args.cmd == "remove":
                remove_user(s, args.email)
                print(f"Removed {normalize_email(args.email)}")
            elif args.cmd == "reset":
                reset_password(s, args.email, prompt_password(ask))
                print(f"Password reset for {normalize_email(args.email)}; their sessions are signed out")
    except UserError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
