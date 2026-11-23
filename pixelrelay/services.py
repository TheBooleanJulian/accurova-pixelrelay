"""Domain operations. Routes call these after resolving who the caller is
(see access.py); these functions trust their inputs' ownership but still
validate state transitions.
"""

from __future__ import annotations

import io
import sqlite3
from datetime import timedelta
from pathlib import PurePath

from PIL import Image

from . import db, jobs
from .config import Settings
from .distribution import Contributor, PhotoSlot, Plan, distribute
from .images import ImageRejected, probe, work_size
from .security import derive_pin, new_pin_seed, new_slug, random_token, token_hash
from .storage import StorageProvider, save_asset


class Conflict(Exception):
    """A request that is valid in shape but not in the current state. User-facing."""


ASSIGNMENT_STATES = ("assigned", "editing", "submitted", "needs_changes", "approved")
PHOTO_STATES = ("unassigned", "assigned", "editing", "submitted", "needs_changes", "approved", "final")
INVITE_DAYS = 30


# --------------------------------------------------------------------------
# Projects and the Room PIN


def create_project(
    conn: sqlite3.Connection,
    owner_id: str,
    name: str,
    description: str = "",
    shoot_date: str | None = None,
    deadline: str | None = None,
    participants: list[str] | None = None,
) -> str:
    project_id = db.new_id()
    now = db.now_iso()
    with db.transaction(conn):
        db.insert(
            conn,
            "projects",
            {
                "id": project_id,
                "slug": new_slug(),
                "owner_id": owner_id,
                "name": name,
                "description": description,
                "shoot_date": shoot_date,
                "deadline": deadline,
                "status": "active",
                "created_at": now,
                "updated_at": now,
            },
        )
        db.insert(
            conn,
            "room_access_secrets",
            {
                "project_id": project_id,
                "seed": new_pin_seed(),
                "version": 1,
                "rotated_at": now,
            },
        )
        db.insert(
            conn,
            "storage_integrations",
            {
                "id": db.new_id(),
                "project_id": project_id,
                "provider": "local",
                "config_json": "{}",
                "created_at": now,
            },
        )
        _add_members(conn, project_id, participants or [])
        db.log_event(conn, project_id, "project_created", "owner", owner_id)
    return project_id


def room_pin(conn: sqlite3.Connection, settings: Settings, project_id: str) -> tuple[str, int]:
    row = db.one(conn, "SELECT seed, version FROM room_access_secrets WHERE project_id = ?", (project_id,))
    return derive_pin(settings.secret_key, project_id, row["seed"]), row["version"]


def rotate_pin(conn: sqlite3.Connection, settings: Settings, project_id: str, owner_id: str) -> str:
    with db.transaction(conn):
        conn.execute(
            "UPDATE room_access_secrets SET seed = ?, version = version + 1, rotated_at = ? WHERE project_id = ?",
            (new_pin_seed(), db.now_iso(), project_id),
        )
        # Sessions made with the old PIN stop working (access.room_session also
        # checks the version); invitation-link sessions are kept.
        conn.execute(
            "UPDATE contributor_sessions SET revoked_at = ? WHERE project_id = ? AND via = 'pin' AND revoked_at IS NULL",
            (db.now_iso(), project_id),
        )
        db.log_event(conn, project_id, "pin_rotated", "owner", owner_id)
    return room_pin(conn, settings, project_id)[0]


# --------------------------------------------------------------------------
# Participants


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned or len(cleaned) > 60:
        raise Conflict("Names must be 1 to 60 characters.")
    return cleaned


def _add_members(conn: sqlite3.Connection, project_id: str, names: list[str]) -> list[str]:
    row = db.one(
        conn, "SELECT COALESCE(MAX(sort_order), -1) AS m FROM project_members WHERE project_id = ?", (project_id,)
    )
    order = row["m"] + 1
    ids = []
    seen: set[str] = set()
    for raw in names:
        name = _clean_name(raw)
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        exists = db.one(
            conn,
            "SELECT 1 FROM project_members WHERE project_id = ? AND display_name = ? COLLATE NOCASE AND removed_at IS NULL",
            (project_id, name),
        )
        if exists:
            raise Conflict(f"{name} is already in this project.")
        member_id = db.new_id()
        db.insert(
            conn,
            "project_members",
            {
                "id": member_id,
                "project_id": project_id,
                "display_name": name,
                "role": "contributor",
                "opted_in": 0,
                "capacity": None,
                "invite_only": 0,
                "sort_order": order,
                "created_at": db.now_iso(),
                "removed_at": None,
            },
        )
        order += 1
        ids.append(member_id)
    return ids


