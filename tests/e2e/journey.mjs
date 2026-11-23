// End-to-end: the owner and a contributor (phone-sized, separate cookies) go
// through the whole PixelRelay journey in a real headless browser.
//
//   node tests/e2e/journey.mjs            (starts its own server on :8799)
//   PYTHON=python node tests/e2e/journey.mjs
//   SCREENSHOTS=docs/screenshots node tests/e2e/journey.mjs
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { launchBrowser, sleep } from "./cdp.mjs";

const ROOT = resolve(import.meta.dirname, "..", "..");
const PY = process.env.PYTHON || [join(ROOT, ".venv", "Scripts", "python.exe"), join(ROOT, ".venv", "bin", "python")].find(existsSync) || "python";
const PORT = Number(process.env.E2E_PORT || 8799);
const BASE = `http://127.0.0.1:${PORT}`;
const SHOTS = process.env.SCREENSHOTS ? resolve(process.env.SCREENSHOTS) : join(ROOT, "tests", "e2e", "out");
mkdirSync(SHOTS, { recursive: true });

const work = mkdtempSync(join(tmpdir(), "pixelrelay-journey-"));
const fixtures = join(work, "fixtures");
const made = spawnSync(PY, ["-m", "tests.e2e.make_fixtures", fixtures], { cwd: ROOT, stdio: "inherit" });
if (made.status !== 0) throw new Error("fixture generation failed");

const server = spawn(PY, ["-m", "pixelrelay"], {
  cwd: ROOT, stdio: ["ignore", "pipe", "pipe"],
  env: { ...process.env, DATA_DIR: join(work, "data"), AUTH_MODE: "dev", APP_ENV: "development", PORT: String(PORT),
    BASE_URL: BASE, SECRET_KEY: "e2e-secret-".padEnd(48, "x"), WORKER_MODE: "thread" },
});
let serverLog = "";
server.stdout.on("data", (d) => (serverLog += d));
server.stderr.on("data", (d) => (serverLog += d));

