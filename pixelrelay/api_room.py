"""Contributor routes, all under /api/rooms/{slug}.

Access: Room PIN (or invitation) -> room session cookie -> pick your name.
Contributors see only photos assigned to them and only their own edits.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from . import access, db, ratelimit, services
from .images import ImageRejected
from .security import device_label, ip_hash, normalise_pin, pin_matches, room_cookie_name
from .services import Conflict
from .web import clear_cookie, client_ip, conflict, file_response, get_conn, read_upload, set_cookie

router = APIRouter(prefix="/api/rooms/{slug}")
invite_router = APIRouter(prefix="/api/invites")
log = logging.getLogger("pixelrelay.room")


class PinIn(BaseModel):
    pin: str = Field(min_length=1, max_length=20)


class IdentifyIn(BaseModel):
    member_id: str = Field(min_length=1, max_length=64)


class AvailabilityIn(BaseModel):
    opted_in: bool
    capacity: int | None = Field(default=None, ge=0, le=500)


class SmartIn(BaseModel):
    sensitivity: str = Field(pattern="^(low|medium|high)$")


class AlignIn(BaseModel):
    auto: bool = False
    rotate: int = Field(default=0, ge=0, le=3)
    scale: float = Field(default=1.0, ge=0.2, le=5.0)
    dx: float = Field(default=0.0, ge=-1.0, le=1.0)
    dy: float = Field(default=0.0, ge=-1.0, le=1.0)


class SubmitIn(BaseModel):
    mask_id: str
    note: str = Field(default="", max_length=500)


class InviteIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


STATE_LABELS = {
    "assigned": "To do",
    "editing": "Editing",
    "submitted": "Submitted",
    "needs_changes": "Needs changes",
    "approved": "Approved",
    "final": "Final version",
}


def _state(request: Request, conn: db.Conn, room: access.Room) -> dict:
    if not room.session:
        return {"state": "need_pin"}
    base = {
        "project": {
            "name": room.project["name"],
            "deadline": room.project["deadline"],
            "shoot_date": room.project["shoot_date"],
            "description": room.project["description"],
        },
        "via": room.session["via"],
    }
    if not room.member:
        return {"state": "need_name", **base}
    m = room.member
    return {
        "state": "ready",
        **base,
        "me": {
            "id": m["id"],
            "name": m["display_name"],
            "opted_in": bool(m["opted_in"]),
            "capacity": m["capacity"],
        },
        "progress": services.progress(conn, room.project_id),
    }


@router.get("")
def room_state(slug: str, request: Request, conn: db.Conn = Depends(get_conn)):
    return _state(request, conn, access.resolve_room(request, conn, slug))


@router.post("/join")
def join(slug: str, body: PinIn, request: Request, response: Response, conn: db.Conn = Depends(get_conn)):
    settings = request.app.state.settings
    room = access.resolve_room(request, conn, slug)
    project = room.project
    who = ip_hash(settings.secret_key, client_ip(request))
    decision = ratelimit.check(conn, project["id"], who)
    if not decision.allowed:
        raise HTTPException(
            429,
            f"Too many tries. Wait {decision.retry_after} seconds and try again.",
            headers={"Retry-After": str(decision.retry_after)},
        )
    pin = normalise_pin(body.pin)
    secret = db.one(conn, "SELECT seed, version FROM room_access_secrets WHERE project_id = ?", (project["id"],))
    ok = pin is not None and pin_matches(settings.secret_key, project["id"], secret["seed"], pin)
    with db.transaction(conn):
        ratelimit.prune(conn)
        ratelimit.record(conn, project["id"], who, ok)
        if not ok:
            after = ratelimit.check(conn, project["id"], who)
            if after.suspicious and not decision.suspicious:
                db.log_event(conn, project["id"], "suspicious_pin_attempts", "system")
        else:
            token = services.create_room_session(
                conn,
                settings,
                project["id"],
                via="pin",
                pin_version=secret["version"],
                member_id=None,
                device=device_label(request.headers.get("user-agent", "")),
            )
    if not ok:
        raise HTTPException(401, "That PIN is not right. Check it and try again.")
    if room.session:  # replace an older session on this device
        conn.execute("UPDATE contributor_sessions SET revoked_at = ? WHERE id = ?", (db.now_iso(), room.session["id"]))
    set_cookie(request, response, room_cookie_name(slug), token, access.ROOM_SESSION_DAYS)
    return {"state": "need_name"}


@router.get("/people")
def people(slug: str, request: Request, conn: db.Conn = Depends(get_conn)):
    room = access.require_room_pass(request, conn, slug)
    rows = services._members(conn, room.project_id)
    return {"people": [{"id": m["id"], "name": m["display_name"], "invite_only": bool(m["invite_only"])} for m in rows]}


@router.post("/identify")
def identify(slug: str, body: IdentifyIn, request: Request, conn: db.Conn = Depends(get_conn)):
    room = access.require_room_pass(request, conn, slug)
    try:
        with db.transaction(conn):
            services.bind_member(conn, room.session, body.member_id)
    except Conflict as exc:
        raise conflict(str(exc)) from None
    return _state(request, conn, access.resolve_room(request, conn, slug))


@router.post("/not-me")
def not_me(slug: str, request: Request, conn: db.Conn = Depends(get_conn)):
    room = access.require_room_pass(request, conn, slug)
    if room.session["via"] == "invite":
        raise conflict("This device joined with a personal invitation. Use Leave instead.")
    conn.execute("UPDATE contributor_sessions SET member_id = NULL WHERE id = ?", (room.session["id"],))
    return {"state": "need_name"}


@router.post("/leave")
def leave(slug: str, request: Request, response: Response, conn: db.Conn = Depends(get_conn)):
    room = access.resolve_room(request, conn, slug)
    if room.session:
        conn.execute("UPDATE contributor_sessions SET revoked_at = ? WHERE id = ?", (db.now_iso(), room.session["id"]))
    clear_cookie(request, response, room_cookie_name(slug))
    return {"state": "need_pin"}


@router.post("/me/availability")
def availability(slug: str, body: AvailabilityIn, request: Request, conn: db.Conn = Depends(get_conn)):
    room = access.require_contributor(request, conn, slug)
    services.update_member(conn, room.member, {"opted_in": body.opted_in, "capacity": body.capacity})
    db.log_event(
        conn,
        room.project_id,
        "availability_set",
        "contributor",
        room.member["id"],
        data={"opted_in": body.opted_in, "capacity": body.capacity},
    )
    return _state(request, conn, access.resolve_room(request, conn, slug))


# --------------------------------------------------------------------------
# My photos


def _my_photo(conn: db.Conn, room: access.Room, photo: db.Row) -> dict:
    member_id = room.member["id"]
    a = access.active_assignment(conn, photo["id"], member_id)
    latest = db.one(
        conn,
        "SELECT * FROM revisions WHERE photo_id = ? AND member_id = ? ORDER BY number DESC LIMIT 1",
        (photo["id"], member_id),
    )
    if photo["final_revision_id"] and latest and photo["final_revision_id"] == latest["id"]:
        state = "final"
    elif a:
        state = a["status"]
    else:
        state = "reassigned"
    change_note = None
    if state == "needs_changes" and latest:
        row = db.one(
            conn,
            "SELECT note FROM approvals WHERE revision_id = ? AND decision = 'changes_requested'"
            " ORDER BY created_at DESC LIMIT 1",
            (latest["id"],),
        )
        change_note = row["note"] if row else None
    draft = db.one(
        conn,
        "SELECT * FROM merge_drafts WHERE photo_id = ? AND member_id = ? AND status NOT IN ('submitted')"
        " ORDER BY created_at DESC LIMIT 1",
        (photo["id"], member_id),
    )
    return {
        "id": photo["id"],
        "title": photo["title"],
        "width": photo["width"],
        "height": photo["height"],
        "format": photo["format"],
        "thumb_asset_id": photo["thumb_asset_id"],
        "preview_asset_id": photo["preview_asset_id"],
        "processing": photo["processing"],
        "state": state,
        "state_label": STATE_LABELS.get(state, "No longer assigned to you"),
        "active": a is not None,
        "change_note": change_note,
        "downloaded": bool(a and a["original_downloaded_at"]),
        "latest_revision": services.revision_dict(latest) if latest else None,
        "draft": services.draft_dict(conn, draft) if draft else None,
    }


@router.get("/photos")
def my_photos(slug: str, request: Request, conn: db.Conn = Depends(get_conn)):
    room = access.require_contributor(request, conn, slug)
    rows = db.all_rows(
        conn,
        "SELECT p.* FROM photos p JOIN assignments a ON a.photo_id = p.id"
        " WHERE a.member_id = ? AND a.active = 1 ORDER BY p.sort_order",
        (room.member["id"],),
    )
    return {"photos": [_my_photo(conn, room, p) for p in rows]}


@router.get("/photos/{photo_id}")
def my_photo(slug: str, photo_id: str, request: Request, conn: db.Conn = Depends(get_conn)):
    room = access.require_contributor(request, conn, slug)
    photo = access.contributor_photo(conn, room, photo_id, need_active=False)
    return _my_photo(conn, room, photo)


@router.get("/photos/{photo_id}/original")
def download_original(slug: str, photo_id: str, request: Request, conn: db.Conn = Depends(get_conn)):
    room = access.require_contributor(request, conn, slug)
    photo = access.contributor_photo(conn, room, photo_id, need_active=True)
    asset = db.one(conn, "SELECT * FROM assets WHERE id = ?", (photo["original_asset_id"],))
    conn.execute(
        "UPDATE assignments SET original_downloaded_at = COALESCE(original_downloaded_at, ?)"
        " WHERE photo_id = ? AND member_id = ? AND active = 1",
        (db.now_iso(), photo_id, room.member["id"]),
    )
    return file_response(
        request.app.state.storage,
        asset,
        filename=asset["original_filename"] or f"{photo['title']}.{asset['format'].lower()}",
    )


@router.post("/photos/{photo_id}/editing")
def mark_editing(slug: str, photo_id: str, request: Request, conn: db.Conn = Depends(get_conn)):
    room = access.require_contributor(request, conn, slug)
    access.contributor_photo(conn, room, photo_id, need_active=True)
    a = access.active_assignment(conn, photo_id, room.member["id"])
    if a["status"] == "assigned":
        services.set_assignment_status(conn, photo_id, room.member["id"], "editing")
    return {"ok": True}


@router.post("/photos/{photo_id}/uploads", status_code=201)
def upload_edit(
    slug: str,
    photo_id: str,
    request: Request,
    file: UploadFile = File(...),
    conn: db.Conn = Depends(get_conn),
):
    room = access.require_contributor(request, conn, slug)
    photo = access.contributor_photo(conn, room, photo_id, need_active=True)
    if photo["processing"] != "ready":
        raise conflict("This photo is still being prepared. Try again in a moment.")
    data = read_upload(request, file)
    try:
        draft_id = services.create_draft(
            conn,
            request.app.state.storage,
            request.app.state.settings,
            room.project_id,
            photo,
            room.member["id"],
            file.filename or "edit",
            data,
        )
    except ImageRejected as exc:
        raise HTTPException(415, str(exc)) from None
    return services.draft_dict(conn, db.one(conn, "SELECT * FROM merge_drafts WHERE id = ?", (draft_id,)))


# --------------------------------------------------------------------------
# Drafts (Smart Merge / brush) and submission


def _draft(conn: db.Conn, room: access.Room, draft_id: str) -> db.Row:
    row = db.one(
        conn,
        "SELECT * FROM merge_drafts WHERE id = ? AND project_id = ? AND member_id = ?",
        (draft_id, room.project_id, room.member["id"]),
    )
    if not row:
        raise access.not_found("Edit not found.")
    return row


def _require_active(conn: db.Conn, room: access.Room, draft: db.Row) -> None:
    if not access.active_assignment(conn, draft["photo_id"], room.member["id"]):
        raise access.forbidden("This photo is no longer assigned to you. Your earlier edits are kept.")


@router.get("/drafts/{draft_id}")
def get_draft(slug: str, draft_id: str, request: Request, conn: db.Conn = Depends(get_conn)):
    room = access.require_contributor(request, conn, slug)
    return services.draft_dict(conn, _draft(conn, room, draft_id))


@router.post("/drafts/{draft_id}/smart")
def smart(slug: str, draft_id: str, body: SmartIn, request: Request, conn: db.Conn = Depends(get_conn)):
    room = access.require_contributor(request, conn, slug)
    draft = _draft(conn, room, draft_id)
    _require_active(conn, room, draft)
    try:
        services.request_remask(conn, draft, body.sensitivity, None)
    except Conflict as exc:
        raise conflict(str(exc)) from None
    return services.draft_dict(conn, _draft(conn, room, draft_id))


@router.post("/drafts/{draft_id}/align")
def align(slug: str, draft_id: str, body: AlignIn, request: Request, conn: db.Conn = Depends(get_conn)):
    room = access.require_contributor(request, conn, slug)
    draft = _draft(conn, room, draft_id)
    _require_active(conn, room, draft)
    manual = None if body.auto else {"rotate": body.rotate, "scale": body.scale, "dx": body.dx, "dy": body.dy}
    try:
        # Re-aligning also re-renders the edit preview (see jobs.draft_analyse).
        services.request_remask(conn, draft, None, manual, auto_align=body.auto)
    except Conflict as exc:
        raise conflict(str(exc)) from None
    return services.draft_dict(conn, _draft(conn, room, draft_id))


@router.post("/drafts/{draft_id}/mask", status_code=201)
def save_mask(
    slug: str,
    draft_id: str,
    request: Request,
    file: UploadFile = File(...),
    source: str = Form("hybrid"),
    conn: db.Conn = Depends(get_conn),
):
    room = access.require_contributor(request, conn, slug)
    draft = _draft(conn, room, draft_id)
    _require_active(conn, room, draft)
    if source not in ("smart", "manual", "hybrid"):
        raise HTTPException(400, "Unknown mask source.")
    data = read_upload(request, file)
    try:
        mask_id = services.save_mask(conn, request.app.state.storage, draft, data, source)
    except Conflict as exc:
        raise conflict(str(exc)) from None
    return {"mask_id": mask_id}


@router.post("/drafts/{draft_id}/submit", status_code=202)
def submit(slug: str, draft_id: str, body: SubmitIn, request: Request, conn: db.Conn = Depends(get_conn)):
    room = access.require_contributor(request, conn, slug)
    draft = _draft(conn, room, draft_id)
    _require_active(conn, room, draft)
    try:
        services.submit_draft(conn, draft, body.mask_id, body.note.strip())
    except Conflict as exc:
        raise conflict(str(exc)) from None
    return services.draft_dict(conn, _draft(conn, room, draft_id))


@router.get("/edits")
def my_edits(slug: str, request: Request, conn: db.Conn = Depends(get_conn)):
    room = access.require_contributor(request, conn, slug)
    rows = db.all_rows(
        conn,
        "SELECT r.*, p.title, p.thumb_asset_id, p.final_revision_id FROM revisions r"
        " JOIN photos p ON p.id = r.photo_id WHERE r.member_id = ? ORDER BY r.created_at DESC, r.number DESC",
        (room.member["id"],),
    )
    out = []
    for r in rows:
        d = services.revision_dict(r)
        d.update(
            {
                "photo_title": r["title"],
                "thumb_asset_id": r["thumb_asset_id"],
                "is_final": r["final_revision_id"] == r["id"],
                "can_reopen": access.active_assignment(conn, r["photo_id"], room.member["id"]) is not None,
            }
        )
        out.append(d)
    return {"edits": out}


@router.post("/revisions/{revision_id}/reopen", status_code=201)
def reopen(slug: str, revision_id: str, request: Request, conn: db.Conn = Depends(get_conn)):
    room = access.require_contributor(request, conn, slug)
    rev = db.one(
        conn,
        "SELECT * FROM revisions WHERE id = ? AND project_id = ? AND member_id = ?",
        (revision_id, room.project_id, room.member["id"]),
    )
    if not rev:
        raise access.not_found("Edit not found.")
    if not access.active_assignment(conn, rev["photo_id"], room.member["id"]):
        raise access.forbidden("This photo is no longer assigned to you.")
    draft_id = services.reopen_revision(conn, rev)
    return services.draft_dict(conn, db.one(conn, "SELECT * FROM merge_drafts WHERE id = ?", (draft_id,)))


# --------------------------------------------------------------------------
# Invitations


@invite_router.post("/redeem")
def redeem(body: InviteIn, request: Request, response: Response, conn: db.Conn = Depends(get_conn)):
    settings = request.app.state.settings
    try:
        project, token = services.redeem_invite(
            conn, settings, body.token, device_label(request.headers.get("user-agent", ""))
        )
    except Conflict as exc:
        raise conflict(str(exc)) from None
    set_cookie(request, response, room_cookie_name(project["slug"]), token, access.ROOM_SESSION_DAYS)
    return {"room_url": f"/r/{project['slug']}"}


@invite_router.get("/rooms")
def device_rooms(request: Request, conn: db.Conn = Depends(get_conn)):
    """Projects this device has joined (from its room cookies)."""
    rooms = []
    for name in request.cookies:
        if not name.startswith("pr_room_"):
            continue
        slug = name[len("pr_room_") :]
        project = db.one(conn, "SELECT * FROM projects WHERE slug = ? AND status = 'active'", (slug,))
        if not project:
            continue
        session = access.room_session(request, conn, project)
        if session:
            rooms.append({"slug": slug, "name": project["name"], "url": f"/r/{slug}"})
    return {"rooms": rooms}