def add_members(conn: sqlite3.Connection, project_id: str, names: list[str], owner_id: str) -> list[str]:
    with db.transaction(conn):
        ids = _add_members(conn, project_id, names)
        if ids:
            db.log_event(conn, project_id, "participants_added", "owner", owner_id, data={"count": len(ids)})
    return ids


def update_member(conn: sqlite3.Connection, member: sqlite3.Row, fields: dict) -> None:
    values = {}
    if "display_name" in fields and fields["display_name"] is not None:
        values["display_name"] = _clean_name(fields["display_name"])
        clash = db.one(
            conn,
            "SELECT 1 FROM project_members WHERE project_id = ? AND display_name = ? COLLATE NOCASE"
            " AND removed_at IS NULL AND id != ?",
            (member["project_id"], values["display_name"], member["id"]),
        )
        if clash:
            raise Conflict("Another participant already has that name.")
    if "opted_in" in fields and fields["opted_in"] is not None:
        values["opted_in"] = 1 if fields["opted_in"] else 0
    if "capacity" in fields:
        cap = fields["capacity"]
        if cap is not None and not 0 <= cap <= 500:
            raise Conflict("Capacity must be between 0 and 500.")
        values["capacity"] = cap
    if values:
        db.update(conn, "project_members", member["id"], values)


def remove_member(conn: sqlite3.Connection, member: sqlite3.Row, owner_id: str) -> None:
    now = db.now_iso()
    with db.transaction(conn):
        conn.execute(
            "UPDATE assignments SET active = 0, ended_at = ?, ended_reason = 'participant_removed', updated_at = ?"
            " WHERE member_id = ? AND active = 1",
            (now, now, member["id"]),
        )
        conn.execute(
            "UPDATE contributor_sessions SET revoked_at = ? WHERE member_id = ? AND revoked_at IS NULL",
            (now, member["id"]),
        )
        conn.execute(
            "UPDATE invitation_tokens SET revoked_at = ? WHERE member_id = ? AND revoked_at IS NULL",
            (now, member["id"]),
        )
        db.update(conn, "project_members", member["id"], {"removed_at": now})
        db.log_event(
            conn, member["project_id"], "participant_removed", "owner", owner_id, data={"name": member["display_name"]}
        )


def create_invite(conn: sqlite3.Connection, settings: Settings, member: sqlite3.Row) -> str:
    token = random_token()
    now = db.now_iso()
    with db.transaction(conn):
        conn.execute(
            "UPDATE invitation_tokens SET revoked_at = ? WHERE member_id = ? AND redeemed_at IS NULL AND revoked_at IS NULL",
            (now, member["id"]),
        )
        db.insert(
            conn,
            "invitation_tokens",
            {
                "id": db.new_id(),
                "project_id": member["project_id"],
                "member_id": member["id"],
                "token_hash": token_hash(settings.secret_key, token),
                "created_at": now,
                "expires_at": db.now_iso(timedelta(days=INVITE_DAYS)),
                "redeemed_at": None,
                "revoked_at": None,
            },
        )
    return token


def create_room_session(
    conn: sqlite3.Connection,
    settings: Settings,
    project_id: str,
    *,
    via: str,
    pin_version: int | None,
    member_id: str | None,
    device: str,
) -> str:
    from .access import ROOM_SESSION_DAYS

    token = random_token()
    now = db.now_iso()
    db.insert(
        conn,
        "contributor_sessions",
        {
            "id": db.new_id(),
            "project_id": project_id,
            "member_id": member_id,
            "token_hash": token_hash(settings.secret_key, token),
            "via": via,
            "pin_version": pin_version,
            "device_label": device,
            "created_at": now,
            "last_seen_at": now,
            "expires_at": db.now_iso(timedelta(days=ROOM_SESSION_DAYS)),
            "revoked_at": None,
        },
    )
    return token


