"""Owner routes: projects, participants, photos, assignments, review, export.

Every route starts with `access.require_owner` + `access.owner_project`, so a
signed-in owner can only reach their own projects (others get 404).
"""

from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, field_validator

from . import access, db, jobs, ratelimit, services
from .images import ImageRejected
from .services import Conflict
from .web import conflict, get_conn, read_upload

router = APIRouter(prefix="/api/projects")


def _date(v: str | None) -> str | None:
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise ValueError("Use a date like 2026-12-31.") from None


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    shoot_date: str | None = None
    deadline: str | None = None
    participants: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("shoot_date", "deadline")
    @classmethod
    def _dates(cls, v):
        return _date(v)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    shoot_date: str | None = None
    deadline: str | None = None
    status: str | None = Field(default=None, pattern="^(active|archived)$")

    @field_validator("shoot_date", "deadline")
    @classmethod
    def _dates(cls, v):
        return _date(v)


class NamesIn(BaseModel):
    names: list[str] = Field(min_length=1, max_length=200)


class MemberPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=60)
    opted_in: bool | None = None
    capacity: int | None = Field(default=None, ge=0, le=500)
    clear_capacity: bool = False


class PhotoPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    available: bool | None = None
    priority: int | None = Field(default=None, ge=-100, le=100)


class AssignIn(BaseModel):
    member_id: str | None = None


class PlanItem(BaseModel):
    photo_id: str
    member_id: str
    source: str = Field(default="auto", pattern="^(auto|manual)$")


class PlanIn(BaseModel):
    assignments: list[PlanItem] = Field(max_length=2000)


class ReviewIn(BaseModel):
    note: str = Field(default="", max_length=500)


class FinalIn(BaseModel):
    revision_id: str | None = None


class ExportIn(BaseModel):
    format: str = Field(default="jpeg", pattern="^(jpeg|png)$")


def _ctx(request: Request, conn: sqlite3.Connection, project_id: str):
    owner = access.require_owner(request, conn)
    return owner, access.owner_project(conn, owner, project_id)


def _project_dict(request: Request, conn: sqlite3.Connection, p: sqlite3.Row) -> dict:
    settings = request.app.state.settings
    pin, version = services.room_pin(conn, settings, p["id"])
    decision = ratelimit.check(conn, p["id"], "-")
    return {
        "id": p["id"],
        "slug": p["slug"],
        "name": p["name"],
        "description": p["description"],
        "shoot_date": p["shoot_date"],
        "deadline": p["deadline"],
        "status": p["status"],
        "created_at": p["created_at"],
        "pin": pin,
        "pin_version": version,
        "room_url": f"{settings.base_url}/r/{p['slug']}",
        "suspicious_pin_activity": decision.suspicious,
        "progress": services.progress(conn, p["id"]),
    }


# --------------------------------------------------------------------------
# Projects


