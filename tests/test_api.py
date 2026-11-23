"""Integration tests through the HTTP API: access, assignments, revisions, review."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from pixelrelay import db, ratelimit
from tests.conftest import join, make_client
from tests.fixtures import edited_copy, lowres_jpeg_export, textured_photo


def assign_all(owner, project, mapping):
    for photo_idx, name in mapping.items():
        r = owner.put(
            f"/api/projects/{project['id']}/photos/{project['photos'][photo_idx]}/assignment",
            json={"member_id": project["members"][name]},
        )
        assert r.status_code == 200, r.text


def upload_and_submit(client, project, photo_id, run_jobs, data=None):
    data = data or lowres_jpeg_export(edited_copy(textured_photo()))
    slug = project["slug"]
    r = client.post(f"/api/rooms/{slug}/photos/{photo_id}/uploads", files={"file": ("edit.jpg", data, "image/jpeg")})
    assert r.status_code == 201, r.text
    draft = r.json()
    run_jobs()
    draft = client.get(f"/api/rooms/{slug}/drafts/{draft['id']}").json()
    assert draft["status"] == "ready", draft
    r = client.post(
        f"/api/rooms/{slug}/drafts/{draft['id']}/submit", json={"mask_id": draft["mask"]["id"], "note": "skin + colour"}
    )
    assert r.status_code == 202, r.text
    run_jobs()
    return client.get(f"/api/rooms/{slug}/drafts/{draft['id']}").json()


# --------------------------------------------------------------------------
# Projects, PIN and sessions


def test_project_has_persistent_six_digit_pin(owner, project):
    assert len(project["pin"]) == 6 and project["pin"].isdigit()
    again = owner.get(f"/api/projects/{project['id']}").json()
    assert again["pin"] == project["pin"]
    assert again["room_url"].endswith(f"/r/{project['slug']}")


def dump_database(conn) -> str:
    """Every value in every table, as text (both backends)."""
    if conn.dialect == "sqlite":
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    else:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = ?", (conn.schema,)
            )
        ]
    return "\n".join(repr(tuple(row)) for t in tables for row in conn.execute(f"SELECT * FROM {t}"))


def test_pin_is_not_stored_in_plain_text(app, project):
    conn = db.connect_settings(app.state.settings)
    dump = dump_database(conn)
    conn.close()
    assert "Group shoot" in dump  # the dump does see the project
    assert project["pin"] not in dump.replace(project["id"], "")


def test_contributor_joins_without_email(app, project):
    c = make_client(app)
    slug = project["slug"]
    assert c.get(f"/api/rooms/{slug}").json() == {"state": "need_pin"}
    assert c.get(f"/api/rooms/{slug}/people").status_code == 401
    assert (
        c.post(
            f"/api/rooms/{slug}/join", json={"pin": "000000" if project["pin"] != "000000" else "111111"}
        ).status_code
        == 401
    )
    assert c.post(f"/api/rooms/{slug}/join", json={"pin": project["pin"]}).status_code == 200
    names = [p["name"] for p in c.get(f"/api/rooms/{slug}/people").json()["people"]]
    assert names == ["Ava", "Ben", "Cy"]
    r = c.post(f"/api/rooms/{slug}/identify", json={"member_id": project["members"]["Ava"]})
    assert r.json()["state"] == "ready" and r.json()["me"]["name"] == "Ava"
    # Remembered: a new request with the same cookie jar is still Ava.
    assert c.get(f"/api/rooms/{slug}").json()["me"]["name"] == "Ava"
    # Session cookie is HttpOnly and SameSite.
    raw = c.post(f"/api/rooms/{slug}/join", json={"pin": project["pin"]}).headers["set-cookie"]
    assert "HttpOnly" in raw and "SameSite=lax" in raw


def test_not_me_and_leave(app, project):
    c = join(app, project, "Ava")
    slug = project["slug"]
    assert c.post(f"/api/rooms/{slug}/not-me").json()["state"] == "need_name"
    assert c.get(f"/api/rooms/{slug}").json()["state"] == "need_name"
    c.post(f"/api/rooms/{slug}/identify", json={"member_id": project["members"]["Ben"]})
    c.post(f"/api/rooms/{slug}/leave")
    assert c.get(f"/api/rooms/{slug}").json()["state"] == "need_pin"


def test_owner_can_revoke_remembered_device(app, owner, project):
    c = join(app, project, "Ava")
    sessions = owner.get(f"/api/projects/{project['id']}/members/{project['members']['Ava']}/sessions").json()[
        "sessions"
    ]
    assert len(sessions) == 1
    owner.post(f"/api/projects/{project['id']}/sessions/{sessions[0]['id']}/revoke")
    assert c.get(f"/api/rooms/{project['slug']}").json()["state"] == "need_pin"


def test_pin_rotation_invalidates_pin_sessions_only(app, owner, project):
    pin_user = join(app, project, "Ava")
    link = owner.post(f"/api/projects/{project['id']}/members/{project['members']['Ben']}/invite").json()["url"]
    invited = make_client(app)
    assert invited.post("/api/invites/redeem", json={"token": link.split("#", 1)[1]}).status_code == 200
    new_pin = owner.post(f"/api/projects/{project['id']}/pin/rotate").json()["pin"]
    assert new_pin != project["pin"] or True  # 1-in-a-million chance of equality is fine
    assert pin_user.get(f"/api/rooms/{project['slug']}").json()["state"] == "need_pin"
    assert invited.get(f"/api/rooms/{project['slug']}").json()["me"]["name"] == "Ben"
    late = make_client(app)
    if new_pin != project["pin"]:
        assert late.post(f"/api/rooms/{project['slug']}/join", json={"pin": project["pin"]}).status_code == 401
    assert late.post(f"/api/rooms/{project['slug']}/join", json={"pin": new_pin}).status_code == 200


def test_invitation_is_single_use_and_locks_the_name(app, owner, project):
    link = owner.post(f"/api/projects/{project['id']}/members/{project['members']['Cy']}/invite").json()["url"]
    token = link.split("#", 1)[1]
    first, second = make_client(app), make_client(app)
    assert first.post("/api/invites/redeem", json={"token": token}).status_code == 200
    assert second.post("/api/invites/redeem", json={"token": token}).status_code == 409
    # Someone with only the shared PIN can no longer pick "Cy".
    pin_user = make_client(app)
    pin_user.post(f"/api/rooms/{project['slug']}/join", json={"pin": project["pin"]})
    r = pin_user.post(f"/api/rooms/{project['slug']}/identify", json={"member_id": project["members"]["Cy"]})
    assert r.status_code == 409


def test_pin_brute_force_is_rate_limited(app, project):
    c = make_client(app)
    slug = project["slug"]
    wrong = "000000" if project["pin"] != "000000" else "111111"
    codes = [
        c.post(f"/api/rooms/{slug}/join", json={"pin": wrong}).status_code for _ in range(ratelimit.FREE_TRIES + 1)
    ]
    assert codes[: ratelimit.FREE_TRIES] == [401] * ratelimit.FREE_TRIES
    r = c.post(f"/api/rooms/{slug}/join", json={"pin": project["pin"]})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def test_rate_limit_decays_and_never_locks_out(app):
    conn = db.connect_settings(app.state.settings)
    t0 = 1_000_000.0
    for i in range(30):
        ratelimit.record(conn, "p", "ip", False, now=t0 + i)
    d = ratelimit.check(conn, "p", "ip", now=t0 + 31)
    assert not d.allowed and d.retry_after <= ratelimit.MAX_DELAY + 1
    # After the window passes, the same client may try again.
    assert ratelimit.check(conn, "p", "ip", now=t0 + ratelimit.PROJECT_WINDOW + 60).allowed
    # Another IP is not blocked by the first one's failures alone (below the project alert).
    assert ratelimit.check(conn, "p", "other-ip", now=t0 + 31).allowed


def test_suspicious_activity_is_flagged(app, owner, project):
    import time

    conn = db.connect_settings(app.state.settings)
    for i in range(ratelimit.PROJECT_ALERT):
        ratelimit.record(conn, project["id"], f"ip{i}", False, now=time.time() - 100)
    assert owner.get(f"/api/projects/{project['id']}").json()["suspicious_pin_activity"] is True


def test_csrf_header_required(app, owner, project):
    bare = make_client(app)
    bare.headers.pop("X-PixelRelay")
    assert bare.post(f"/api/rooms/{project['slug']}/join", json={"pin": project["pin"]}).status_code == 403
    assert (
        owner.post("/api/projects", json={"name": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    )


# --------------------------------------------------------------------------
# Permissions


def test_contributor_cannot_use_owner_functions(app, project):
    c = join(app, project, "Ava")
    pid = project["id"]
    assert c.get(f"/api/projects/{pid}").status_code == 401
    assert c.post(f"/api/projects/{pid}/pin/rotate").status_code == 401
    assert (
        c.put(
            f"/api/projects/{pid}/photos/{project['photos'][0]}/assignment",
            json={"member_id": project["members"]["Ava"]},
        ).status_code
        == 401
    )
    assert c.get("/api/projects").status_code == 401


def test_other_owner_gets_404(app, project):
    other = make_client(app)
    other.post("/api/auth/dev-login", json={"email": "someone@example.com"})
    assert other.get(f"/api/projects/{project['id']}").status_code == 404
    assert other.get(f"/api/projects/{project['id']}/photos").status_code == 404


def test_file_access_is_authorised(app, owner, project, run_jobs):
    assign_all(owner, project, {0: "Ava", 1: "Ben"})
    ava, ben = join(app, project, "Ava"), join(app, project, "Ben")
    photos = owner.get(f"/api/projects/{project['id']}/photos").json()["photos"]
    p0, p1 = photos[0], photos[1]
    assert ava.get(f"/api/files/{p0['thumb_asset_id']}").status_code == 200
    assert ava.get(f"/api/files/{p1['thumb_asset_id']}").status_code == 404  # not Ava's photo
    assert make_client(app).get(f"/api/files/{p0['thumb_asset_id']}").status_code == 404  # anonymous
    assert ava.get(f"/api/rooms/{project['slug']}/photos/{p1['id']}/original").status_code == 404
    draft = upload_and_submit(ava, project, p0["id"], run_jobs)
    rev = ava.get(f"/api/rooms/{project['slug']}/edits").json()["edits"][0]
    assert ava.get(f"/api/files/{rev['composite_asset_id']}").status_code == 200
    assert ben.get(f"/api/files/{rev['composite_asset_id']}").status_code == 404  # another contributor's edit
    assert ben.get(f"/api/files/{draft['upload_asset_id']}").status_code == 404
    assert owner.get(f"/api/files/{rev['composite_asset_id']}").status_code == 200


# --------------------------------------------------------------------------
# Assignments


def test_balanced_distribution_preview_and_publish(app, owner, project):
    pid = project["id"]
    for name, cap in (("Ava", 2), ("Ben", None)):
        owner.patch(f"/api/projects/{pid}/members/{project['members'][name]}", json={"opted_in": True, "capacity": cap})
    plan = owner.post(f"/api/projects/{pid}/assignments/preview").json()
    counts = {x["member_name"]: x["count"] for x in plan["load"]}
    assert counts == {"Ava": 2, "Ben": 1} or counts == {"Ava": 1, "Ben": 2}
    assert "Cy" not in counts  # not opted in
    # Nothing changes until the owner publishes.
    assert all(p["state"] == "unassigned" for p in owner.get(f"/api/projects/{pid}/photos").json()["photos"])
    items = [{"photo_id": a["photo_id"], "member_id": a["member_id"]} for a in plan["assignments"]]
    assert owner.post(f"/api/projects/{pid}/assignments/publish", json={"assignments": items}).json()["assigned"] == 3
    states = [p["state"] for p in owner.get(f"/api/projects/{pid}/photos").json()["photos"]]
    assert states == ["assigned"] * 3
    # Publishing a stale preview again is refused rather than double-assigning.
    assert owner.post(f"/api/projects/{pid}/assignments/publish", json={"assignments": items}).status_code == 409


def test_contributor_opt_in(app, owner, project):
    c = join(app, project, "Cy")
    r = c.post(f"/api/rooms/{project['slug']}/me/availability", json={"opted_in": True, "capacity": 1})
    assert r.json()["me"]["capacity"] == 1
    members = owner.get(f"/api/projects/{project['id']}/members").json()["members"]
    cy = next(m for m in members if m["display_name"] == "Cy")
    assert cy["opted_in"] and cy["capacity"] == 1


def test_parallel_editing_by_several_contributors(app, owner, project, run_jobs):
    assign_all(owner, project, {0: "Ava", 1: "Ben", 2: "Cy"})
    clients = {n: join(app, project, n) for n in ("Ava", "Ben", "Cy")}
    slug = project["slug"]
    mine = {n: [p["id"] for p in c.get(f"/api/rooms/{slug}/photos").json()["photos"]] for n, c in clients.items()}
    assert mine == {"Ava": [project["photos"][0]], "Ben": [project["photos"][1]], "Cy": [project["photos"][2]]}
    # Everyone uploads before anyone's processing runs: no queueing between people.
    drafts = {}
    for i, (n, c) in enumerate(clients.items()):
        r = c.post(
            f"/api/rooms/{slug}/photos/{project['photos'][i]}/uploads",
            files={"file": ("e.jpg", lowres_jpeg_export(edited_copy(textured_photo())), "image/jpeg")},
        )
        drafts[n] = r.json()["id"]
    run_jobs()
    for n, c in clients.items():
        d = c.get(f"/api/rooms/{slug}/drafts/{drafts[n]}").json()
        assert d["status"] == "ready"
    prog = owner.get(f"/api/projects/{project['id']}").json()["progress"]
    assert prog["states"]["editing"] == 3


def test_reassignment_preserves_history(app, owner, project, run_jobs):
    assign_all(owner, project, {0: "Ava"})
    ava = join(app, project, "Ava")
    upload_and_submit(ava, project, project["photos"][0], run_jobs)
    r = owner.put(
        f"/api/projects/{project['id']}/photos/{project['photos'][0]}/assignment",
        json={"member_id": project["members"]["Ben"]},
    )
    assert r.json() == {"changed": True, "kept_revisions": 1}
    detail = owner.get(f"/api/projects/{project['id']}/photos/{project['photos'][0]}").json()
    assert len(detail["revisions"]) == 1 and detail["revisions"][0]["member_name"] == "Ava"
    assert [a["active"] for a in detail["assignments"]] == [False, True]
    # Ava keeps her history but can no longer upload for this photo.
    assert len(ava.get(f"/api/rooms/{project['slug']}/edits").json()["edits"]) == 1
    r = ava.post(
        f"/api/rooms/{project['slug']}/photos/{project['photos'][0]}/uploads",
        files={"file": ("e.jpg", lowres_jpeg_export(textured_photo()), "image/jpeg")},
    )
    assert r.status_code == 403


# --------------------------------------------------------------------------
# Revisions, Smart Merge and review


def test_full_editing_journey(app, owner, project, run_jobs):
    pid, slug, photo_id = project["id"], project["slug"], project["photos"][0]
    assign_all(owner, project, {0: "Ava"})
    ava = join(app, project, "Ava")

    original = ava.get(f"/api/rooms/{slug}/photos/{photo_id}/original")
    assert original.status_code == 200 and original.content.startswith(b"\xff\xd8")
    assert ava.get(f"/api/rooms/{slug}/photos/{photo_id}").json()["downloaded"] is True

    draft = upload_and_submit(ava, project, photo_id, run_jobs)
    assert draft["status"] == "submitted"
    revs = ava.get(f"/api/rooms/{slug}/edits").json()["edits"]
    assert len(revs) == 1 and revs[0]["number"] == 1
    comp = Image.open(io.BytesIO(owner.get(f"/api/files/{revs[0]['composite_asset_id']}").content))
    assert comp.size == (900, 600), "composite keeps the original canvas"
    assert any(w["code"] == "lower_resolution" for w in revs[0]["warnings"])

    # Owner requests changes; contributor sees the note, reopens, refines the mask, resubmits.
    r = owner.post(f"/api/projects/{pid}/revisions/{revs[0]['id']}/request-changes", json={"note": ""})
    assert r.status_code == 409  # a note is required
    owner.post(f"/api/projects/{pid}/revisions/{revs[0]['id']}/request-changes", json={"note": "Softer edges please"})
    mine = ava.get(f"/api/rooms/{slug}/photos/{photo_id}").json()
    assert mine["state"] == "needs_changes" and mine["change_note"] == "Softer edges please"

    reopened = ava.post(f"/api/rooms/{slug}/revisions/{revs[0]['id']}/reopen").json()
    assert reopened["status"] == "ready" and reopened["parent_revision_id"] == revs[0]["id"]
    w, h = reopened["work_size"]
    brushed = Image.new("L", (w, h), 0)
    brushed.paste(255, (100, 100, 400, 300))
    buf = io.BytesIO()
    brushed.save(buf, "PNG")
    r = ava.post(
        f"/api/rooms/{slug}/drafts/{reopened['id']}/mask",
        files={"file": ("mask.png", buf.getvalue(), "image/png")},
        data={"source": "hybrid"},
    )
    assert r.status_code == 201, r.text
    ava.post(f"/api/rooms/{slug}/drafts/{reopened['id']}/submit", json={"mask_id": r.json()["mask_id"]})
    run_jobs()
    revs = ava.get(f"/api/rooms/{slug}/edits").json()["edits"]
    assert [x["number"] for x in revs] == [2, 1]
    assert revs[0]["parent_revision_id"] == revs[1]["id"]
    assert revs[0]["upload_asset_id"] == revs[1]["upload_asset_id"]  # same upload, new revision
    assert revs[0]["composite_asset_id"] != revs[1]["composite_asset_id"]

    # Cannot finalise before approval; approve then finalise.
    assert (
        owner.put(f"/api/projects/{pid}/photos/{photo_id}/final", json={"revision_id": revs[0]["id"]}).status_code
        == 409
    )
    owner.post(f"/api/projects/{pid}/revisions/{revs[0]['id']}/approve", json={})
    assert (
        owner.put(f"/api/projects/{pid}/photos/{photo_id}/final", json={"revision_id": revs[0]["id"]}).status_code
        == 200
    )
    photo = owner.get(f"/api/projects/{pid}/photos/{photo_id}").json()
    assert photo["photo"]["state"] == "final"
    assert ava.get(f"/api/rooms/{slug}/photos/{photo_id}").json()["state"] == "final"

    # Export the finals.
    job = owner.post(f"/api/projects/{pid}/exports", json={"format": "jpeg"}).json()
    run_jobs()
    exp = owner.get(f"/api/projects/{pid}/exports").json()["exports"][0]
    assert exp["status"] == "succeeded" and exp["count"] == 1 and exp["job_id"] == job["job_id"]
    z = owner.get(f"/api/files/{exp['asset_id']}?download=1")
    assert z.status_code == 200 and z.content[:2] == b"PK"
    assert ava.get(f"/api/files/{exp['asset_id']}").status_code == 404

    events = [e["kind"] for e in owner.get(f"/api/projects/{pid}/activity").json()["events"]]
    for kind in (
        "edit_uploaded",
        "edit_submitted",
        "changes_requested",
        "approved",
        "final_selected",
        "finals_exported",
    ):
        assert kind in events


def test_new_upload_never_overwrites_original_or_previous(app, owner, project, run_jobs):
    pid, slug, photo_id = project["id"], project["slug"], project["photos"][0]
    assign_all(owner, project, {0: "Ava"})
    ava = join(app, project, "Ava")
    before = owner.get(
        f"/api/files/{owner.get(f'/api/projects/{pid}/photos').json()['photos'][0]['original_asset_id']}"
    ).content
    upload_and_submit(ava, project, photo_id, run_jobs)
    first = ava.get(f"/api/rooms/{slug}/edits").json()["edits"][0]
    first_bytes = ava.get(f"/api/files/{first['composite_asset_id']}").content
    upload_and_submit(
        ava, project, photo_id, run_jobs, data=lowres_jpeg_export(edited_copy(textured_photo(), (100, 100, 250, 220)))
    )
    edits = ava.get(f"/api/rooms/{slug}/edits").json()["edits"]
    assert len(edits) == 2
    assert ava.get(f"/api/files/{first['composite_asset_id']}").content == first_bytes
    original_id = owner.get(f"/api/projects/{pid}/photos").json()["photos"][0]["original_asset_id"]
    assert owner.get(f"/api/files/{original_id}").content == before


def test_database_enforces_immutability(app, owner, project, run_jobs):
    assign_all(owner, project, {0: "Ava"})
    upload_and_submit(join(app, project, "Ava"), project, project["photos"][0], run_jobs)
    conn = db.connect_settings(app.state.settings)
    with pytest.raises(db.DatabaseError):
        conn.execute("UPDATE assets SET storage_key = 'x' WHERE kind = 'original'")
    with pytest.raises(db.DatabaseError):
        conn.execute("UPDATE revisions SET composite_asset_id = upload_asset_id")
    with pytest.raises(db.DatabaseError):
        conn.execute("DELETE FROM revisions")
    with pytest.raises(db.DatabaseError):
        conn.execute("UPDATE photos SET original_asset_id = thumb_asset_id")
    # Status changes are still allowed.
    conn.execute("UPDATE revisions SET status = 'approved'")
    # And storage refuses to overwrite a key.
    from pixelrelay.storage import StorageError

    key = db.one(conn, "SELECT storage_key FROM assets WHERE kind = 'original'")["storage_key"]
    with pytest.raises(StorageError):
        app.state.storage.put(key, b"overwrite")


def test_unaligned_upload_is_blocked_until_lined_up_by_hand(app, owner, project, run_jobs):
    assign_all(owner, project, {0: "Ava"})
    ava = join(app, project, "Ava")
    slug = project["slug"]
    rng = np.random.default_rng(3)
    junk = Image.fromarray(rng.integers(0, 255, (10, 15, 3), dtype=np.uint8), "RGB").resize(
        (450, 300), Image.Resampling.BICUBIC
    )
    buf = io.BytesIO()
    junk.save(buf, "JPEG")
    draft = ava.post(
        f"/api/rooms/{slug}/photos/{project['photos'][0]}/uploads",
        files={"file": ("junk.jpg", buf.getvalue(), "image/jpeg")},
    ).json()
    run_jobs()
    draft = ava.get(f"/api/rooms/{slug}/drafts/{draft['id']}").json()
    assert draft["status"] == "blocked"
    assert any(w["code"] == "alignment_unreliable" and w["level"] == "blocker" for w in draft["warnings"])
    r = ava.post(f"/api/rooms/{slug}/drafts/{draft['id']}/submit", json={"mask_id": draft["mask"]["id"]})
    assert r.status_code == 409
    ava.post(f"/api/rooms/{slug}/drafts/{draft['id']}/align", json={"rotate": 0, "scale": 1.0, "dx": 0, "dy": 0})
    run_jobs()
    draft = ava.get(f"/api/rooms/{slug}/drafts/{draft['id']}").json()
    assert draft["alignment"]["placement"]["method"] == "manual"
    r = ava.post(f"/api/rooms/{slug}/drafts/{draft['id']}/submit", json={"mask_id": draft["mask"]["id"]})
    assert r.status_code == 202


def test_sensitivity_change_creates_new_mask(app, owner, project, run_jobs):
    assign_all(owner, project, {0: "Ava"})
    ava = join(app, project, "Ava")
    slug = project["slug"]
    d = ava.post(
        f"/api/rooms/{slug}/photos/{project['photos'][0]}/uploads",
        files={"file": ("e.jpg", lowres_jpeg_export(edited_copy(textured_photo())), "image/jpeg")},
    ).json()
    run_jobs()
    first = ava.get(f"/api/rooms/{slug}/drafts/{d['id']}").json()["mask"]["id"]
    ava.post(f"/api/rooms/{slug}/drafts/{d['id']}/smart", json={"sensitivity": "high"})
    run_jobs()
    after = ava.get(f"/api/rooms/{slug}/drafts/{d['id']}").json()
    assert after["sensitivity"] == "high" and after["mask"]["id"] != first


def test_rejects_non_image_uploads(owner, project):
    r = owner.post(
        f"/api/projects/{project['id']}/photos", files={"file": ("x.jpg", b"<html>nope</html>", "image/jpeg")}
    )
    assert r.status_code == 415


def test_progress_reflects_real_states(app, owner, project, run_jobs):
    pid = project["id"]
    assert owner.get(f"/api/projects/{pid}").json()["progress"]["states"]["unassigned"] == 3
    assign_all(owner, project, {0: "Ava", 1: "Ben"})
    upload_and_submit(join(app, project, "Ava"), project, project["photos"][0], run_jobs)
    states = owner.get(f"/api/projects/{pid}").json()["progress"]["states"]
    assert states["submitted"] == 1 and states["assigned"] == 1 and states["unassigned"] == 1


def test_production_refuses_dev_auth(tmp_path):
    from pixelrelay.config import load_settings

    with pytest.raises(RuntimeError):
        load_settings(
            env={"APP_ENV": "production", "AUTH_MODE": "dev", "DATA_DIR": str(tmp_path)}, env_file=tmp_path / "none.env"
        )


def test_log_redaction():
    from pixelrelay.logging_setup import redact

    assert "123456" not in redact('{"pin": "123456"}')
    assert "abc" not in redact("Cookie: pr_room_abcd2345ef=abcDEF123")