def redeem_invite(conn: sqlite3.Connection, settings: Settings, token: str, device: str) -> tuple[sqlite3.Row, str]:
    """Single-use: binds this device to the invited participant. Returns (project, session token)."""
    row = db.one(
        conn,
        "SELECT * FROM invitation_tokens WHERE token_hash = ? AND revoked_at IS NULL",
        (token_hash(settings.secret_key, token),),
    )
    if not row or row["redeemed_at"] or row["expires_at"] <= db.now_iso():
        raise Conflict("This invitation link has expired or was already used. Ask the organiser for a new one.")
    member = db.one(conn, "SELECT * FROM project_members WHERE id = ? AND removed_at IS NULL", (row["member_id"],))
    project = db.one(conn, "SELECT * FROM projects WHERE id = ? AND status = 'active'", (row["project_id"],))
    if not member or not project:
        raise Conflict("This invitation is no longer valid.")
    with db.transaction(conn):
        conn.execute("UPDATE invitation_tokens SET redeemed_at = ? WHERE id = ?", (db.now_iso(), row["id"]))
        # Once someone has claimed their name by invitation, the shared PIN can
        # no longer be used to pick that name.
        db.update(conn, "project_members", member["id"], {"invite_only": 1})
        session = create_room_session(
            conn, settings, project["id"], via="invite", pin_version=None, member_id=member["id"], device=device
        )
        db.log_event(conn, project["id"], "invitation_redeemed", "contributor", member["id"])
    return project, session


def bind_member(conn: sqlite3.Connection, session: sqlite3.Row, member_id: str) -> sqlite3.Row:
    member = db.one(
        conn,
        "SELECT * FROM project_members WHERE id = ? AND project_id = ? AND removed_at IS NULL",
        (member_id, session["project_id"]),
    )
    if not member:
        raise Conflict("That participant is not in this project.")
    if session["via"] == "pin" and member["invite_only"]:
        raise Conflict(f"{member['display_name']} joins with a personal invitation link. Ask the organiser for it.")
    if session["member_id"] and session["member_id"] != member_id and session["via"] == "invite":
        raise Conflict("This device joined with a personal invitation and cannot switch names.")
    conn.execute("UPDATE contributor_sessions SET member_id = ? WHERE id = ?", (member_id, session["id"]))
    db.log_event(conn, session["project_id"], "participant_identified", "contributor", member_id)
    return member


# --------------------------------------------------------------------------
# Photos


def add_original(
    conn: sqlite3.Connection,
    storage: StorageProvider,
    settings: Settings,
    project_id: str,
    filename: str,
    data: bytes,
    owner_id: str,
) -> str:
    if len(data) > settings.max_upload_bytes:
        raise ImageRejected(f"Files must be {settings.max_upload_mb} MB or smaller.")
    info = probe(data, settings.max_image_megapixels)
    title = PurePath(filename or "photo").stem[:120] or "photo"
    photo_id = db.new_id()
    with db.transaction(conn):
        asset_id = save_asset(
            conn,
            storage,
            project_id=project_id,
            kind="original",
            data=data,
            fmt=info.format,
            width=info.width,
            height=info.height,
            photo_id=photo_id,
            original_filename=filename[:200],
        )
        order = db.one(
            conn, "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM photos WHERE project_id = ?", (project_id,)
        )["n"]
        now = db.now_iso()
        db.insert(
            conn,
            "photos",
            {
                "id": photo_id,
                "project_id": project_id,
                "original_asset_id": asset_id,
                "thumb_asset_id": None,
                "preview_asset_id": None,
                "title": title,
                "width": info.width,
                "height": info.height,
                "format": info.format,
                "processing": "pending",
                "available": 1,
                "priority": 0,
                "sort_order": order,
                "final_revision_id": None,
                "created_at": now,
                "updated_at": now,
            },
        )
        jobs.enqueue(conn, "photo.derivatives", {"photo_id": photo_id}, project_id)
        db.log_event(conn, project_id, "photo_added", "owner", owner_id, photo_id)
    return photo_id


def photo_state(photo: sqlite3.Row, assignment: sqlite3.Row | None, latest: sqlite3.Row | None) -> str:
    if photo["final_revision_id"]:
        return "final"
    if assignment:
        return assignment["status"]
    if latest:
        return (
            "approved"
            if latest["status"] == "approved"
            else ("needs_changes" if latest["status"] == "needs_changes" else "submitted")
        )
    return "unassigned"


