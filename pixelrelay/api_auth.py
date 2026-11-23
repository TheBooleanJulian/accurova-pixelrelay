"""Owner sign-in routes."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import access, auth, db
from .security import OWNER_COOKIE
from .web import clear_cookie, get_conn, set_cookie

router = APIRouter(prefix="/api/auth")
log = logging.getLogger("pixelrelay.auth")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")


class EmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class TokenIn(BaseModel):
    access_token: str = Field(min_length=20, max_length=4096)


def _email(raw: str) -> str:
    email = raw.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Enter a valid email address.")
    return email


@router.get("/config")
def config(request: Request):
    s = request.app.state.settings
    return {"mode": s.auth_mode, "restricted": bool(s.owner_emails)}


@router.get("/me")
def me(request: Request, conn: db.Conn = Depends(get_conn)):
    owner = access.require_owner(request, conn)
    return {"user": {"id": owner.user_id, "email": owner.email}}


def _start_session(
    request: Request, response: Response, conn: db.Conn, provider: str, subject: str, email: str
) -> dict:
    settings = request.app.state.settings
    if not auth.owner_allowed(settings, email):
        raise HTTPException(403, "This email is not set up as a PixelRelay owner.")
    try:
        with db.transaction(conn):
            user_id = auth.upsert_user(conn, provider, subject, email)
            token = auth.create_owner_session(conn, settings, user_id)
    except auth.AuthError as exc:
        raise HTTPException(409, str(exc)) from None
    set_cookie(request, response, OWNER_COOKIE, token, access.OWNER_SESSION_DAYS)
    return {"user": {"id": user_id, "email": email}}


@router.post("/dev-login")
def dev_login(body: EmailIn, request: Request, response: Response, conn: db.Conn = Depends(get_conn)):
    settings = request.app.state.settings
    if settings.auth_mode != "dev" or settings.is_production:
        raise HTTPException(404, "Not found")
    email = _email(body.email)
    return _start_session(request, response, conn, "dev", email, email)


@router.post("/magic-link")
def magic_link(body: EmailIn, request: Request):
    settings = request.app.state.settings
    if settings.auth_mode != "supabase":
        raise HTTPException(404, "Not found")
    email = _email(body.email)
    if auth.owner_allowed(settings, email):
        try:
            auth.send_magic_link(settings, email)
        except auth.AuthError as exc:
            raise HTTPException(502, str(exc)) from None
    # Same answer either way, so the endpoint does not reveal who is an owner.
    return {"sent": True}


@router.post("/supabase-session")
def supabase_session(body: TokenIn, request: Request, response: Response, conn: db.Conn = Depends(get_conn)):
    settings = request.app.state.settings
    if settings.auth_mode != "supabase":
        raise HTTPException(404, "Not found")
    try:
        subject, email = auth.verify_supabase_token(settings, body.access_token)
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc)) from None
    return _start_session(request, response, conn, "supabase", subject, email)


@router.post("/logout")
def logout(request: Request, response: Response, conn: db.Conn = Depends(get_conn)):
    owner = access.current_owner(request, conn)
    if owner:
        conn.execute("UPDATE owner_sessions SET revoked_at = ? WHERE id = ?", (db.now_iso(), owner.session_id))
    clear_cookie(request, response, OWNER_COOKIE)
    return {"ok": True}
