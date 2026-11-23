"""A small durable job queue in SQLite, plus the image-processing handlers.

Jobs are rows. A worker claims one atomically (UPDATE ... RETURNING) with a
lease; if the worker dies, the lease expires and another claim picks it up.
Failures retry with exponential backoff up to `max_attempts`;
`PermanentError` fails a job at once (for example an unreadable file).

The worker runs as a thread inside the web process by default
(WORKER_MODE=thread), or as its own process: `python -m pixelrelay.worker`.
"""

from __future__ import annotations

import io
import logging
import threading
import time
import traceback
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image

from . import db

log = logging.getLogger("pixelrelay.jobs")

LEASE_SECONDS = 600
HANDLERS: dict[str, Callable] = {}
FAILURE_HOOKS: dict[str, Callable] = {}
_wake = threading.Event()


class PermanentError(Exception):
    """Do not retry. The message is shown to the user."""


@dataclass
class Context:
    settings: object
    storage: object

    def connect(self) -> db.Conn:
        return db.connect_settings(self.settings)


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn

    return deco


def on_failure(kind: str):
    def deco(fn):
        FAILURE_HOOKS[kind] = fn
        return fn

    return deco


def enqueue(conn: db.Conn, kind: str, payload: dict, project_id: str | None, max_attempts: int = 3) -> str:
    job_id = db.new_id()
    now = db.now_iso()
    db.insert(
        conn,
        "jobs",
        {
            "id": job_id,
            "project_id": project_id,
            "kind": kind,
            "payload_json": db.dumps(payload),
            "status": "queued",
            "attempts": 0,
            "max_attempts": max_attempts,
            "run_after": time.time(),
            "lease_until": None,
            "error": None,
            "result_json": None,
            "created_at": now,
            "updated_at": now,
        },
    )
    _wake.set()
    return job_id


def claim(conn: db.Conn) -> db.Row | None:
    now = time.time()
    lock = " FOR UPDATE SKIP LOCKED" if conn.dialect == "postgres" else ""
    return conn.execute(
        "UPDATE jobs SET status = 'running', attempts = attempts + 1, lease_until = ?, updated_at = ?"
        " WHERE id = (SELECT id FROM jobs WHERE (status = 'queued' AND run_after <= ?)"
        f"             OR (status = 'running' AND lease_until < ?) ORDER BY run_after LIMIT 1{lock})"
        " RETURNING *",
        (now + LEASE_SECONDS, db.now_iso(), now, now),
    ).fetchone()


def run_one(ctx: Context) -> bool:
    """Claim and run one job. Returns False when nothing was ready."""
    conn = ctx.connect()
    try:
        job = claim(conn)
        if job is None:
            return False
        payload = db.loads(job["payload_json"], {})
        fn = HANDLERS.get(job["kind"])
        try:
            if fn is None:
                raise PermanentError(f"unknown job kind {job['kind']}")
            result = fn(ctx, conn, payload) or {}
            conn.execute(
                "UPDATE jobs SET status = 'succeeded', result_json = ?, lease_until = NULL, updated_at = ? WHERE id = ?",
                (db.dumps(result), db.now_iso(), job["id"]),
            )
        except Exception as exc:  # noqa: BLE001 - every failure must be recorded
            permanent = isinstance(exc, PermanentError)
            message = str(exc) if permanent else "Processing failed. It will be retried."
            log.warning("job %s (%s) failed: %s", job["id"], job["kind"], type(exc).__name__)
            log.debug("%s", traceback.format_exc())
            if not permanent and job["attempts"] < job["max_attempts"]:
                conn.execute(
                    "UPDATE jobs SET status = 'queued', run_after = ?, error = ?, lease_until = NULL, updated_at = ? WHERE id = ?",
                    (time.time() + 5 * 2 ** job["attempts"], message, db.now_iso(), job["id"]),
                )
            else:
                final = message if permanent else "Processing failed. Try again, or upload the file again."
                conn.execute(
                    "UPDATE jobs SET status = 'failed', error = ?, lease_until = NULL, updated_at = ? WHERE id = ?",
                    (final, db.now_iso(), job["id"]),
                )
                hook = FAILURE_HOOKS.get(job["kind"])
                if hook:
                    try:
                        hook(ctx, conn, payload, final)
                    except Exception:  # noqa: BLE001
                        log.exception("failure hook for %s failed", job["kind"])
        return True
    finally:
        conn.close()


def run_pending(ctx: Context, limit: int = 100) -> int:
    """Run ready jobs synchronously (tests, scripts)."""
    n = 0
    while n < limit and run_one(ctx):
        n += 1
    return n


