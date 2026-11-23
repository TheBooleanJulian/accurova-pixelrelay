# Architecture

## Processes

One Python process (`python -m pixelrelay`) serves the HTML, static files and JSON API, and by default runs the image worker as a background thread. With `WORKER_MODE=off`, run `python -m pixelrelay.worker` as a second process against the same `DATA_DIR`. Both processes use the same SQLite database (WAL mode) and file store.

```
browser ──HTTP──▶ FastAPI routes ──▶ services ──▶ SQLite
                       │                 └──enqueue──▶ jobs table
                       └── /api/files/{id} (authorised) ◀── storage (data/files)
worker thread/process ──claim──▶ jobs ──▶ merge.py / images.py ──▶ storage + SQLite
```

### On Cloudflare (production)

The same process runs in one Cloudflare Container. `db.py` and `storage.py` switch backends from settings, and nothing else in the app knows the difference.

```
browser ──▶ Worker accurova-pixelrelay (cloudflare/worker.js, forwards everything)
              └──▶ Durable Object "main" ──▶ Container (the Dockerfile image, standard-1, sleeps after 15 min idle)
                                               ├──▶ Supabase session pooler ──▶ schema accurova_pixelrelay (TLS, app role)
                                               ├──▶ R2 S3 API ──▶ bucket accurova-pixelrelay (bucket-scoped token)
                                               └──▶ Supabase Auth (owner magic links)
```

- The first request after a sleep starts the container (Python, numpy, first database connection). The Worker waits up to 90 s and otherwise answers 503 "starting up".
- If a sleep or restart interrupts a job, the job's lease runs out and the next worker run picks it up again. Jobs are claimed with `FOR UPDATE SKIP LOCKED`.
- Every write transaction takes one Postgres advisory lock, so check-then-write code behaves exactly as it does under SQLite's `BEGIN IMMEDIATE`.

## Modules

| Module | Role |
|---|---|
| `config.py` | Settings from env / `.env` (stdlib reader). Refuses unsafe production settings. |
| `db.py` | Connections, migration runner (`migrations/*.sql`, applied at start-up), helpers, `log_event`. |
| `security.py` | Random tokens, keyed hashes, PIN derivation, slugs, device labels. |
| `ratelimit.py` | PIN brute-force protection (progressive delays, decaying windows, suspicious flag). |
| `access.py` | **All authorisation**: owner sessions, room sessions (PIN version check), project and row scoping, file access rules. |
| `auth.py` | Owner identity: Supabase Auth (magic link, token verification via `GET /auth/v1/user`) and dev sign-in. |
| `services.py` | Domain operations and state transitions (projects, members, invitations, photos, assignments, drafts, masks, revisions, review). |
| `distribution.py` | Pure, deterministic balanced assignment. |
| `images.py` | Decode and validate, EXIF orientation, ICC, thumbnails and previews. |
| `merge.py` | Smart Merge (see [image-processing.md](image-processing.md)). |
| `jobs.py` | Durable queue and handlers: `photo.derivatives`, `draft.analyse`, `draft.composite`, `project.export`. |
| `storage.py` | `StorageProvider` interface, write-once `LocalDiskStorage`, `save_asset`. |
| `api_auth.py`, `api_owner.py`, `api_room.py` | HTTP routes. Each resolves its caller through `access.py` first. |
| `app.py` | App factory, CSRF and security-header middleware, page routes, file serving. |
| `web/` | HTML shells and vanilla JS: `owner.js`, `room.js`, `editor.js` (merge editor), `compare.js`, `core.js`. |

## Data model

All IDs are UUID strings. Binaries are never stored in the database.

| Table | Notes |
|---|---|
| `users`, `owner_sessions` | Owners (Supabase or dev identity). Sessions hold only a keyed hash of the cookie token. |
| `projects` | Owned by one user. `slug` is the public URL id (10 chars, 50 bits). |
| `room_access_secrets` | PIN seed and version (see D7). |
| `project_members` | Contributors. `role` is fixed to `contributor` for V1; an organiser role can be added. `invite_only` is set once an invitation is redeemed. |
| `contributor_sessions` | Room sessions: `via` (pin/invite), `pin_version`, bound `member_id`, device label. |
| `invitation_tokens` | Hashed, per participant, single-use, 30-day expiry, revocable. |
| `pin_attempts` | Rate-limit log (IP stored as keyed hash). Pruned after two hours. |
| `assets` | **Immutable** file records (triggers block UPDATE/DELETE). `member_id` marks a contributor's own file; `photo_id` ties files to a photo for access checks. |
| `photos` | One immutable original each (trigger blocks changing it). `final_revision_id`. |
| `assignments` | History of who has each photo. A partial unique index allows one active assignment per photo in V1. Remove it to allow alternative edits later. |
| `merge_drafts` | An upload being prepared: alignment, warnings, sensitivity, current mask, status. |
| `masks` | Every saved mask is a new row and a new PNG. |
| `revisions` | **Immutable** submissions: upload, mask, composite, dimensions and formats, settings, warnings, validation, parent, note. Only `status` may change. |
| `approvals` | Append-only review decisions (approve, changes requested, final selected or cleared). |
| `activity_events` | Project timeline. |
| `jobs` | Queue. |
| `storage_integrations` | Per-project provider record (`local` today). |

## States

Assignment status (one active per photo): `assigned → editing → submitted → needs_changes / approved`. Uploading moves `assigned` or `needs_changes` to `editing`; a composite moves the photo to `submitted`; the owner's decision moves it on.

Photo state shown to the owner: `final` if a final revision is chosen, otherwise the active assignment's status, otherwise derived from the latest revision, otherwise `unassigned`. Progress counts come only from these states; nothing is entered by hand.

Downloads are recorded (`original_downloaded_at`) when the server serves the original. That proves the file was requested, not that it was opened, so it is shown as "original downloaded", never as "editing".

## Request flow: contributor submits

1. `POST /api/rooms/{slug}/photos/{id}/uploads`: validate the header (`images.probe`), store the upload (immutable asset), create a `merge_draft` (status `processing`), enqueue `draft.analyse`.
2. Worker: decode both images, orient them, convert the edit into the original's ICC profile, `merge.align`, `merge.difference_mask`, store the edit preview and mask; the draft becomes `ready` (or `blocked`).
3. Browser editor: loads base preview, edit preview and mask (all work size), and blends on a canvas. Brush strokes edit the mask locally.
4. `POST …/drafts/{id}/mask`: a new mask row and PNG. `POST …/drafts/{id}/submit`: draft becomes `submitting`, enqueue `draft.composite`.
5. Worker: full-resolution composite (lossless PNG with the original's ICC and EXIF), preview, new `revisions` row, assignment becomes `submitted`.