def list_photos(conn: sqlite3.Connection, project_id: str) -> list[dict]:
    photos = db.all_rows(conn, "SELECT * FROM photos WHERE project_id = ? ORDER BY sort_order", (project_id,))
    active = {
        r["photo_id"]: r
        for r in db.all_rows(
            conn,
            "SELECT a.*, m.display_name FROM assignments a JOIN project_members m ON m.id = a.member_id"
            " WHERE a.project_id = ? AND a.active = 1",
            (project_id,),
        )
    }
    latest = {
        r["photo_id"]: r
        for r in db.all_rows(
            conn,
            "SELECT r.* FROM revisions r JOIN (SELECT photo_id, MAX(number) AS n FROM revisions"
            " WHERE project_id = ? GROUP BY photo_id) x ON x.photo_id = r.photo_id AND x.n = r.number",
            (project_id,),
        )
    }
    counts = {
        r["photo_id"]: r["n"]
        for r in db.all_rows(
            conn, "SELECT photo_id, COUNT(*) AS n FROM revisions WHERE project_id = ? GROUP BY photo_id", (project_id,)
        )
    }
    out = []
    for p in photos:
        a, lr = active.get(p["id"]), latest.get(p["id"])
        out.append(
            {
                "id": p["id"],
                "title": p["title"],
                "width": p["width"],
                "height": p["height"],
                "format": p["format"],
                "processing": p["processing"],
                "available": bool(p["available"]),
                "priority": p["priority"],
                "thumb_asset_id": p["thumb_asset_id"],
                "preview_asset_id": p["preview_asset_id"],
                "original_asset_id": p["original_asset_id"],
                "state": photo_state(p, a, lr),
                "assignee": {"id": a["member_id"], "name": a["display_name"]} if a else None,
                "revisions": counts.get(p["id"], 0),
                "latest_revision_id": lr["id"] if lr else None,
                "final_revision_id": p["final_revision_id"],
            }
        )
    return out


def progress(conn: sqlite3.Connection, project_id: str) -> dict:
    states = {s: 0 for s in PHOTO_STATES}
    photos = list_photos(conn, project_id)
    for p in photos:
        if p["available"] or p["state"] != "unassigned":
            states[p["state"]] += 1
    total = sum(states.values())
    done = states["approved"] + states["final"]
    submitted = done + states["submitted"]
    return {
        "total": total,
        "states": states,
        "done": done,
        "submitted": submitted,
        "done_percent": round(100 * done / total) if total else 0,
        "submitted_percent": round(100 * submitted / total) if total else 0,
    }


# --------------------------------------------------------------------------
# Assignments


def _members(conn: sqlite3.Connection, project_id: str) -> list[sqlite3.Row]:
    return db.all_rows(
        conn,
        "SELECT * FROM project_members WHERE project_id = ? AND removed_at IS NULL ORDER BY sort_order",
        (project_id,),
    )


def loads(conn: sqlite3.Connection, project_id: str) -> dict[str, int]:
    return {
        r["member_id"]: r["n"]
        for r in db.all_rows(
            conn,
            "SELECT member_id, COUNT(*) AS n FROM assignments WHERE project_id = ? AND active = 1 GROUP BY member_id",
            (project_id,),
        )
    }


def preview_distribution(conn: sqlite3.Connection, project_id: str) -> Plan:
    current = loads(conn, project_id)
    people = [
        Contributor(
            m["id"], m["display_name"], m["sort_order"], bool(m["opted_in"]), m["capacity"], current.get(m["id"], 0)
        )
        for m in _members(conn, project_id)
    ]
    open_photos = db.all_rows(
        conn,
        "SELECT p.* FROM photos p WHERE p.project_id = ? AND p.available = 1 AND p.final_revision_id IS NULL"
        " AND NOT EXISTS (SELECT 1 FROM assignments a WHERE a.photo_id = p.id AND a.active = 1)"
        " ORDER BY p.sort_order",
        (project_id,),
    )
    slots = [PhotoSlot(p["id"], p["priority"], p["sort_order"]) for p in open_photos]
    return distribute(slots, people)