class Worker(threading.Thread):
    def __init__(self, ctx: Context):
        super().__init__(name="pixelrelay-worker", daemon=True)
        self.ctx = ctx
        self.stopping = threading.Event()

    def run(self) -> None:
        while not self.stopping.is_set():
            try:
                worked = run_one(self.ctx)
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                worked = False
            if not worked:
                _wake.wait(2.0)
                _wake.clear()

    def stop(self) -> None:
        self.stopping.set()
        _wake.set()


# ---------------------------------------------------------------------------
# Handlers. Imports are local to keep module import cheap and avoid cycles.


def _asset(conn: db.Conn, asset_id: str) -> db.Row:
    row = db.one(conn, "SELECT * FROM assets WHERE id = ?", (asset_id,))
    if not row:
        raise PermanentError("A file is missing.")
    return row


def _read(ctx: Context, conn: db.Conn, asset_id: str) -> bytes:
    return ctx.storage.read(_asset(conn, asset_id)["storage_key"])


@handler("photo.derivatives")
def photo_derivatives(ctx: Context, conn: db.Conn, payload: dict) -> dict:
    from .images import derivatives, load_rgb
    from .storage import save_asset

    photo = db.one(conn, "SELECT * FROM photos WHERE id = ?", (payload["photo_id"],))
    if not photo:
        raise PermanentError("Photo not found.")
    if photo["processing"] == "ready":
        return {"skipped": True}
    try:
        im, icc, _ = load_rgb(_read(ctx, conn, photo["original_asset_id"]))
    except Exception as exc:
        raise PermanentError("This photo could not be decoded.") from exc
    out = derivatives(im, icc)
    with db.transaction(conn):
        ids = {}
        for kind, (data, w, h) in out.items():
            ids[kind] = save_asset(
                conn,
                ctx.storage,
                project_id=photo["project_id"],
                kind=kind,
                data=data,
                fmt="JPEG",
                width=w,
                height=h,
                photo_id=photo["id"],
            )
        conn.execute(
            "UPDATE photos SET thumb_asset_id = ?, preview_asset_id = ?, processing = 'ready', updated_at = ? WHERE id = ?",
            (ids["thumbnail"], ids["preview"], db.now_iso(), photo["id"]),
        )
    return ids


@on_failure("photo.derivatives")
def photo_derivatives_failed(ctx, conn, payload, message):
    conn.execute("UPDATE photos SET processing = 'failed' WHERE id = ?", (payload["photo_id"],))


def _load_pair(ctx: Context, conn: db.Conn, draft: db.Row):
    from .images import load_rgb, to_profile

    photo = db.one(conn, "SELECT * FROM photos WHERE id = ?", (draft["photo_id"],))
    try:
        base, base_icc, base_exif = load_rgb(_read(ctx, conn, photo["original_asset_id"]))
    except Exception as exc:
        raise PermanentError("The original photo could not be decoded.") from exc
    try:
        edit, edit_icc, _ = load_rgb(_read(ctx, conn, draft["upload_asset_id"]))
    except Exception as exc:
        raise PermanentError("Your edit could not be decoded. Export it again as JPEG or PNG.") from exc
    edit = to_profile(edit, edit_icc, base_icc)  # compare and composite in the original's colour space
    return photo, base, base_icc, base_exif, edit


