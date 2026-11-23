# Handover — accurova-pixelrelay

_Last updated: 2026-11-24_

## Your next steps

The go-live checklist is in **[progress.md](progress.md)**: nine steps with exact commands. In short:

- [ ] [1. Apply the schema and give the app its login](progress.md#1-create-the-database-schema-and-the-apps-login-about-2-min)
- [ ] [2. Create the R2 bucket and token](progress.md#2-create-the-r2-bucket-and-its-token-about-3-min)
- [ ] [3. Write `secrets/pixelrelay.env`](progress.md#3-write-the-secrets-file-about-3-min)
- [ ] [4. Add the Supabase redirect URL](progress.md#4-supabase-sign-in-redirect-about-1-min)
- [ ] [5. Add the Cloudflare token to Actions, then merge `dev → main`](progress.md#5-let-github-deploy-then-merge-about-10-min-mostly-waiting)
- [ ] [6. Push the Worker secrets](progress.md#6-put-the-secrets-on-the-worker-about-1-min)
- [ ] [7. Check health, sign-in and the full journey](progress.md#7-check-it-works-about-10-min)
- [ ] [8. Billing alert](progress.md#8-guard-the-bill-about-2-min)
- [ ] [9. Pilot on real phones](progress.md#9-pilot)

## Current state

- Public repo https://github.com/TheBooleanJulian/accurova-pixelrelay. `feature/initial-build` and `feature/cloudflare-container` are merged into `dev`, and the `dev → main` PR is open. Merging it deploys, so wait for step 5.
- Production design ([D12](docs/decisions/README.md)): a Cloudflare Container behind a Worker, data in the shared Supabase project's schema `accurova_pixelrelay`, files in R2 `accurova-pixelrelay`, owner sign-in through Supabase Auth on the same project.
- Schema: [thebooleanjulian-db PR #12](https://github.com/TheBooleanJulian/thebooleanjulian-db/pull/12), merged to its `dev`, not applied.
- Checks: `ruff` is clean. 61 tests pass on SQLite and on Postgres 16 as the restricted role, with files through the R2 adapter. The browser journey passes on SQLite and on Postgres plus R2. CI also builds and boots the image and runs `wrangler deploy --dry-run`.
- Never run on Cloudflare, real R2 or live Supabase Auth yet. Step 7 is the first real test of all three.
- Dev copy: `C:\Users\Temp\dev\accurova-pixelrelay` (Python 3.12 venv at `.venv`; `npm install` done for the Worker library). The Drive folder is a source mirror without `.venv`, `node_modules`, `data` or `.git`.

## What was built

- Session 1 (2026-11-23): the app.
  - Room PIN, invitations, parallel assignments, Smart Merge with the brush editor, immutable revisions, review and export.
  - FastAPI with vanilla JS, in Unified UI.
- Session 2 (2026-11-23/24): production plumbing.
  - Two-backend `db.py` (SQLite / Postgres with advisory-lock transactions and `SKIP LOCKED` job claims).
  - R2 adapter (stdlib Signature V4, write-once PUT).
  - Worker and Container wrapper, deploy workflow, Postgres schema.
  - CI jobs for Postgres, R2 and the container image.

## Departures from the brief

Python/FastAPI/vanilla JS instead of Next.js/Tailwind/shadcn. Unified UI instead of the brief's palette and fonts. numpy+Pillow instead of OpenCV. Production now matches the brief's Supabase Postgres, but in the fleet's schema-per-app form and without RLS: the server is the only client. Reasons: [docs/decisions/README.md](docs/decisions/README.md).

## Open questions / not done

- Project and photo deletion and data retention for client photos: not built, because records are immutable by design. Decide before real client work at scale.
- Custom SMTP for Supabase Auth, if organisers other than you will sign in.
- Google Drive storage adapter; alternative edits UI; notifications through the bot fleet.

## Not attempted (needs your hands or explicit authorisation)

- Applying the schema or provisioning the role (needs `secrets/supabase.env`), creating R2 tokens, setting any secret, merging `dev → main` (that deploys), Supabase dashboard settings.
