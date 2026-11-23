# PixelRelay by Accurova

**Every edit moves the picture forward.** Collaborate. Create. Preserve.

PixelRelay coordinates group photo editing. A photographer or organiser shares out the photos from a group shoot. Each contributor (often a cosplayer) downloads their originals, edits them in any app, and uploads the export. **Smart Merge** works out what they changed and puts those changes back onto the full-resolution original. Every original, upload, mask and composite is kept. The owner reviews, approves, picks final versions and exports them.

Contributors don't need an account. They open the project link, enter the six-digit **Room PIN** and pick their name.

| Owner overview | Review and compare | Contributor on a phone | Smart Merge editor |
|---|---|---|---|
| ![Owner overview](docs/screenshots/owner-overview.png) | ![Review](docs/screenshots/owner-review.png) | ![Contributor home](docs/screenshots/contributor-home.png) | ![Editor](docs/screenshots/contributor-editor-mobile.png) |

## Quick start

Needs Python 3.12+.

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt      # Windows; on macOS/Linux use .venv/bin/
.venv/Scripts/python -m pixelrelay                     # http://127.0.0.1:8000
```

With no `.env`, it runs in development mode: **dev sign-in** (any email, no check; refused in production), data in `./data`, and the image worker in a background thread. Open `/login`, create a project, add photos, then open the room link in a private window to act as a contributor.

## The journey

1. **Owner:** sign in, create a project (name, optional dates, participant names), upload originals.
2. **Owner:** opt people in (or let them choose how many they can edit), **preview an even split**, adjust, **publish**. Share the link and Room PIN in the group chat.
3. **Contributor:** open link, enter PIN, pick name, see only **their** photos.
4. **Contributor:** download the original, edit anywhere, upload the export.
5. **Smart Merge** aligns the export to the original, detects changes (Low/Medium/High), and opens the editor. The contributor refines the mask with **Add edit / Keep original** brushes, checks Result/Original/Your edit, and submits.
6. The server composites **at the original's full resolution**; untouched areas keep the original pixels exactly.
7. **Owner:** compare original and edit, **approve** or **ask for changes** (the contributor sees the note), **choose the final version**, **export finals** as a ZIP.

Contributors can reopen any of their submissions and create a new edit; nothing is overwritten.

## Architecture

```
Browser (vanilla JS modules, no build step)
  owner app /owner · contributor app /r/{slug} · merge editor (canvas, work size <= 2048 px)
        │ JSON + multipart, cookie sessions, X-PixelRelay CSRF header
        ▼
FastAPI (pixelrelay/)
  access.py     centralised authorisation (owner sessions, room sessions, file access)
  api_*.py      routes: auth, owner, room
  services.py   domain operations and state transitions
  jobs.py       durable SQLite job queue + handlers (worker thread or separate process)
  merge.py      Smart Merge: alignment, difference mask, full-res compositing (numpy + Pillow)
  storage.py    write-once storage behind a provider interface (local disk today)
        │
SQLite (data/pixelrelay.sqlite3)  ·  files (data/files/...)
```

More: [architecture](docs/architecture.md) · [security](docs/security.md) · [image processing](docs/image-processing.md) · [decisions](docs/decisions/README.md) · [status and limitations](docs/implementation-status.md) · [original brief](docs/product-requirements.md).

## Configuration

Everything is in [.env.example](.env.example). The essentials:

| Variable | Development | Production |
|---|---|---|
| `APP_ENV` | `development` | `production` (enforces the checks below) |
| `BASE_URL` | `http://localhost:8000` | `https://…` (required) |
| `SECRET_KEY` | auto-generated into `DATA_DIR/.dev-secret` | 48+ random chars (required) |
| `AUTH_MODE` | `dev` | `supabase` + `SUPABASE_URL`, `SUPABASE_ANON_KEY` |
| `OWNER_EMAILS` | empty (anyone) | the organisers' emails |
| `DATA_DIR` | `data` | a persistent, backed-up volume |
| `WORKER_MODE` | `thread` | `thread`, or `off` plus `python -m pixelrelay.worker` |

### Supabase Auth (owner sign-in)

1. In the Supabase project: Authentication → Providers → Email: enable, magic link on.
2. Authentication → URL configuration: add `https://<your host>/auth/callback` to the redirect allow-list.
3. Set `AUTH_MODE=supabase`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `OWNER_EMAILS`.

Only Supabase **Auth** is used. PixelRelay's data stays in its own SQLite database (see [decision D2](docs/decisions/README.md)).

## Tests and checks

```bash
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check .
.venv/Scripts/python -m pytest -q            # 50 tests: image processing, assignment, API integration
node tests/e2e/journey.mjs                   # whole journey in headless Edge/Chrome (owner desktop + contributor phone)
SCREENSHOTS=docs/screenshots node tests/e2e/journey.mjs   # refresh the README screenshots
```

| Suite | Covers |
|---|---|
| `tests/test_merge.py` | low-res JPEG export → local mask, no false positives, canvas size kept, untouched pixels identical, sensitivity order, rotated / cropped / shifted / unrelated / reshaped exports, EXIF orientation, bad files |
| `tests/test_distribution.py` | balanced split, capacity, opt-in, existing load, priority, determinism |
| `tests/test_api.py` | PIN creation and storage, join without email, remembered and revoked devices, PIN rotation, single-use invitations, rate limiting, CSRF, owner-only functions, file authorisation, parallel editing, reassignment history, full revision journey, immutability (DB triggers + write-once storage), blocked alignment, progress |
| `tests/e2e/journey.mjs` | the main journey in a real browser, with zero console errors |

CI (`.github/workflows/ci.yml`) runs lint, pytest and the browser journey.

## Deployment

Not deployed yet; hosting is an open decision (see [handover.md](handover.md)). PixelRelay needs a long-running Python process with a persistent disk, because it decodes and composites full-resolution images. That rules out Workers Free (10 ms CPU limit).

```bash
docker build -t accurova-pixelrelay .
docker run -p 8000:8000 -v pixelrelay-data:/data --env-file .env accurova-pixelrelay
```

Back up the `/data` volume: it holds the database and every image.

## Project layout

```
pixelrelay/           app package (see Architecture)
  migrations/         SQL migrations, applied automatically at start-up
  web/                HTML shells + static/css (Unified UI tokens) + static/js
tests/                pytest suites, synthetic fixtures, e2e/ browser journey
docs/                 architecture, security, image processing, decisions, status, screenshots
```