def assign(
    conn: sqlite3.Connection,
    project_id: str,
    photo_id: str,
    member_id: str | None,
    owner_id: str,
    source: str = "manual",
) -> dict:
    """Give a photo to someone (or nobody). Ends the previous assignment but
    never touches anyone's uploads or revisions."""
    photo = db.one(conn, "SELECT * FROM photos WHERE id = ? AND project_id = ?", (photo_id, project_id))
    if not photo:
        raise Conflict("Photo not found.")
    member = None
    if member_id:
        member = db.one(
            conn,
            "SELECT * FROM project_members WHERE id = ? AND project_id = ? AND removed_at IS NULL",
            (member_id, project_id),
        )
        if not member:
            raise Conflict("Participant not found.")
    current = db.one(conn, "SELECT * FROM assignments WHERE photo_id = ? AND active = 1", (photo_id,))
    if current and member_id and current["member_id"] == member_id:
        return {"changed": False, "kept_revisions": 0}
    now = db.now_iso()
    kept = 0
    if current:
        kept = db.one(
            conn,
            "SELECT COUNT(*) AS n FROM revisions WHERE photo_id = ? AND member_id = ?",
            (photo_id, current["member_id"]),
        )["n"]
        conn.execute(
            "UPDATE assignments SET active = 0, ended_at = ?, ended_reason = ?, updated_at = ? WHERE id = ?",
            (now, "reassigned" if member_id else "unassigned", now, current["id"]),
        )
    if member:
        latest = db.one(
            conn,
            "SELECT status FROM revisions WHERE photo_id = ? AND member_id = ? ORDER BY number DESC LIMIT 1",
            (photo_id, member_id),
        )
        status = {"approved": "approved", "needs_changes": "needs_changes", "submitted": "submitted"}.get(
            latest["status"] if latest else "", "assigned"
        )
        db.insert(
            conn,
            "assignments",
            {
                "id": db.new_id(),
                "project_id": project_id,
                "photo_id": photo_id,
                "member_id": member_id,
                "status": status,
                "source": source,
                "capacity_at_assignment": member["capacity"],
                "original_downloaded_at": None,
                "active": 1,
                "ended_at": None,
                "ended_reason": None,
                "created_at": now,
                "updated_at": now,
            },
        )
    db.log_event(
        conn,
        project_id,
        "photo_assigned" if member else "photo_unassigned",
        "owner",
        owner_id,
        photo_id,
        {"member": member["display_name"] if member else None, "source": source},
    )
    return {"changed": True, "kept_revisions": kept}


def publish_plan(conn: sqlite3.Connection, project_id: str, items: list[dict], owner_id: str) -> int:
    count = 0
    with db.transaction(conn):
        for item in items:
            current = db.one(conn, "SELECT 1 FROM assignments WHERE photo_id = ? AND active = 1", (item["photo_id"],))
            if current:
                raise Conflict("Some photos were assigned in the meantime. Preview again.")
            res = assign(conn, project_id, item["photo_id"], item["member_id"], owner_id, item.get("source", "auto"))
            count += 1 if res["changed"] else 0
    return count


def set_assignment_status(conn: sqlite3.Connection, photo_id: str, member_id: str, status: str) -> None:
    conn.execute(
        "UPDATE assignments SET status = ?, updated_at = ? WHERE photo_id = ? AND member_id = ? AND active = 1",
        (status, db.now_iso(), photo_id, member_id),
    )


# --------------------------------------------------------------------------
# Drafts, masks and revisions


