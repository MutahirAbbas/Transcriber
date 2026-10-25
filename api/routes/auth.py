from __future__ import annotations

import math
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from api import auth
from api.db import get_session
from api.models import User, utcnow

router = APIRouter(prefix="/auth")

SessionDep = Annotated[Session, Depends(get_session)]


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=1024)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, session: SessionDep) -> dict[str, Any]:
    a: auth.Auth = request.app.state.auth
    if not a.enabled:
        return {"email": None, "auth_disabled": True}
    ip = auth.client_ip(request)
    if wait := a.lockout.seconds_left(ip):
        raise HTTPException(429, f"Too many failed sign-ins. Try again in {math.ceil(wait / 60)} min.")
    user = auth.find_user(session, body.email)
    if not auth.verify_password(user.password_hash if user else None, body.password):
        a.lockout.failure(ip)
        raise HTTPException(401, "Email or password is incorrect")
    assert user is not None
    a.lockout.clear(ip)
    if auth.needs_rehash(user.password_hash):
        user.password_hash = auth.hash_password(body.password)
    user.last_login = utcnow()
    session.add(user)
    session.commit()
    a.set_session(response, user)
    return {"email": user.email, "auth_disabled": False}


@router.post("/logout", status_code=204)
def logout(request: Request) -> Response:
    response = Response(status_code=204)
    request.app.state.auth.clear_session(response)
    return response


@router.get("/me")
def me(request: Request, user: Annotated[User | None, Depends(auth.require_user)]) -> dict[str, Any]:
    if not request.app.state.auth.enabled:
        return {"email": None, "auth_disabled": True}
    assert user is not None
    return {"email": user.email, "auth_disabled": False}