@handler("draft.analyse")
def draft_analyse(ctx: Context, conn: db.Conn, payload: dict) -> dict:
    """Align the upload (when asked), build the Smart Merge mask and the editor previews."""
    from . import merge
    from .images import encode_jpeg, encode_png, to_profile, work_size
    from .storage import save_asset

    draft = db.one(conn, "SELECT * FROM merge_drafts WHERE id = ?", (payload["draft_id"],))
    if not draft or draft["status"] != "processing":
        return {"skipped": True}
    photo, base, base_icc, _, edit = _load_pair(ctx, conn, draft)
    manual = db.loads(draft["manual_align_json"], {}) or None
    previous = db.loads(draft["alignment_json"], {})

    if payload.get("realign") or not previous:
        alignment = merge.align(base, edit, manual)
        if manual:
            # The contributor lined it up by hand: they can see the result, so a
            # poor automatic match becomes a warning rather than a block.
            for w in alignment.warnings:
                if w.code == "alignment_unreliable":
                    w.level = "warning"
                    w.message = "You lined this up by hand. Check the preview closely before submitting."
        al_dict = alignment.to_dict()
        make_preview = True
    else:
        placement = merge.placement_from_dict(previous)
        alignment = merge.Alignment(
            placement,
            previous["correlation"],
            previous["coverage"],
            tuple(previous["edit_size"]),
            tuple(previous["base_size"]),
            previous.get("moved_tiles", 0),
            previous.get("textured_tiles", 0),
            [merge.MergeWarning(**w) for w in previous.get("warnings", [])],
        )
        al_dict = previous
        make_preview = draft["edit_preview_asset_id"] is None

    ws = work_size(*base.size)
    if alignment.blocked:
        mask_res = merge.MaskResult(Image.new("L", ws, 0), 0.0, [], {"sensitivity": draft["sensitivity"]})
    else:
        mask_res = merge.difference_mask(base, edit, alignment, draft["sensitivity"])

    with db.transaction(conn):
        values: dict = {}
        if make_preview:
            # The editor blends this with the photo's preview in the browser, so
            # it is rendered on the same grid; areas the edit does not cover show
            # the original, matching what the final composite does.
            placed, cov = merge.place(edit, alignment.placement, ws, base.size)
            base_ws = base.resize(ws, Image.Resampling.LANCZOS)
            filled = Image.composite(placed, base_ws, cov)
            data = encode_jpeg(to_profile(filled, base_icc, None), 90)
            values["edit_preview_asset_id"] = save_asset(
                conn,
                ctx.storage,
                project_id=draft["project_id"],
                kind="edit_preview",
                data=data,
                fmt="JPEG",
                width=ws[0],
                height=ws[1],
                photo_id=draft["photo_id"],
                member_id=draft["member_id"],
            )
        mask_asset = save_asset(
            conn,
            ctx.storage,
            project_id=draft["project_id"],
            kind="mask",
            data=encode_png(mask_res.mask),
            fmt="PNG",
            width=ws[0],
            height=ws[1],
            photo_id=draft["photo_id"],
            member_id=draft["member_id"],
        )
        mask_id = db.new_id()
        db.insert(
            conn,
            "masks",
            {
                "id": mask_id,
                "project_id": draft["project_id"],
                "draft_id": draft["id"],
                "asset_id": mask_asset,
                "source": "smart",
                "width": ws[0],
                "height": ws[1],
                "settings_json": db.dumps(mask_res.settings),
                "alignment_json": db.dumps(al_dict),
                "created_at": db.now_iso(),
            },
        )
        upload_info = db.loads(draft["analysis_json"], {}).get("upload", {})
        warnings = [w for w in al_dict.get("warnings", [])] + [
            {"code": w.code, "level": w.level, "message": w.message} for w in mask_res.warnings
        ]
        values.update(
            {
                "status": "blocked" if alignment.blocked else "ready",
                "alignment_json": db.dumps(al_dict),
                "warnings_json": db.dumps(warnings),
                "analysis_json": db.dumps(
                    {
                        "upload": upload_info,
                        "changed_fraction": mask_res.changed_fraction,
                        "mask_settings": mask_res.settings,
                    }
                ),
                "current_mask_id": mask_id,
                "updated_at": db.now_iso(),
            }
        )
        db.update(conn, "merge_drafts", draft["id"], values)
    return {"mask_id": mask_id, "blocked": alignment.blocked}


@on_failure("draft.analyse")
def draft_analyse_failed(ctx, conn, payload, message):
    conn.execute(
        "UPDATE merge_drafts SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
        (message, db.now_iso(), payload["draft_id"]),
    )


