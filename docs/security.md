# Security

## Threat model in one paragraph

Projects hold private photographs of real people. Outsiders must not see them. Contributors must see only their own assignments and edits, and must not change settings or approve their own work. The Room PIN is a convenience secret for a group chat, **not** strong authentication: six digits is about 20 bits. PixelRelay makes guessing it slow and visible, ties every action to a participant, and gives the owner tools to rotate the PIN and sign devices out. Owners sign in with Supabase Auth (email magic link), which is stronger than the PIN.

## Controls

| Area | Control | Where |
|---|---|---|
| Owner sign-in | Supabase magic link; the server verifies the access token with Supabase, then issues its **own** server-side session. Optional `OWNER_EMAILS` allow-list. Dev sign-in refused when `APP_ENV=production`. | `auth.py`, `api_auth.py`, `config.py` |
| Sessions | Random 256-bit tokens in `HttpOnly`, `SameSite=Lax` cookies (`Secure` on https). The database stores only `HMAC(SECRET_KEY, token)`. Sliding expiry: owners 14 days, rooms 60 days. Logout, per-device revoke and revoke-all. Nothing secret in `localStorage`. | `access.py`, `web.py` |
| Room PIN | 6 digits derived from a 256-bit random seed and `SECRET_KEY` (never stored). Compared in constant time. Rotation invalidates PIN-made sessions; invitation sessions survive. | `security.py`, `services.rotate_pin` |
| Brute force | Per (project, IP) and per IP: 5 free tries per 15 minutes, then waits of 2, 4, 8 … seconds after each failure, capped at 5 minutes. The window decays, so there is **no permanent lockout**. Per project: 60+ failures in an hour flags the room (owner sees "rotate the PIN") and spaces all attempts 3 seconds apart. IPs are stored as keyed hashes. | `ratelimit.py` |
| Identity | After the PIN, people pick their name. The owner is never on that list. A personal invitation (single-use, 30-day, revocable, token in the URL **fragment** so it never reaches server logs) binds a device to one participant and then locks that name against PIN selection. | `services.bind_member`, `redeem_invite` |
| Authorisation | Every route resolves the caller through `access.py`. Owners reach only projects they own; others get 404. Contributors reach only photos they are or were assigned to (404 if never), upload only to active assignments (403 otherwise), and read only their own uploads, masks and composites. Exports are owner-only. | `access.py`, all routes |
| Files | Served only via `/api/files/{id}` after `access.can_read_asset`. Storage keys are server-generated, path traversal is rejected, and keys are write-once. | `app.py`, `storage.py` |
| Uploads | Size cap (`MAX_UPLOAD_MB`), megapixel cap (decompression-bomb guard), real decoding with Pillow (JPEG/PNG/WebP/TIFF only; SVG and HTML rejected whatever they're called), stored under the verified type. | `images.probe` |
| CSRF | State-changing API calls must send `X-PixelRelay: 1` (a cross-site form cannot) and, if present, a same-origin `Origin`. Cookies are `SameSite=Lax`. | `app.py` middleware |
| Headers | CSP (`script-src 'self'`, no inline scripts), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`, `no-store` on API responses. | `app.py` |
| Integrity | Originals, uploads, masks, composites and revisions are immutable: database triggers on both SQLite and Postgres, plus write-once storage (a hard link on disk, `If-None-Match: *` on R2). Approvals are append-only. | `migrations/` |
| Database (production) | Own schema `accurova_pixelrelay` in the shared Supabase project, not exposed over the Data API. The app's role `accurova_pixelrelay_app` has row access to its own tables only: no DDL, no other schema, and it can't write its migration record. CI runs every test as that role. TLS is required to the pooler. | thebooleanjulian-db migration, `db.py` |
| Files (production) | One private R2 bucket. The container uses an R2 token scoped to that bucket (Object Read & Write). There is no public bucket URL: every file still goes through `/api/files` and its access check. | `storage.R2Storage`, `wrangler.jsonc` |
| Secrets (production) | Worker secrets only (`SECRET_KEY`, `DATABASE_URL`, R2 keys, Supabase anon key, `OWNER_EMAILS`), passed to the container as environment variables when it starts. None are in git, and the log filter redacts database URLs and S3 credentials. | `cloudflare/worker.js` |
| Logging | No PINs, tokens or cookies are logged (PINs travel only in POST bodies, invitation tokens only in fragments). A redacting log filter is a second line of defence. Supabase error bodies are never echoed. | `logging_setup.py`, `auth.py` |
| Validation | Pydantic models with length and range limits on every JSON body; dates parsed strictly. | `api_*.py` |

## Known limits (be honest with users)

- Anyone with the link **and** the PIN can join and pick any name not claimed by invitation. Use personal invitation links when attribution matters.
- The PIN cannot be stronger than six digits. If the room is flagged, or the PIN leaks beyond the group, make a new one.
- `SECRET_KEY` is the root secret: it derives PINs and keys every token hash. Keep it only in the host's secret store. Rotating it changes all PINs and signs everyone out.
- Rate limiting counts attempts in the database, so it holds across restarts. Behind a proxy, set `TRUST_PROXY=1` so client IPs are read correctly, but only if the proxy overwrites those headers. On Cloudflare it reads `CF-Connecting-IP`, which Cloudflare sets itself.
- Production readiness still needs a pilot and a review of the deployment (TLS, backups, host hardening). See [implementation-status.md](implementation-status.md).
