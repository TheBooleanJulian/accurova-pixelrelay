# Handover — accurova-pixelrelay

_Last updated: 2026-11-23_

## Your next steps

### Decisions (need your call)

- [ ] **GitHub repo: public or private?** Nothing is pushed yet. The local repo has `main`, `dev` and `feature/initial-build` (merged into `dev`). Once you decide, Claude can create `TheBooleanJulian/accurova-pixelrelay`, push all three branches and open the `dev → main` PR. Note: the synthetic test images are generated at test time; no client photos are in the repo.
- [ ] **Hosting.** PixelRelay needs a long-running Python process and a persistent disk (SQLite plus images). Workers Free can't run it (10 ms CPU), and Vercel has no persistent disk. Options:
  1. **VPS or home server + Docker + Cloudflare Tunnel** at `pixelrelay.accurova.com` (recommended for the pilot: the [Dockerfile](Dockerfile) works as is; back up the `/data` volume).
  2. **Cloudflare Container** (needs Workers Paid; the $5/mo was approved for FreeLLMAPI only). Container disks are ephemeral, so this also needs the R2 storage adapter and Supabase Postgres ([decisions D2/D5](docs/decisions/README.md)). That is a bigger change.
- [ ] **Which Supabase project holds owner Auth:** the shared `thebooleanjulian` project ([Auth settings](https://supabase.com/dashboard/project/xdzsgdhxjdmhrysxcspo/auth/providers)) or a separate one. Only Auth is used; data stays in SQLite.

### Setup once hosting is chosen

- [ ] Generate the secret and store it in the host's secret store (never in git): `python -c "import secrets; print(secrets.token_urlsafe(48))"` → `SECRET_KEY`
- [ ] Supabase → Authentication → URL configuration: add `https://pixelrelay.accurova.com/auth/callback` to the redirect URLs.
- [ ] Set env from [.env.example](.env.example): `APP_ENV=production`, `BASE_URL=https://pixelrelay.accurova.com`, `AUTH_MODE=supabase`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `OWNER_EMAILS=<your email>`, `SECRET_KEY`.
- [ ] Sign in once with the magic link (this path has not been run against a live Supabase project yet), then create a test project.

### Pilot

- [ ] Run one real group shoot through it. Try it on at least one iPhone (Safari) and one Android phone. Note any Smart Merge cases that look wrong (export settings, app used); those go into tuning `SENSITIVITY` in `pixelrelay/merge.py`.

## Current state

- Built from the brief ([docs/product-requirements.md](docs/product-requirements.md)) through milestone 5, except the real-world pilot and deployment. Details and the acceptance-criteria map: [docs/implementation-status.md](docs/implementation-status.md).
- Checks: `ruff check`, `ruff format --check`, `pytest` (50 passed), `node tests/e2e/journey.mjs` (passes in headless Edge; owner desktop plus contributor at phone size).
- Dev copy: `C:\Users\Temp\dev\accurova-pixelrelay` (venv at `.venv`, Python 3.12). The Drive folder is a source mirror without `.venv`, `data` or `.git`.

## What was built (this session)

- FastAPI + SQLite app, vanilla JS front end with no build step, in Julian Unified UI (tokens copied from `thebooleanjulian-branding`).
- Room PIN derived from a secret seed (not stored), progressive rate limiting without lockout, personal invitations that lock a name, remembered and revocable devices.
- Parallel assignments: balanced deterministic split with preview, adjust and publish; reassignment keeps history.
- Immutable originals, uploads, masks, composites and revisions (DB triggers plus write-once storage).
- Smart Merge: numpy/Pillow alignment (scale, shift, rotation, crop search), two-scale ΔE + SSIM detection, warnings, a browser brush editor, manual line-up, and a lossless full-res composite.
- Review: approve, ask for changes, final selection, ZIP export.

## Departures from the brief

Python/FastAPI/vanilla JS instead of Next.js/Tailwind/shadcn; SQLite instead of Supabase Postgres (Auth still Supabase); Unified UI instead of the brief's palette and fonts; numpy+Pillow instead of OpenCV. Reasons: [docs/decisions/README.md](docs/decisions/README.md).

## Open questions / not done

- Google Drive storage adapter: interface ready, adapter not built (do it only if the pilot asks for it).
- Project and photo deletion and data retention for client photos: not built, because records are immutable by design. This needs a policy decision before real client work at scale.
- Alternative edits (several people editing one photo): in the data model, not in the UI.
- Notifications (Telegram via the bot fleet) for "assigned" or "changes requested": not built.

## Not attempted (needs explicit authorisation)

- Creating the GitHub repo, pushing, any deployment, DNS, Supabase configuration, secrets.