def create_draft(
    conn: sqlite3.Connection,
    storage: StorageProvider,
    settings: Settings,
    project_id: str,
    photo: sqlite3.Row,
    member_id: str,
    filename: str,
    data: bytes,
) -> str:
    if len(data) > settings.max_upload_bytes:
        raise ImageRejected(f"Files must be {settings.max_upload_mb} MB or smaller.")
    info = probe(data, settings.max_image_megapixels)
    draft_id = db.new_id()
    now = db.now_iso()
    parent = db.one(
        conn,
        "SELECT id FROM revisions WHERE photo_id = ? AND member_id = ? ORDER BY number DESC LIMIT 1",
        (photo["id"], member_id),
    )
    with db.transaction(conn):
        upload_id = save_asset(
            conn,
            storage,
            project_id=project_id,
            kind="upload",
            data=data,
            fmt=info.format,
            width=info.width,
            height=info.height,
            photo_id=photo["id"],
            member_id=member_id,
            original_filename=(filename or "edit")[:200],
        )
        db.insert(
            conn,
            "merge_drafts",
            {
                "id": draft_id,
                "project_id": project_id,
                "photo_id": photo["id"],
                "member_id": member_id,
                "upload_asset_id": upload_id,
                "edit_preview_asset_id": None,
                "parent_revision_id": parent["id"] if parent else None,
                "current_mask_id": None,
                "status": "processing",
                "sensitivity": "medium",
                "manual_align_json": "{}",
                "alignment_json": "{}",
                "warnings_json": db.dumps([{"code": "format", "level": "info", "message": w} for w in info.warnings]),
                "analysis_json": db.dumps(
                    {"upload": {"width": info.width, "height": info.height, "format": info.format}}
                ),
                "error": None,
                "revision_id": None,
                "created_at": now,
                "updated_at": now,
            },
        )
        current = db.one(
            conn,
            "SELECT status FROM assignments WHERE photo_id = ? AND member_id = ? AND active = 1",
            (photo["id"], member_id),
        )
        if current and current["status"] in ("assigned", "needs_changes"):
            set_assignment_status(conn, photo["id"], member_id, "editing")
        jobs.enqueue(conn, "draft.analyse", {"draft_id": draft_id, "realign": True}, project_id)
        db.log_event(conn, project_id, "edit_uploaded", "contributor", member_id, photo["id"])
    return draft_id


def request_remask(
    conn: sqlite3.Connection, draft: sqlite3.Row, sensitivity: str | None, manual: dict | None, auto_align: bool = False
) -> None:
    if draft["status"] in ("submitting", "submitted", "processing"):
        raise Conflict("This edit is busy or already submitted.")
    values: dict = {"status": "processing", "error": None, "updated_at": db.now_iso()}
    realign = False
    if sensitivity:
        values["sensitivity"] = sensitivity
    if manual is not None:
        values["manual_align_json"] = db.dumps(manual)
        realign = True
    if auto_align:
        values["manual_align_json"] = "{}"
        realign = True
    with db.transaction(conn):
        db.update(conn, "merge_drafts", draft["id"], values)
        jobs.enqueue(conn, "draft.analyse", {"draft_id": draft["id"], "realign": realign}, draft["project_id"])


def save_mask(
    conn: sqlite3.Connection,
    storage: StorageProvider,
    draft: sqlite3.Row,
    png: bytes,
    source: str,
) -> str:
    if draft["status"] not in ("ready", "blocked"):
        raise Conflict("This edit cannot be changed right now.")
    photo = db.one(conn, "SELECT width, height FROM photos WHERE id = ?", (draft["photo_id"],))
    expected = work_size(photo["width"], photo["height"])
    try:
        with Image.open(io.BytesIO(png)) as im:
            if im.format != "PNG" or im.size != expected:
                raise Conflict("The mask does not match this photo. Reload and try again.")
            mask = im.convert("L")
    except Conflict:
        raise
    except Exception as exc:
        raise Conflict("The mask could not be read. Reload and try again.") from exc
    from .images import encode_png

    data = encode_png(mask)
    mask_id = db.new_id()
    with db.transaction(conn):
        asset_id = save_asset(
            conn,
            storage,
            project_id=draft["project_id"],
            kind="mask",
            data=data,
            fmt="PNG",
            width=expected[0],
            height=expected[1],
            photo_id=draft["photo_id"],
            member_id=draft["member_id"],
        )
        db.insert(
            conn,
            "masks",
            {
                "id": mask_id,
                "project_id": draft["project_id"],
                "draft_id": draft["id"],
                "asset_id": asset_id,
                "source": source,
                "width": expected[0],
                "height": expected[1],
                "settings_json": db.dumps({"sensitivity": draft["sensitivity"], "edited_by_hand": True}),
                "alignment_json": draft["alignment_json"],
                "created_at": db.now_iso(),
            },
        )
        db.update(conn, "merge_drafts", draft["id"], {"current_mask_id": mask_id, "updated_at": db.now_iso()})
    return mask_id


