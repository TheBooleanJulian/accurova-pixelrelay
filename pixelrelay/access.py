"""Centralised authorisation: who is calling, and what may they touch.

Two kinds of caller:

* Owner: a signed-in user (Supabase Auth or dev login) with a server-side
  session in the `pr_owner` cookie. Owners act only on projects they own.
* Contributor: a project-scoped room session (one cookie per project),
  created by the Room PIN or an invitation link, then bound to a participant.

Every route resolves its caller through these helpers; nothing relies on the
UI hiding buttons. Unknown or foreign IDs return 404 so a project's contents
are not revealed to outsiders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException, Request

from . import db
from .security import OWNER_COOKIE, room_cookie_name, token_hash

OWNER_SESSION_DAYS = 14
ROOM_SESSION_DAYS = 60
TOUCH_EVERY = timedelta(minutes=5)


def not_found(what: str = "Not found") -> HTTPException:
    return HTTPException(status_code=404, detail=what)


def forbidden(what: str = "Not allowed") -> HTTPException:
    return HTTPException(status_code=403, detail=what)


# --------------------------------------------------------------------------
# Owners


@dataclass
class Owner:
    user_id: str
    email: str
    session_id: str


def _touch(conn: db.Conn, table: str, row: db.Row, days: int) -> None:
    last = db.parse_iso(row["last_seen_at"])
    if db.parse_iso(db.now_iso()) - last >= TOUCH_EVERY:
        conn.execute(
            f"UPDATE {table} SET last_seen_at = ?, expires_at = ? WHERE id = ?",
            (db.now_iso(), db.now_iso(timedelta(days=days)), row["id"]),
        )


def current_owner(request: Request, conn: db.Conn) -> Owner | None:
    token = request.cookies.get(OWNER_COOKIE)
    if not token:
        return None
    secret = request.app.state.settings.secret_key
    row = db.one(
        conn,
        "SELECT s.*, u.email FROM owner_sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ?",
        (token_hash(secret, token), db.now_iso()),
    )
    if not row:
        return None
    _touch(conn, "owner_sessions", row, OWNER_SESSION_DAYS)
    return Owner(row["user_id"], row["email"], row["id"])


def require_owner(request: Request, conn: db.Conn) -> Owner:
    owner = current_owner(request, conn)
    if not owner:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return owner


def owner_project(conn: db.Conn, owner: Owner, project_id: str) -> db.Row:
    row = db.one(conn, "SELECT * FROM projects WHERE id = ? AND owner_id = ?", (project_id, owner.user_id))
    if not row:
        raise not_found("Project not found.")
    return row


def owner_row(conn: db.Conn, project_id: str, table: str, row_id: str) -> db.Row:
    """Fetch a row of `table` that must belong to `project_id` (else 404)."""
    row = db.one(conn, f"SELECT * FROM {table} WHERE id = ? AND project_id = ?", (row_id, project_id))
    if not row:
        raise not_found()
    return row


# --------------------------------------------------------------------------
# Contributors (room sessions)


@dataclass
class Room:
    project: db.Row
    session: db.Row | None
    member: db.Row | None

    @property
    def project_id(self) -> str:
        return self.project["id"]


def room_session(request: Request, conn: db.Conn, project: db.Row) -> db.Row | None:
    token = request.cookies.get(room_cookie_name(project["slug"]))
    if not token:
        return None
    secret = request.app.state.settings.secret_key
    row = db.one(
        conn,
        "SELECT * FROM contributor_sessions WHERE token_hash = ? AND project_id = ?"
        " AND revoked_at IS NULL AND expires_at > ?",
        (token_hash(secret, token), project["id"], db.now_iso()),
    )
    if not row:
        return None
    if row["via"] == "pin":
        secret_row = db.one(conn, "SELECT version FROM room_access_secrets WHERE project_id = ?", (project["id"],))
        if not secret_row or row["pin_version"] != secret_row["version"]:
            return None  # PIN was rotated since this session was made
    _touch(conn, "contributor_sessions", row, ROOM_SESSION_DAYS)
    return row


def resolve_room(request: Request, conn: db.Conn, slug: str) -> Room:
    project = db.one(conn, "SELECT * FROM projects WHERE slug = ?", (slug,))
    if not project or project["status"] != "active":
        raise not_found("This project link is not valid.")
    session = room_session(request, conn, project)
    member = None
    if session and session["member_id"]:
        member = db.one(
            conn,
            "SELECT * FROM project_members WHERE id = ? AND project_id = ? AND removed_at IS NULL",
            (session["member_id"], project["id"]),
        )
    return Room(project, session, member)


def require_room_pass(request: Request, conn: db.Conn, slug: str) -> Room:
    room = resolve_room(request, conn, slug)
    if not room.session:
        raise HTTPException(status_code=401, detail="Enter the Room PIN to continue.")
    return room


def require_contributor(request: Request, conn: db.Conn, slug: str) -> Room:
    room = require_room_pass(request, conn, slug)
    if not room.member:
        raise HTTPException(status_code=409, detail="Choose your name first.")
    return room


def active_assignment(conn: db.Conn, photo_id: str, member_id: str) -> db.Row | None:
    return db.one(
        conn,
        "SELECT * FROM assignments WHERE photo_id = ? AND member_id = ? AND active = 1",
        (photo_id, member_id),
    )


def contributor_photo(conn: db.Conn, room: Room, photo_id: str, *, need_active: bool) -> db.Row:
    """A photo the contributor is (or, for history, was) assigned to."""
    photo = db.one(conn, "SELECT * FROM photos WHERE id = ? AND project_id = ?", (photo_id, room.project_id))
    if not photo:
        raise not_found("Photo not found.")
    ever = db.one(conn, "SELECT 1 FROM assignments WHERE photo_id = ? AND member_id = ?", (photo_id, room.member["id"]))
    if not ever:
        raise not_found("Photo not found.")  # never theirs: do not reveal it exists
    if need_active and not active_assignment(conn, photo_id, room.member["id"]):
        raise forbidden("This photo is no longer assigned to you. Your earlier edits are kept.")
    return photo


# --------------------------------------------------------------------------
# Files


def can_read_asset(conn: db.Conn, asset: db.Row, owner: Owner | None, room: Room | None) -> bool:
    if owner:
        project = db.one(conn, "SELECT owner_id FROM projects WHERE id = ?", (asset["project_id"],))
        if project and project["owner_id"] == owner.user_id:
            return True
    if not room or not room.member or room.project_id != asset["project_id"]:
        return False
    if asset["kind"] == "export":
        return False
    member_id = room.member["id"]
    if asset["member_id"]:
        return asset["member_id"] == member_id  # contributors see only their own files
    if asset["photo_id"] and asset["kind"] in ("original", "thumbnail", "preview"):
        row = db.one(
            conn, "SELECT 1 FROM assignments WHERE photo_id = ? AND member_id = ?", (asset["photo_id"], member_id)
        )
        return row is not None
    return False
