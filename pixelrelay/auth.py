"""Owner sign-in providers.

* `supabase`: passwordless magic link through Supabase Auth. The browser
  receives the Supabase access token in the URL fragment after clicking the
  emailed link, posts it once to /api/auth/supabase-session, and the server
  verifies it against Supabase (`GET /auth/v1/user`) before creating its own
  server-side session. Supabase tokens are never stored.
* `dev`: email-only sign-in for local development. Refused in production.

Both end in the same place: a `users` row and an `owner_sessions` row whose
random token lives only in an HttpOnly cookie.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from . import db
from .access import OWNER_SESSION_DAYS
from .config import Settings
from .security import random_token, token_hash


class AuthError(Exception):
    pass


def owner_allowed(settings: Settings, email: str) -> bool:
    return not settings.owner_emails or email.lower() in settings.owner_emails


def upsert_user(conn: db.Conn, provider: str, subject: str, email: str) -> str:
    row = db.one(conn, "SELECT id FROM users WHERE auth_provider = ? AND auth_subject = ?", (provider, subject))
    if row:
        return row["id"]
    row = db.one(conn, "SELECT id, auth_provider FROM users WHERE lower(email) = lower(?)", (email,))
    if row:
        # Same email via another provider (e.g. dev then supabase): refuse to merge silently.
        raise AuthError("This email is already linked to a different sign-in method.")
    user_id = db.new_id()
    db.insert(
        conn,
        "users",
        {
            "id": user_id,
            "email": email.lower(),
            "auth_provider": provider,
            "auth_subject": subject,
            "display_name": None,
            "created_at": db.now_iso(),
        },
    )
    return user_id


def create_owner_session(conn: db.Conn, settings: Settings, user_id: str) -> str:
    token = random_token()
    now = db.now_iso()
    db.insert(
        conn,
        "owner_sessions",
        {
            "id": db.new_id(),
            "user_id": user_id,
            "token_hash": token_hash(settings.secret_key, token),
            "created_at": now,
            "last_seen_at": now,
            "expires_at": db.now_iso(timedelta(days=OWNER_SESSION_DAYS)),
            "revoked_at": None,
        },
    )
    return token


def _supabase_request(
    settings: Settings,
    method: str,
    path: str,
    body: dict | None = None,
    bearer: str | None = None,
    query: dict | None = None,
) -> dict:
    url = f"{settings.supabase_url}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    headers = {"apikey": settings.supabase_anon_key, "Content-Type": "application/json"}
    headers["Authorization"] = f"Bearer {bearer or settings.supabase_anon_key}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        # Never echo Supabase's response body: it can include token details.
        raise AuthError(f"Sign-in service refused the request ({exc.code}).") from None
    except (urllib.error.URLError, TimeoutError):
        raise AuthError("Sign-in service is unreachable. Try again shortly.") from None
    return json.loads(raw) if raw else {}


def send_magic_link(settings: Settings, email: str) -> None:
    _supabase_request(
        settings,
        "POST",
        "/auth/v1/otp",
        {"email": email, "create_user": True},
        query={"redirect_to": f"{settings.base_url}/auth/callback"},
    )


def verify_supabase_token(settings: Settings, access_token: str) -> tuple[str, str]:
    """Return (supabase user id, email) for a valid access token."""
    user = _supabase_request(settings, "GET", "/auth/v1/user", bearer=access_token)
    subject, email = user.get("id"), (user.get("email") or "").lower()
    if not subject or not email:
        raise AuthError("Sign-in could not be confirmed.")
    return subject, email