@handler("draft.composite")
def draft_composite(ctx: Context, conn: db.Conn, payload: dict) -> dict:
    """Full-resolution composite onto the original, then an immutable revision."""
    from . import merge
    from .images import encode_jpeg, encode_png, to_profile, work_size
    from .services import set_assignment_status
    from .storage import save_asset

    draft = db.one(conn, "SELECT * FROM merge_drafts WHERE id = ?", (payload["draft_id"],))
    if not draft or draft["status"] != "submitting":
        return {"skipped": True}
    mask_row = db.one(conn, "SELECT * FROM masks WHERE id = ? AND draft_id = ?", (payload["mask_id"], draft["id"]))
    if not mask_row:
        raise PermanentError("The mask is missing. Save it again.")
    photo, base, base_icc, base_exif, edit = _load_pair(ctx, conn, draft)
    alignment = db.loads(draft["alignment_json"], {})
    placement = merge.placement_from_dict(alignment)
    with Image.open(io.BytesIO(_read(ctx, conn, mask_row["asset_id"]))) as m:
        mask = m.convert("L")

    result = merge.composite(base, edit, placement, mask)
    if result.size != base.size:  # defensive: the canvas must be the original's
        raise PermanentError("Internal error: composite size mismatch.")
    composite_png = encode_png(result, icc=base_icc, exif=base_exif.tobytes(), level=3)
    ws = work_size(*base.size)
    preview = encode_jpeg(to_profile(result.resize(ws, Image.Resampling.LANCZOS), base_icc, None), 90)
    upload = _asset(conn, draft["upload_asset_id"])

    with db.transaction(conn):
        comp_id = save_asset(
            conn,
            ctx.storage,
            project_id=draft["project_id"],
            kind="composite",
            data=composite_png,
            fmt="PNG",
            width=result.width,
            height=result.height,
            photo_id=draft["photo_id"],
            member_id=draft["member_id"],
        )
        prev_id = save_asset(
            conn,
            ctx.storage,
            project_id=draft["project_id"],
            kind="composite_preview",
            data=preview,
            fmt="JPEG",
            width=ws[0],
            height=ws[1],
            photo_id=draft["photo_id"],
            member_id=draft["member_id"],
        )
        number = db.one(
            conn, "SELECT COALESCE(MAX(number), 0) + 1 AS n FROM revisions WHERE photo_id = ?", (draft["photo_id"],)
        )["n"]
        revision_id = db.new_id()
        now = db.now_iso()
        db.insert(
            conn,
            "revisions",
            {
                "id": revision_id,
                "project_id": draft["project_id"],
                "photo_id": draft["photo_id"],
                "member_id": draft["member_id"],
                "number": number,
                "parent_revision_id": draft["parent_revision_id"],
                "draft_id": draft["id"],
                "upload_asset_id": draft["upload_asset_id"],
                "mask_id": mask_row["id"],
                "composite_asset_id": comp_id,
                "composite_preview_asset_id": prev_id,
                "upload_width": upload["width"],
                "upload_height": upload["height"],
                "upload_format": upload["format"],
                "composite_width": result.width,
                "composite_height": result.height,
                "composite_format": "PNG",
                "merge_settings_json": db.dumps(
                    {
                        "mask_source": mask_row["source"],
                        "mask_settings": db.loads(mask_row["settings_json"], {}),
                        "placement": alignment.get("placement", {}),
                    }
                ),
                "warnings_json": draft["warnings_json"],
                "validation_json": db.dumps(
                    {
                        "correlation": alignment.get("correlation"),
                        "coverage": alignment.get("coverage"),
                        "moved_tiles": alignment.get("moved_tiles"),
                    }
                ),
                "note": payload.get("note", ""),
                "status": "submitted",
                "created_at": now,
                "status_updated_at": now,
            },
        )
        db.update(
            conn, "merge_drafts", draft["id"], {"status": "submitted", "revision_id": revision_id, "updated_at": now}
        )
        set_assignment_status(conn, draft["photo_id"], draft["member_id"], "submitted")
        db.log_event(
            conn,
            draft["project_id"],
            "edit_submitted",
            "contributor",
            draft["member_id"],
            draft["photo_id"],
            {"revision": number},
        )
    return {"revision_id": revision_id}


@on_failure("draft.composite")
def draft_composite_failed(ctx, conn, payload, message):
    # Back to ready so the contributor can try again; nothing was written.
    conn.execute(
        "UPDATE merge_drafts SET status = 'ready', error = ?, updated_at = ? WHERE id = ?",
        (message, db.now_iso(), payload["draft_id"]),
    )


@handler("project.export")
def project_export(ctx: Context, conn: db.Conn, payload: dict) -> dict:
    """ZIP of every final version (high-res). JPEG at quality 95 or lossless PNG."""
    from .images import encode_jpeg
    from .storage import save_asset

    fmt = payload.get("format", "jpeg")
    rows = db.all_rows(
        conn,
        "SELECT p.title, p.sort_order, r.composite_asset_id, r.number FROM photos p"
        " JOIN revisions r ON r.id = p.final_revision_id WHERE p.project_id = ? ORDER BY p.sort_order",
        (payload["project_id"],),
    )
    if not rows:
        raise PermanentError("No final versions are selected yet.")
    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for r in rows:
            data = _read(ctx, conn, r["composite_asset_id"])
            stem = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in r["title"]).strip() or "photo"
            name = f"{stem}_final"
            while name in used:
                name += "_"
            used.add(name)
            if fmt == "jpeg":
                with Image.open(io.BytesIO(data)) as im:
                    im.load()
                    data = encode_jpeg(im, 95, icc=im.info.get("icc_profile"), exif=im.info.get("exif"))
                zf.writestr(f"{name}.jpg", data)
            else:
                zf.writestr(f"{name}.png", data)
    with db.transaction(conn):
        asset_id = save_asset(
            conn,
            ctx.storage,
            project_id=payload["project_id"],
            kind="export",
            data=buf.getvalue(),
            fmt="ZIP",
            original_filename="pixelrelay-finals.zip",
        )
        db.log_event(
            conn,
            payload["project_id"],
            "finals_exported",
            "owner",
            payload.get("owner_id"),
            data={"count": len(rows), "format": fmt},
        )
    return {"asset_id": asset_id, "count": len(rows)}