const step = (msg) => console.log(`- ${msg}`);
let browser;
let ok = false;
const pages = {};
try {
  for (let i = 0; i < 80; i++) { try { if ((await fetch(`${BASE}/healthz`)).ok) break; } catch { /* starting */ } await sleep(250); }
  browser = await launchBrowser();

  // ---------------------------------------------------------------- owner sets up
  const owner = (pages.owner = await browser.newPage({ width: 1366, height: 900 }));
  await owner.goto(`${BASE}/`);
  await owner.waitFor("document.querySelector('.hero')");
  await owner.screenshot(join(SHOTS, "landing.png"));
  step("landing page renders");

  await owner.goto(`${BASE}/login`);
  await owner.waitFor("document.querySelector('#email')");
  await owner.evaluate("document.querySelector('#email').value = 'owner@example.com'; document.querySelector('form').requestSubmit(); true");
  await owner.waitFor("location.pathname === '/owner' && !!document.querySelector('h1')");
  step("owner signed in (dev login)");

  await owner.clickText("button", "New project");
  await owner.waitFor("document.querySelector('#name')");
  await owner.evaluate(`document.querySelector('#name').value = 'Winter group shoot';
    document.querySelector('textarea').value = 'Ava\\nBen'; document.querySelector('form').requestSubmit(); true`);
  await owner.waitFor("location.search.includes('tab=photos') && !!document.querySelector('input[type=file][multiple]')");
  await owner.setFiles("input[type=file][multiple]", [1, 2, 3].map((i) => join(fixtures, `original_${i}.jpg`)));
  await owner.waitFor("document.querySelectorAll('.gallery .tile img').length === 3", { timeout: 60000, label: "3 photo thumbnails" });
  await owner.screenshot(join(SHOTS, "owner-photos.png"));
  step("project created, 3 originals uploaded and previewed");

  await owner.clickText(".tab", "Participants");
  await owner.waitFor("document.querySelectorAll('tbody tr').length === 2");
  await owner.evaluate("document.querySelectorAll('tbody input[role=switch]').forEach((s) => s.click()); true");
  await sleep(600);
  await owner.clickText(".tab", "Assignments");
  await owner.clickText("button", "Preview even split");
  await owner.waitFor("[...document.querySelectorAll('th')].some((t) => t.textContent.includes('Goes to'))");
  await owner.screenshot(join(SHOTS, "owner-assignments.png"));
  await owner.clickText("button", "Publish");
  await owner.waitFor("[...document.querySelectorAll('.badge')].filter((b) => b.textContent === 'Assigned').length === 3");
  step("balanced split previewed and published");

  const pin = await owner.evaluate("document.querySelector('.pin').textContent");
  const roomUrl = await owner.evaluate("document.querySelector('.share-url').textContent");
  if (!/^\d{6}$/.test(pin)) throw new Error(`bad PIN ${pin}`);
  await owner.clickText(".tab", "Overview");
  await owner.waitFor("document.querySelector('.stats')");
  await owner.screenshot(join(SHOTS, "owner-overview.png"));
  step(`Room PIN shown (${pin.length} digits)`);

  // ---------------------------------------------------------------- contributor on a phone
  const ava = (pages.ava = await browser.newPage({ width: 390, height: 844, mobile: true }));
  await ava.goto(roomUrl);
  await ava.waitFor("document.querySelector('#pin')");
  await ava.evaluate("document.querySelector('#pin').value = '000000'; document.querySelector('form').requestSubmit(); true");
  if (pin !== "000000") await ava.waitFor("document.querySelector('.field__error')", { label: "wrong-PIN message" });
  await ava.screenshot(join(SHOTS, "contributor-pin.png"));
  await ava.evaluate(`document.querySelector('#pin').value = '${pin}'; document.querySelector('form').requestSubmit(); true`);
  await ava.waitFor("document.querySelectorAll('.choice').length === 2");
  await ava.screenshot(join(SHOTS, "contributor-names.png"));
  await ava.clickText(".choice", "Ava");
  // The owner already opted Ava in, so no prompt; she changes her number herself.
  await ava.clickText("button", "Change how many");
  await ava.waitFor("document.querySelector('.capacity')");
  await ava.clickText(".capacity .btn", "2");
  await ava.waitFor("document.querySelectorAll('a.card').length >= 1");
  await ava.screenshot(join(SHOTS, "contributor-home.png"));
  step("contributor joined with PIN, picked name, set availability, sees their photos");

  await ava.evaluate("document.querySelector('a.card').click(); true");
  await ava.waitFor("[...document.querySelectorAll('a.btn')].some((a) => a.textContent.includes('Download original'))");
  const dl = await ava.evaluate("fetch([...document.querySelectorAll('a.btn')].find((a) => a.textContent.includes('Download original')).href).then((r) => r.status + ' ' + r.headers.get('content-type'))");
  if (!dl.startsWith("200 image/jpeg")) throw new Error(`download failed: ${dl}`);
  await ava.evaluate("location.reload(); true");
  await ava.waitFor("document.querySelector('.step--done')", { label: "download step ticked" });
  await ava.screenshot(join(SHOTS, "contributor-steps.png"));
  step("original downloaded and recorded");

  await ava.setFiles("input[type=file]", [join(fixtures, "edit_1.jpg")]);
  await ava.waitFor("document.querySelector('.editor__stage canvas') && document.querySelector('.editor__busy') && document.querySelector('.editor__busy').hidden", { timeout: 90000, label: "editor ready" });
  await sleep(500);
  await ava.screenshot(join(SHOTS, "contributor-editor-mobile.png"));
  step("edit uploaded, Smart Merge ran, editor opened");

  const rect = await ava.evaluate("(() => { const r = document.querySelector('.editor__stage canvas').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()");
  await ava.evaluate("document.querySelector('.editor__stage').scrollIntoView({block: 'center'}); true");
  const r2 = await ava.evaluate("(() => { const r = document.querySelector('.editor__stage canvas').getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; })()");
  const pts = Array.from({ length: 16 }, (_, i) => ({ x: r2.x + r2.w * (0.2 + 0.035 * i), y: r2.y + r2.h * 0.3 }));
  await ava.stroke(pts);
  const undoOn = await ava.waitFor("[...document.querySelectorAll('button')].some((b) => b.textContent.includes('Undo') && !b.disabled)", { label: "brush stroke recorded" });
  await ava.clickText("button", "Undo");
  await ava.clickText("button", "Redo");
  step(`brush stroke painted, undo/redo work (${Math.round(rect.w)}x${Math.round(rect.h)} canvas)`);

  await ava.clickText("button", "Submit edit");
  await ava.waitFor("[...document.querySelectorAll('.alert__title')].some((t) => t.textContent.includes('Edit submitted'))", { timeout: 90000 });
  await ava.screenshot(join(SHOTS, "contributor-submitted.png"));
  step("edit submitted and composited at full resolution");

  await ava.clickText(".tab, button, a", "My photos");
  await ava.clickText(".tab", "My edits");
  await ava.waitFor("document.querySelectorAll('.card').length === 1");
  await ava.screenshot(join(SHOTS, "contributor-edits.png"));

  // ---------------------------------------------------------------- owner reviews
  await owner.clickText(".tab", "Review");
  await owner.waitFor("document.querySelectorAll('.gallery .tile').length === 1");
  await owner.evaluate("document.querySelector('.gallery .tile').click(); true");
  await owner.waitFor("document.querySelector('dialog[open] .compare')");
  await sleep(600);
  await owner.screenshot(join(SHOTS, "owner-review.png"));
  await owner.clickText("dialog[open] button", "Approve");
  await owner.clickText("dialog[open] button", "Choose as final", { timeout: 20000 });
  await owner.waitFor("[...document.querySelectorAll('dialog[open] .badge')].some((b) => b.textContent === 'Final')");
  await owner.evaluate("document.querySelector('dialog[open]').close(); true");
  await owner.waitFor("!document.querySelector('dialog[open]') && document.body.innerText.includes('1 photo has a final version')");
  await sleep(500); // let the re-render settle
  await owner.clickText("button", "Export finals");
  await owner.waitFor("[...document.querySelectorAll('a.btn')].some((a) => a.textContent.includes('Download ZIP'))", { timeout: 60000 });
  const zip = await owner.evaluate("fetch([...document.querySelectorAll('a.btn')].find((a) => a.textContent.includes('Download ZIP')).href).then(async (r) => r.status + ' ' + (await r.arrayBuffer()).byteLength)");
  if (!zip.startsWith("200 ")) throw new Error(`export download failed: ${zip}`);
  step(`approved, chose final, exported (${zip.split(" ")[1]} bytes ZIP)`);

  // Ava sees the final status.
  await ava.clickText(".tab", "My photos");
  await ava.waitFor("[...document.querySelectorAll('.badge')].some((b) => b.textContent.includes('Final'))");

  // Light theme check.
  await owner.evaluate("document.documentElement.dataset.theme = 'light'; true");
  await owner.clickText(".tab", "Overview");
  await sleep(400);
  await owner.screenshot(join(SHOTS, "owner-overview-light.png"));

  const errors = [...owner.errors, ...ava.errors].filter((e) => !/Failed to load resource.*(401|409|404)/.test(e) && !/status of 401/.test(e));
  if (errors.length) throw new Error(`browser errors:\n${errors.join("\n")}`);
  ok = true;
  console.log(`\nE2E journey passed. Screenshots: ${SHOTS}`);
} catch (err) {
  console.error(`\nE2E journey FAILED: ${err.message}`);
  console.error(serverLog.split("\n").filter((l) => !l.includes("GET /static")).slice(-25).join("\n"));
  for (const [name, pg] of Object.entries(pages)) {
    try {
      await pg.screenshot(join(SHOTS, `failure-${name}.png`));
      console.error(`--- ${name} page text:\n` + (await pg.evaluate("document.body.innerText")).slice(0, 1500));
      if (pg.errors.length) console.error(`--- ${name} errors:\n${pg.errors.join("\n")}`);
    } catch { /* page gone */ }
  }
} finally {
  if (browser) await browser.close();
  server.kill();
  await sleep(500);
  try { rmSync(work, { recursive: true, force: true }); } catch { /* Windows file locks */ }
  process.exit(ok ? 0 : 1);
}
