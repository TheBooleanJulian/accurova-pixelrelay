# Decisions

Where PixelRelay departs from the build brief ([product-requirements.md](../product-requirements.md)), and why. The brief's own rule applies: *adjust if a simpler architecture is more maintainable*. Julian's fleet rules (`AGENTS.md`) take priority over the brief's "preferred starting point".

## D1 — Python + FastAPI + vanilla JS, not Next.js + Tailwind + shadcn/ui

The brief suggested Next.js/TypeScript/Tailwind/shadcn on Vercel. The fleet defaults are Python (stdlib-first), FastAPI and no frontend framework or build step unless needed. Image processing needs Python (numpy/Pillow) anyway, so a Next.js app would add a second language, a bundler and a separate worker service for one person to maintain. The UI is plain ES modules with no build step. The interactive editor is about 600 lines of canvas code that a framework would not shorten. Server-side validation uses Pydantic (bundled with FastAPI) in place of Zod.

## D2 — SQLite for data, Supabase only for owner sign-in

The brief suggested Supabase Postgres with RLS. Image files live on the server's disk in V1 (see D5), and one process serves all requests and runs the jobs. A database on the same volume is the simplest consistent unit: one backup, no network hop, nothing extra to run. SQLite is the fleet default. The schema uses portable SQL (TEXT UUIDs, ISO timestamps, partial indexes) and every query lives in `services.py`, `access.py` or the routes, so moving to Supabase Postgres later is mechanical. **Revisit together with D5** if PixelRelay moves to Supabase Storage or needs several server instances. Supabase **Auth** is still used for owners, as the brief asked.

## D3 — Julian Unified UI instead of the brief's navy/cyan palette and Manrope/Inter

Unified UI is the hard global design default and new work may not invent colours or fonts (`AGENTS.md` §4 and §15.9). The brief's direction (dark photographic interface, restrained cyan, photography as the focus) already matches Unified UI: dark default, cyan for actions, magenta used sparingly. The only magenta element is the "your edit is used" highlight. Tokens are copied from `thebooleanjulian-branding/dist/tokens.css`. Fonts are Chakra Petch, IBM Plex Sans and JetBrains Mono. The logo is a placeholder pixel-cluster mark and is easy to replace.

## D4 — numpy + Pillow, no OpenCV or scikit-image

What Smart Merge needs fits in a few hundred readable lines:

- normalised cross-correlation via FFT for alignment and crop search
- box-filter SSIM
- Lab ΔE colour difference
- separable min/max morphology

Pillow covers decoding, EXIF orientation, ICC conversion, resizing and alpha compositing (`Image.composite`, which keeps mask-0 pixels exact). OpenCV would add a large binary dependency with platform quirks for features V1 doesn't use, such as feature-based homography. If more advanced warping is ever needed, OpenCV is the first candidate.

## D5 — Local-disk storage behind a provider interface

`storage.StorageProvider` has four methods. `LocalDiskStorage` is write-once: it refuses to overwrite a key. The database stores only `(provider, key)`. Google Drive or Supabase Storage adapters can be added without schema changes. Drive OAuth was deliberately not allowed to block the first slice, as the brief advised.

## D6 — Background jobs in a SQLite queue, worker thread by default

Jobs are rows, claimed atomically with a lease, retried with backoff, and marked failed with a user-facing message. The web process runs a worker thread (`WORKER_MODE=thread`). For heavier use, run `python -m pixelrelay.worker` separately with `WORKER_MODE=off`. Requests never block on image processing; the browser polls the draft status.

## D7 — Room PIN derived from a secret seed, never stored

The owner must see the PIN at any time, but the brief says to store only keyed hashes. So the PIN is *derived*: `HMAC(SECRET_KEY, project_id || seed) mod 10^6`, where `seed` is 256 random bits in the database. The database alone reveals nothing. The server can show the owner the PIN and check guesses in constant time. Rotating replaces the seed and bumps a version, which invalidates PIN-made sessions.

## D8 — Masks stored at "work size" (long edge ≤ 2048 px)

The mask is soft by nature: feathered edges, and brush work at screen resolution. It is stored as an 8-bit PNG at the same size as the editor's previews. Full-resolution compositing upsamples it bilinearly. This keeps the browser editor fast on phones (about 3 MP to repaint, not 24 MP). Difference detection runs on a 1024 px grid for speed (about 2 s for a 24 MP pair), and its soft mask is upsampled to work size.

## D9 — Composite masters are lossless PNG

"Untouched regions keep the original pixels" can only be true if the stored composite is lossless. Re-encoding as JPEG would change every pixel. Composites are PNG with the original's ICC profile and EXIF (orientation reset). The export offers JPEG quality 95 (smaller, for sharing) or PNG.

## D10 — Formatter and linter: ruff (dev only)

The brief asks for linting and formatting. No fleet standard exists yet (`AGENTS.md` §17), so this is a repo-level choice, not a fleet convention: ruff, configured in `pyproject.toml`, dev dependency only.

## D11 — End-to-end tests drive the browser over raw CDP

Same approach as accurova-graphene: a dependency-free Node script (`tests/e2e/cdp.mjs`) drives headless Edge or Chrome, with no Playwright install. It uses separate browser contexts, so the owner and the contributor have separate cookies.