@router.get("")
def list_projects(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    owner = access.require_owner(request, conn)
    rows = db.all_rows(conn, "SELECT * FROM projects WHERE owner_id = ? ORDER BY created_at DESC", (owner.user_id,))
    return {
        "projects": [
            {
                "id": p["id"],
                "name": p["name"],
                "status": p["status"],
                "shoot_date": p["shoot_date"],
                "deadline": p["deadline"],
                "progress": services.progress(conn, p["id"]),
                "participants": db.one(
                    conn,
                    "SELECT COUNT(*) AS n FROM project_members WHERE project_id = ? AND removed_at IS NULL",
                    (p["id"],),
                )["n"],
            }
            for p in rows
        ]
    }


@router.post("", status_code=201)
def create_project(body: ProjectIn, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    owner = access.require_owner(request, conn)
    try:
        project_id = services.create_project(
            conn,
            owner.user_id,
            body.name.strip(),
            body.description.strip(),
            body.shoot_date,
            body.deadline,
            body.participants,
        )
    except Conflict as exc:
        raise conflict(str(exc)) from None
    return _project_dict(request, conn, access.owner_project(conn, owner, project_id))


@router.get("/{project_id}")
def get_project(project_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    _, p = _ctx(request, conn, project_id)
    return _project_dict(request, conn, p)


@router.patch("/{project_id}")
def patch_project(project_id: str, body: ProjectPatch, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    owner, p = _ctx(request, conn, project_id)
    values = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if "name" in values and values["name"]:
        values["name"] = values["name"].strip()
    if values:
        values["updated_at"] = db.now_iso()
        with db.transaction(conn):
            db.update(conn, "projects", p["id"], values)
            db.log_event(conn, p["id"], "project_updated", "owner", owner.user_id)
    return _project_dict(request, conn, access.owner_project(conn, owner, project_id))


@router.post("/{project_id}/pin/rotate")
def rotate_pin(project_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    owner, p = _ctx(request, conn, project_id)
    pin = services.rotate_pin(conn, request.app.state.settings, p["id"], owner.user_id)
    return {"pin": pin}


@router.get("/{project_id}/activity")
def activity(project_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    _, p = _ctx(request, conn, project_id)
    rows = db.all_rows(
        conn,
        "SELECT e.*, m.display_name, ph.title FROM activity_events e"
        " LEFT JOIN project_members m ON m.id = e.actor_id AND e.actor_type = 'contributor'"
        " LEFT JOIN photos ph ON ph.id = e.photo_id"
        " WHERE e.project_id = ? ORDER BY e.created_at DESC, e.rowid DESC LIMIT 200",
        (p["id"],),
    )
    return {
        "events": [
            {
                "kind": r["kind"],
                "actor_type": r["actor_type"],
                "actor_name": r["display_name"],
                "photo_title": r["title"],
                "photo_id": r["photo_id"],
                "data": db.loads(r["data_json"], {}),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }


# --------------------------------------------------------------------------
# Participants


def _member_rows(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    loads = services.loads(conn, project_id)
    sessions = {
        r["member_id"]: r["n"]
        for r in db.all_rows(
            conn,
            "SELECT member_id, COUNT(*) AS n FROM contributor_sessions WHERE project_id = ?"
            " AND revoked_at IS NULL AND expires_at > ? AND member_id IS NOT NULL GROUP BY member_id",
            (project_id, db.now_iso()),
        )
    }
    submitted = {
        r["member_id"]: r["n"]
        for r in db.all_rows(
            conn,
            "SELECT member_id, COUNT(DISTINCT photo_id) AS n FROM revisions WHERE project_id = ? GROUP BY member_id",
            (project_id,),
        )
    }
    return [
        {
            "id": m["id"],
            "display_name": m["display_name"],
            "opted_in": bool(m["opted_in"]),
            "capacity": m["capacity"],
            "invite_only": bool(m["invite_only"]),
            "assigned": loads.get(m["id"], 0),
            "submitted_photos": submitted.get(m["id"], 0),
            "devices": sessions.get(m["id"], 0),
        }
        for m in services._members(conn, project_id)
    ]


@router.get("/{project_id}/members")
def list_members(project_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    _, p = _ctx(request, conn, project_id)
    return {"members": _member_rows(conn, p["id"])}


@router.post("/{project_id}/members", status_code=201)
def add_members(project_id: str, body: NamesIn, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    owner, p = _ctx(request, conn, project_id)
    try:
        services.add_members(conn, p["id"], body.names, owner.user_id)
    except Conflict as exc:
        raise conflict(str(exc)) from None
    return {"members": _member_rows(conn, p["id"])}


@router.patch("/{project_id}/members/{member_id}")
def patch_member(
    project_id: str, member_id: str, body: MemberPatch, request: Request, conn: sqlite3.Connection = Depends(get_conn)
):
    _, p = _ctx(request, conn, project_id)
    member = access.owner_row(conn, p["id"], "project_members", member_id)
    if member["removed_at"]:
        raise access.not_found()
    fields = body.model_dump(exclude_unset=True)
    if fields.pop("clear_capacity", False):
        fields["capacity"] = None
    try:
        services.update_member(conn, member, fields)
    except Conflict as exc:
        raise conflict(str(exc)) from None
    return {"members": _member_rows(conn, p["id"])}


@router.delete("/{project_id}/members/{member_id}")
def remove_member(project_id: str, member_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    owner, p = _ctx(request, conn, project_id)
    member = access.owner_row(conn, p["id"], "project_members", member_id)
    if not member["removed_at"]:
        services.remove_member(conn, member, owner.user_id)
    return {"members": _member_rows(conn, p["id"])}


@router.post("/{project_id}/members/{member_id}/invite")
def invite(project_id: str, member_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    _, p = _ctx(request, conn, project_id)
    member = access.owner_row(conn, p["id"], "project_members", member_id)
    if member["removed_at"]:
        raise access.not_found()
    token = services.create_invite(conn, request.app.state.settings, member)
    # The token travels in the URL fragment, which browsers never send to servers or logs.
    return {"url": f"{request.app.state.settings.base_url}/invite#{token}", "expires_in_days": services.INVITE_DAYS}


@router.get("/{project_id}/members/{member_id}/sessions")
def member_sessions(project_id: str, member_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    _, p = _ctx(request, conn, project_id)
    access.owner_row(conn, p["id"], "project_members", member_id)
    rows = db.all_rows(
        conn,
        "SELECT id, via, device_label, created_at, last_seen_at FROM contributor_sessions"
        " WHERE project_id = ? AND member_id = ? AND revoked_at IS NULL AND expires_at > ? ORDER BY last_seen_at DESC",
        (p["id"], member_id, db.now_iso()),
    )
    return {"sessions": [dict(r) for r in rows]}


@router.post("/{project_id}/sessions/{session_id}/revoke")
def revoke_session(project_id: str, session_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    owner, p = _ctx(request, conn, project_id)
    access.owner_row(conn, p["id"], "contributor_sessions", session_id)
    with db.transaction(conn):
        conn.execute("UPDATE contributor_sessions SET revoked_at = ? WHERE id = ?", (db.now_iso(), session_id))
        db.log_event(conn, p["id"], "device_revoked", "owner", owner.user_id)
    return {"ok": True}


@router.post("/{project_id}/members/{member_id}/sessions/revoke")
def revoke_member_sessions(
    project_id: str, member_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)
):
    owner, p = _ctx(request, conn, project_id)
    access.owner_row(conn, p["id"], "project_members", member_id)
    with db.transaction(conn):
        conn.execute(
            "UPDATE contributor_sessions SET revoked_at = ? WHERE project_id = ? AND member_id = ? AND revoked_at IS NULL",
            (db.now_iso(), p["id"], member_id),
        )
        db.log_event(conn, p["id"], "devices_revoked", "owner", owner.user_id)
    return {"ok": True}


# --------------------------------------------------------------------------
# Photos


@router.post("/{project_id}/photos", status_code=201)
def upload_original(
    project_id: str, request: Request, file: UploadFile = File(...), conn: sqlite3.Connection = Depends(get_conn)
):
    owner, p = _ctx(request, conn, project_id)
    data = read_upload(request, file)
    try:
        photo_id = services.add_original(
            conn,
            request.app.state.storage,
            request.app.state.settings,
            p["id"],
            file.filename or "photo",
            data,
            owner.user_id,
        )
    except ImageRejected as exc:
        raise HTTPException(415, str(exc)) from None
    return {"photo_id": photo_id}


@router.get("/{project_id}/photos")
def list_photos(project_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    _, p = _ctx(request, conn, project_id)
    return {"photos": services.list_photos(conn, p["id"])}


@router.patch("/{project_id}/photos/{photo_id}")
def patch_photo(
    project_id: str, photo_id: str, body: PhotoPatch, request: Request, conn: sqlite3.Connection = Depends(get_conn)
):
    _, p = _ctx(request, conn, project_id)
    access.owner_row(conn, p["id"], "photos", photo_id)
    values = body.model_dump(exclude_unset=True)
    if "available" in values:
        values["available"] = 1 if values["available"] else 0
    if values:
        values["updated_at"] = db.now_iso()
        db.update(conn, "photos", photo_id, values)
    return {"ok": True}


@router.get("/{project_id}/photos/{photo_id}")
def photo_detail(project_id: str, photo_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    _, p = _ctx(request, conn, project_id)
    photo = access.owner_row(conn, p["id"], "photos", photo_id)
    summary = next(x for x in services.list_photos(conn, p["id"]) if x["id"] == photo_id)
    history = db.all_rows(
        conn,
        "SELECT a.*, m.display_name FROM assignments a JOIN project_members m ON m.id = a.member_id"
        " WHERE a.photo_id = ? ORDER BY a.created_at",
        (photo_id,),
    )
    revisions = db.all_rows(
        conn,
        "SELECT r.*, m.display_name FROM revisions r JOIN project_members m ON m.id = r.member_id"
        " WHERE r.photo_id = ? ORDER BY r.number DESC",
        (photo_id,),
    )
    decisions = db.all_rows(conn, "SELECT * FROM approvals WHERE photo_id = ? ORDER BY created_at DESC", (photo_id,))
    return {
        "photo": summary,
        "assignments": [
            {
                "member_name": a["display_name"],
                "member_id": a["member_id"],
                "status": a["status"],
                "active": bool(a["active"]),
                "source": a["source"],
                "created_at": a["created_at"],
                "ended_at": a["ended_at"],
                "ended_reason": a["ended_reason"],
                "original_downloaded_at": a["original_downloaded_at"],
            }
            for a in history
        ],
        "revisions": [services.revision_dict(r, r["display_name"]) for r in revisions],
        "decisions": [
            {
                "decision": d["decision"],
                "revision_id": d["revision_id"],
                "note": d["note"],
                "created_at": d["created_at"],
            }
            for d in decisions
        ],
        "final_revision_id": photo["final_revision_id"],
    }


# --------------------------------------------------------------------------
# Assignments


def _plan_view(conn: sqlite3.Connection, project_id: str, plan) -> dict:
    names = {m["id"]: m["display_name"] for m in services._members(conn, project_id)}
    titles = {
        r["id"]: r["title"]
        for r in db.all_rows(conn, "SELECT id, title FROM photos WHERE project_id = ?", (project_id,))
    }
    d = plan.to_dict()
    for a in d["assignments"]:
        a["member_name"] = names.get(a["member_id"])
        a["photo_title"] = titles.get(a["photo_id"])
    d["unassigned"] = [{"photo_id": pid, "photo_title": titles.get(pid)} for pid in d["unassigned"]]
    d["load"] = [{"member_id": k, "member_name": names.get(k), "count": v} for k, v in d["load"].items()]
    return d


@router.post("/{project_id}/assignments/preview")
def preview_assignments(project_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    _, p = _ctx(request, conn, project_id)
    return _plan_view(conn, p["id"], services.preview_distribution(conn, p["id"]))


@router.post("/{project_id}/assignments/publish")
def publish_assignments(project_id: str, body: PlanIn, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    owner, p = _ctx(request, conn, project_id)
    for item in body.assignments:
        access.owner_row(conn, p["id"], "photos", item.photo_id)
        access.owner_row(conn, p["id"], "project_members", item.member_id)
    try:
        count = services.publish_plan(conn, p["id"], [i.model_dump() for i in body.assignments], owner.user_id)
    except Conflict as exc:
        raise conflict(str(exc)) from None
    return {"assigned": count}


@router.put("/{project_id}/photos/{photo_id}/assignment")
def set_assignment(
    project_id: str, photo_id: str, body: AssignIn, request: Request, conn: sqlite3.Connection = Depends(get_conn)
):
    owner, p = _ctx(request, conn, project_id)
    access.owner_row(conn, p["id"], "photos", photo_id)
    try:
        with db.transaction(conn):
            result = services.assign(conn, p["id"], photo_id, body.member_id, owner.user_id, "manual")
    except Conflict as exc:
        raise conflict(str(exc)) from None
    return result


# --------------------------------------------------------------------------
# Review and export


def _revision(conn: sqlite3.Connection, project_id: str, revision_id: str) -> sqlite3.Row:
    return access.owner_row(conn, project_id, "revisions", revision_id)


@router.post("/{project_id}/revisions/{revision_id}/approve")
def approve(
    project_id: str, revision_id: str, body: ReviewIn, request: Request, conn: sqlite3.Connection = Depends(get_conn)
):
    owner, p = _ctx(request, conn, project_id)
    services.review(conn, _revision(conn, p["id"], revision_id), owner.user_id, "approved", body.note)
    return {"ok": True}


@router.post("/{project_id}/revisions/{revision_id}/request-changes")
def request_changes(
    project_id: str, revision_id: str, body: ReviewIn, request: Request, conn: sqlite3.Connection = Depends(get_conn)
):
    owner, p = _ctx(request, conn, project_id)
    try:
        services.review(conn, _revision(conn, p["id"], revision_id), owner.user_id, "changes_requested", body.note)
    except Conflict as exc:
        raise conflict(str(exc)) from None
    return {"ok": True}


@router.put("/{project_id}/photos/{photo_id}/final")
def set_final(
    project_id: str, photo_id: str, body: FinalIn, request: Request, conn: sqlite3.Connection = Depends(get_conn)
):
    owner, p = _ctx(request, conn, project_id)
    photo = access.owner_row(conn, p["id"], "photos", photo_id)
    revision = None
    if body.revision_id:
        revision = _revision(conn, p["id"], body.revision_id)
        if revision["photo_id"] != photo_id:
            raise access.not_found()
    try:
        services.select_final(conn, photo, revision, owner.user_id)
    except Conflict as exc:
        raise conflict(str(exc)) from None
    return {"ok": True}


@router.post("/{project_id}/exports", status_code=202)
def start_export(project_id: str, body: ExportIn, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    owner, p = _ctx(request, conn, project_id)
    with db.transaction(conn):
        job_id = jobs.enqueue(
            conn,
            "project.export",
            {"project_id": p["id"], "format": body.format, "owner_id": owner.user_id},
            p["id"],
            max_attempts=2,
        )
    return {"job_id": job_id}


@router.get("/{project_id}/exports")
def list_exports(project_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    _, p = _ctx(request, conn, project_id)
    rows = db.all_rows(
        conn,
        "SELECT * FROM jobs WHERE project_id = ? AND kind = 'project.export' ORDER BY created_at DESC LIMIT 10",
        (p["id"],),
    )
    return {
        "exports": [
            {
                "job_id": r["id"],
                "status": r["status"],
                "error": r["error"],
                "created_at": r["created_at"],
                "format": db.loads(r["payload_json"], {}).get("format"),
                "asset_id": db.loads(r["result_json"], {}).get("asset_id") if r["result_json"] else None,
                "count": db.loads(r["result_json"], {}).get("count") if r["result_json"] else None,
            }
            for r in rows
        ]
    }