def submit_draft(conn: sqlite3.Connection, draft: sqlite3.Row, mask_id: str, note: str) -> None:
    if draft["status"] == "blocked":
        alignment = db.loads(draft["alignment_json"], {})
        if alignment.get("placement", {}).get("method") != "manual":
            raise Conflict("Line the edit up by hand first, or upload a better-matched export.")
    elif draft["status"] != "ready":
        raise Conflict("This edit is not ready to submit.")
    mask = db.one(conn, "SELECT id FROM masks WHERE id = ? AND draft_id = ?", (mask_id, draft["id"]))
    if not mask:
        raise Conflict("Save the mask before submitting.")
    with db.transaction(conn):
        db.update(
            conn,
            "merge_drafts",
            draft["id"],
            {"status": "submitting", "current_mask_id": mask_id, "updated_at": db.now_iso()},
        )
        jobs.enqueue(
            conn,
            "draft.composite",
            {"draft_id": draft["id"], "mask_id": mask_id, "note": note[:500]},
            draft["project_id"],
        )


def reopen_revision(conn: sqlite3.Connection, revision: sqlite3.Row) -> str:
    """Start a new draft from an earlier submission: same upload and mask, new revision on submit."""
    old = db.one(conn, "SELECT * FROM merge_drafts WHERE id = ?", (revision["draft_id"],))
    draft_id = db.new_id()
    now = db.now_iso()
    with db.transaction(conn):
        db.insert(
            conn,
            "merge_drafts",
            {
                "id": draft_id,
                "project_id": revision["project_id"],
                "photo_id": revision["photo_id"],
                "member_id": revision["member_id"],
                "upload_asset_id": revision["upload_asset_id"],
                "edit_preview_asset_id": old["edit_preview_asset_id"],
                "parent_revision_id": revision["id"],
                "current_mask_id": None,
                "status": "ready" if old["status"] != "blocked" else "blocked",
                "sensitivity": old["sensitivity"],
                "manual_align_json": old["manual_align_json"],
                "alignment_json": old["alignment_json"],
                "warnings_json": old["warnings_json"],
                "analysis_json": old["analysis_json"],
                "error": None,
                "revision_id": None,
                "created_at": now,
                "updated_at": now,
            },
        )
        source_mask = db.one(conn, "SELECT * FROM masks WHERE id = ?", (revision["mask_id"],))
        mask_id = db.new_id()
        # The new draft points at the same immutable mask file through a new mask row.
        db.insert(
            conn,
            "masks",
            {
                "id": mask_id,
                "project_id": revision["project_id"],
                "draft_id": draft_id,
                "asset_id": source_mask["asset_id"],
                "source": source_mask["source"],
                "width": source_mask["width"],
                "height": source_mask["height"],
                "settings_json": source_mask["settings_json"],
                "alignment_json": source_mask["alignment_json"],
                "created_at": now,
            },
        )
        db.update(conn, "merge_drafts", draft_id, {"current_mask_id": mask_id})
        current = db.one(
            conn,
            "SELECT status FROM assignments WHERE photo_id = ? AND member_id = ? AND active = 1",
            (revision["photo_id"], revision["member_id"]),
        )
        if current and current["status"] in ("assigned", "needs_changes"):
            set_assignment_status(conn, revision["photo_id"], revision["member_id"], "editing")
        db.log_event(
            conn,
            revision["project_id"],
            "edit_reopened",
            "contributor",
            revision["member_id"],
            revision["photo_id"],
            {"revision": revision["number"]},
        )
    return draft_id


# --------------------------------------------------------------------------
# Review


