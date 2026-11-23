# Progress: PixelRelay to production

_Last updated: 2026-11-24._ Target: **https://pixelrelay.accurova.com** on a Cloudflare Container (Workers Paid), with data in the shared Supabase project (schema `accurova_pixelrelay`) and files in R2 (bucket `accurova-pixelrelay`). The reasons are in [decision D12](docs/decisions/README.md).

Legend: `[x]` done · `[ ]` yours to do · **Claude can do** = say "go" and Claude runs it.

## Already done

- [x] Public repo: https://github.com/TheBooleanJulian/accurova-pixelrelay (`main`, `dev`, feature branches).
- [x] The app runs on Postgres or SQLite and on R2 or local disk, with the same code ([db.py](pixelrelay/db.py), [storage.py](pixelrelay/storage.py)).
- [x] Tests: all 61 pass on SQLite and on Postgres 16 as the restricted app role, with files going through the R2 adapter. The browser journey passes on Postgres plus the R2 adapter too. CI runs both setups.
- [x] Cloudflare wrapper: [cloudflare/worker.js](cloudflare/worker.js) and [wrangler.jsonc](wrangler.jsonc). Config checked up to the image build, which needs Docker (CI does that part).
- [x] Deploy workflow: [.github/workflows/deploy.yml](.github/workflows/deploy.yml) runs on every push to `main`.
- [x] Schema in thebooleanjulian-db: [PR #12](https://github.com/TheBooleanJulian/thebooleanjulian-db/pull/12), merged to `dev`, **not applied**.
- [x] `dev → main` PR for this repo is open. **Don't merge it until step 5.**

## Steps (in this order)

Run commands in PowerShell. `$S` is the secrets folder, which only you can read:

```powershell
$S = "D:\My Drive\TheBooleanJulian_GD\zeabur-migration\secrets"
$env:CLOUDFLARE_ACCOUNT_ID = "c580c4403524273d1bece674117472dd"   # the accurova account
```

### 1. Create the database schema and the app's login (about 2 min)

- [ ] Apply only PixelRelay's migration (other pending migrations stay pending):
  ```powershell
  cd C:\Users\Temp\dev\repos\thebooleanjulian-db
  git checkout dev; git pull
  node scripts/apply.mjs "$S\supabase.env" --only 20261123090000 --dry-run
  node scripts/apply.mjs "$S\supabase.env" --only 20261123090000
  ```
- [ ] Give the role a password and save its connection URL:
  ```powershell
  node scripts/provision.mjs "$S\supabase.env" accurova_pixelrelay "$S\db"
  ```
  Done when it prints `accurova_pixelrelay_app: login set` and `$S\db\accurova_pixelrelay.env` exists. The script also creates a Hyperdrive config called `accurova-pixelrelay-db`. PixelRelay doesn't use it (a container can't use Hyperdrive bindings) and it costs nothing, so leave it.

### 2. Create the R2 bucket and its token (about 3 min)