def review(conn: sqlite3.Connection, revision: sqlite3.Row, owner_id: str, decision: str, note: str) -> None:
    if decision not in ("approved", "changes_requested"):
        raise Conflict("Unknown decision.")
    if decision == "changes_requested" and not note.strip():
        raise Conflict("Say briefly what should change.")
    status = "approved" if decision == "approved" else "needs_changes"
    with db.transaction(conn):
        conn.execute(
            "UPDATE revisions SET status = ?, status_updated_at = ? WHERE id = ?",
            (status, db.now_iso(), revision["id"]),
        )
        db.insert(
            conn,
            "approvals",
            {
                "id": db.new_id(),
                "project_id": revision["project_id"],
                "photo_id": revision["photo_id"],
                "revision_id": revision["id"],
                "decision": decision,
                "note": note.strip()[:500],
                "decided_by": owner_id,
                "created_at": db.now_iso(),
            },
        )
        set_assignment_status(conn, revision["photo_id"], revision["member_id"], status)
        if decision == "changes_requested":
            photo = db.one(conn, "SELECT final_revision_id FROM photos WHERE id = ?", (revision["photo_id"],))
            if photo["final_revision_id"] == revision["id"]:
                conn.execute(
                    "UPDATE photos SET final_revision_id = NULL, updated_at = ? WHERE id = ?",
                    (db.now_iso(), revision["photo_id"]),
                )
        db.log_event(
            conn,
            revision["project_id"],
            decision,
            "owner",
            owner_id,
            revision["photo_id"],
            {"revision": revision["number"]},
        )


def select_final(conn: sqlite3.Connection, photo: sqlite3.Row, revision: sqlite3.Row | None, owner_id: str) -> None:
    if revision is not None and revision["status"] != "approved":
        raise Conflict("Approve this edit before choosing it as the final version.")
    with db.transaction(conn):
        conn.execute(
            "UPDATE photos SET final_revision_id = ?, updated_at = ? WHERE id = ?",
            (revision["id"] if revision else None, db.now_iso(), photo["id"]),
        )
        db.insert(
            conn,
            "approvals",
            {
                "id": db.new_id(),
                "project_id": photo["project_id"],
                "photo_id": photo["id"],
                "revision_id": revision["id"] if revision else None,
                "decision": "final_selected" if revision else "final_cleared",
                "note": "",
                "decided_by": owner_id,
                "created_at": db.now_iso(),
            },
        )
        db.log_event(
            conn,
            photo["project_id"],
            "final_selected" if revision else "final_cleared",
            "owner",
            owner_id,
            photo["id"],
            {"revision": revision["number"] if revision else None},
        )


def revision_dict(r: sqlite3.Row, include_member: str | None = None) -> dict:
    d = {
        "id": r["id"],
        "photo_id": r["photo_id"],
        "number": r["number"],
        "status": r["status"],
        "note": r["note"],
        "created_at": r["created_at"],
        "parent_revision_id": r["parent_revision_id"],
        "upload_asset_id": r["upload_asset_id"],
        "composite_asset_id": r["composite_asset_id"],
        "composite_preview_asset_id": r["composite_preview_asset_id"],
        "mask_id": r["mask_id"],
        "upload": {"width": r["upload_width"], "height": r["upload_height"], "format": r["upload_format"]},
        "composite": {"width": r["composite_width"], "height": r["composite_height"], "format": r["composite_format"]},
        "warnings": db.loads(r["warnings_json"], []),
        "merge_settings": db.loads(r["merge_settings_json"], {}),
        "member_id": r["member_id"],
    }
    if include_member is not None:
        d["member_name"] = include_member
    return d


def draft_dict(conn: sqlite3.Connection, d: sqlite3.Row) -> dict:
    mask = db.one(conn, "SELECT * FROM masks WHERE id = ?", (d["current_mask_id"],)) if d["current_mask_id"] else None
    photo = db.one(conn, "SELECT preview_asset_id, width, height FROM photos WHERE id = ?", (d["photo_id"],))
    ws = work_size(photo["width"], photo["height"])
    alignment = db.loads(d["alignment_json"], {})
    return {
        "id": d["id"],
        "photo_id": d["photo_id"],
        "status": d["status"],
        "sensitivity": d["sensitivity"],
        "error": d["error"],
        "warnings": db.loads(d["warnings_json"], []),
        "alignment": alignment,
        "manual_align": db.loads(d["manual_align_json"], {}),
        "analysis": db.loads(d["analysis_json"], {}),
        "upload_asset_id": d["upload_asset_id"],
        "edit_preview_asset_id": d["edit_preview_asset_id"],
        "base_preview_asset_id": photo["preview_asset_id"],
        "mask": {"id": mask["id"], "asset_id": mask["asset_id"], "source": mask["source"]} if mask else None,
        "work_size": list(ws),
        "revision_id": d["revision_id"],
        "parent_revision_id": d["parent_revision_id"],
        "created_at": d["created_at"],
        "updated_at": d["updated_at"],
    }