- [ ] Bucket (**Claude can do**): `npx --yes wrangler@4.152.0 r2 bucket create accurova-pixelrelay --location apac`
- [ ] Token. Only you can do this because it shows a secret:
  1. Open the Cloudflare dashboard, go to **R2 Object Storage → Manage API tokens**, and choose **Create Account API token**.
  2. Permission: **Object Read & Write**. Scope: **Apply to specific buckets only → `accurova-pixelrelay`**. TTL: forever.
  3. Copy the **Access Key ID** and **Secret Access Key** (they're shown once) into step 3.

### 3. Write the secrets file (about 3 min)

- [ ] Create `$S\pixelrelay.env` with a fresh `SECRET_KEY` and the database URL from step 1:
  ```powershell
  python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))" | Out-File -Encoding ascii "$S\pixelrelay.env"
  Select-String -Path "$S\db\accurova_pixelrelay.env" -Pattern '^DATABASE_URL=' | ForEach-Object { $_.Line } | Add-Content -Encoding ascii "$S\pixelrelay.env"
  notepad "$S\pixelrelay.env"
  ```
- [ ] In Notepad, add these lines below the two that are already there:
  ```
  R2_ACCESS_KEY_ID=<from step 2>
  R2_SECRET_ACCESS_KEY=<from step 2>
  SUPABASE_URL=https://xdzsgdhxjdmhrysxcspo.supabase.co
  SUPABASE_ANON_KEY=<Supabase → Project Settings → API Keys → the anon / publishable key>
  OWNER_EMAILS=<your email, comma-separate more organisers>
  ```
  Keep this file. `SECRET_KEY` derives every Room PIN: if it changes, every PIN changes and everyone is signed out.

### 4. Supabase sign-in redirect (about 1 min)

- [ ] Go to Supabase → **Authentication → URL Configuration → Redirect URLs** and add `https://pixelrelay.accurova.com/auth/callback`. Leave the Site URL as it is: other apps share this project.
- [ ] Check **Authentication → Sign In / Providers → Email** is enabled. It is by default.
- Note: Supabase's built-in email only reaches addresses in your Supabase organisation and allows a few emails per hour. That's fine for you as the only owner. Other organisers need custom SMTP (Authentication → Emails → SMTP) first.

### 5. Let GitHub deploy, then merge (about 10 min, mostly waiting)

- [ ] Make a Cloudflare API token for the deploy workflow:
  1. Open **My Profile → API Tokens → Create Token** and use the template **Edit Cloudflare Workers**.
  2. Account: the accurova account. Zone: `accurova.com`.
  3. Add the permission **Account → Containers → Edit**.

  If you still have the token you gave thebooleanjulian-llm, it has the same permissions and can be reused.
- [ ] Save it on this repo (it asks you to paste it): `gh secret set CLOUDFLARE_API_TOKEN -R TheBooleanJulian/accurova-pixelrelay`
- [ ] Merge the open **dev → main** PR once its CI is green. The **Deploy** workflow then builds the image and creates the Worker, the Container and the `pixelrelay.accurova.com` custom domain. Watch it with `gh run watch -R TheBooleanJulian/accurova-pixelrelay`. The first image push takes a few minutes.
- Until step 6 is done the site answers 503 ("starting up"): the app refuses to start without its secrets. That's expected.

### 6. Put the secrets on the Worker (about 1 min)

- [ ] From the migration folder (values are never printed):
  ```powershell
  cd "D:\My Drive\TheBooleanJulian_GD\zeabur-migration"
  python -I push_secrets.py --worker accurova-pixelrelay --env pixelrelay.env --keys SECRET_KEY,DATABASE_URL,R2_ACCESS_KEY_ID,R2_SECRET_ACCESS_KEY,SUPABASE_URL,SUPABASE_ANON_KEY,OWNER_EMAILS --dry-run
  python -I push_secrets.py --worker accurova-pixelrelay --env pixelrelay.env --keys SECRET_KEY,DATABASE_URL,R2_ACCESS_KEY_ID,R2_SECRET_ACCESS_KEY,SUPABASE_URL,SUPABASE_ANON_KEY,OWNER_EMAILS
  ```
- [ ] Restart the container so it picks them up: GitHub → Actions → **Deploy** → **Run workflow** on `main` (**Claude can do**: `gh workflow run deploy.yml -R TheBooleanJulian/accurova-pixelrelay --ref main`).

### 7. Check it works (about 10 min)

- [ ] Health (**Claude can do**): open https://pixelrelay.accurova.com/healthz. Done when it shows `"ok": true, "database": "postgres", "storage": "r2"`. The first call after a sleep can take 10–30 s, or answer 503 once; refresh.
- [ ] Sign in at https://pixelrelay.accurova.com/login with your email, then open the magic link. This is the first time sign-in runs against live Supabase.
- [ ] Create a test project with 2–3 photos, opt yourself in as a participant, and publish the split.
- [ ] On your phone, open the room link, enter the PIN, pick the name, download, edit in any app, upload, then brush and submit.
- [ ] Back as owner: approve, choose final, export the ZIP and open it.
- [ ] Optional: R2 → `accurova-pixelrelay` should now list files under `projects/…`.

### 8. Guard the bill (about 2 min)

- [ ] Cloudflare → **Notifications → Add → Usage Based Billing**: alert on Containers usage. Expected cost: cents per pilot. The container runs only while people use it, and Workers Paid includes 25 GiB-hours and 375 vCPU-minutes a month.

### 9. Pilot

- [ ] Run one real group shoot through it, on at least one iPhone (Safari) and one Android phone.
- [ ] Note any Smart Merge result that looks wrong: what was edited, in which app, and the export settings. Those cases go into tuning `SENSITIVITY` in [merge.py](pixelrelay/merge.py).

## If something goes wrong

| Symptom | Look at | Likely fix |
|---|---|---|
| Deploy workflow fails at "Check the Cloudflare token" | Actions log | Step 5: the Actions secret is missing |
| Deploy fails with an auth error on the custom domain | Actions log | Add **Zone → DNS → Edit** for `accurova.com` to the token |
| Site stays on 503 "starting up" | `npx --yes wrangler@4.152.0 tail accurova-pixelrelay`, then reload the site | The log shows what the app refused. Usually a missing secret (step 6) or the schema not applied (step 1) |
| `/healthz` 500, or log says `schema … has no PixelRelay tables` | step 1 | Run `apply.mjs --only 20261123090000` |
| Log says `password authentication failed` | step 1 | Rerun `provision.mjs` (new password), then copy `DATABASE_URL` again (steps 3 and 6) |
| Uploads fail, log says `R2 PUT answered 403` | step 2 | Token not scoped to `accurova-pixelrelay`, or keys pasted wrongly |
| Magic link says redirect not allowed | step 4 | Redirect URL typo |
| Sign-in says "not allowed" | `OWNER_EMAILS` | Add the email and repeat step 6 |

**Rollback:** Workers → accurova-pixelrelay → Deployments → promote the previous version. To take the site offline, remove `routes` from `wrangler.jsonc` and merge. Data stays in Supabase and R2.

## Not done (later, only if needed)

- Data retention and deletion policy for client photos (records are immutable by design).
- Custom SMTP so other organisers can sign in.
- Google Drive storage adapter; notifications through the bot fleet.
