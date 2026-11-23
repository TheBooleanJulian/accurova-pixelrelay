// Minimal Chrome DevTools Protocol driver for headless Edge/Chrome with
// separate browser contexts (separate cookie jars) per page. No dependencies:
// Node 22's built-in WebSocket and fetch are enough.
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BROWSERS = [
  process.env.PIXELRELAY_BROWSER,
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
].filter(Boolean);

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function launchBrowser({ port = 9339, headless = true } = {}) {
  const exe = BROWSERS.find((p) => existsSync(p));
  if (!exe) throw new Error("No Edge/Chrome found. Set PIXELRELAY_BROWSER.");
  const profile = mkdtempSync(join(tmpdir(), "pixelrelay-e2e-"));
  const proc = spawn(exe, [
    `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check",
    "--disable-extensions", "--disable-features=msEdgeSidebarV2", "--force-device-scale-factor=1",
    ...(headless ? ["--headless=new"] : []), ...(process.env.CI ? ["--no-sandbox"] : []), "about:blank",
  ], { stdio: "ignore" });
  let wsUrl;
  for (let i = 0; i < 100 && !wsUrl; i++) {
    await sleep(150);
    try { wsUrl = (await (await fetch(`http://127.0.0.1:${port}/json/version`)).json()).webSocketDebuggerUrl; } catch { /* starting */ }
  }
  if (!wsUrl) throw new Error("Browser did not expose DevTools.");
  const ws = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let id = 0;
  const pending = new Map();
  const listeners = new Map(); // `${sessionId}|${method}` -> Set
  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);
    if (data.id && pending.has(data.id)) {
      const { resolve, reject } = pending.get(data.id);
      pending.delete(data.id);
      if (data.error) reject(new Error(`${data.error.message} ${data.error.data ?? ""}`));
      else resolve(data.result);
    } else if (data.method) {
      for (const fn of listeners.get(`${data.sessionId ?? ""}|${data.method}`) ?? []) fn(data.params);
    }
  };
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const mid = ++id;
    pending.set(mid, { resolve, reject });
    ws.send(JSON.stringify({ id: mid, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
  const on = (sessionId, method, fn) => {
    const key = `${sessionId}|${method}`;
    if (!listeners.has(key)) listeners.set(key, new Set());
    listeners.get(key).add(fn);
  };

  async function newPage({ width = 1366, height = 900, mobile = false } = {}) {
    const { browserContextId } = await send("Target.createBrowserContext", { disposeOnDetach: true });
    const { targetId } = await send("Target.createTarget", { url: "about:blank", browserContextId });
    const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
    const s = (method, params) => send(method, params, sessionId);
    await s("Page.enable");
    await s("Runtime.enable");
    await s("DOM.enable");
    await s("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile });
    const errors = [];
    on(sessionId, "Runtime.exceptionThrown", (p) => errors.push(p.exceptionDetails?.exception?.description ?? p.exceptionDetails?.text));
    on(sessionId, "Runtime.consoleAPICalled", (p) => { if (p.type === "error") errors.push(p.args.map((a) => a.value ?? a.description).join(" ")); });
    on(sessionId, "Log.entryAdded", (p) => { if (p.entry.level === "error" && !/favicon/.test(p.entry.url || "")) errors.push(`${p.entry.text} ${p.entry.url || ""}`); });
    await s("Log.enable");

    const page = {
      errors,
      send: s,
      async evaluate(expression) {
        const r = await s("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, userGesture: true });
        if (r.exceptionDetails) throw new Error(`Evaluate failed: ${r.exceptionDetails.exception?.description ?? r.exceptionDetails.text}\n  in: ${expression.slice(0, 160)}`);
        return r.result.value;
      },
      async waitFor(expression, { timeout = 30000, label } = {}) {
        const start = Date.now();
        let last;
        while (Date.now() - start < timeout) {
          try { last = await page.evaluate(expression); if (last) return last; } catch (e) { last = e.message; }
          await sleep(150);
        }
        throw new Error(`Timed out waiting for ${label ?? expression} (last: ${JSON.stringify(last)})`);
      },
      async goto(url) { await s("Page.navigate", { url }); await sleep(300); await page.waitFor("document.readyState === 'complete'"); },
      async clickText(selector, text, { timeout = 20000 } = {}) {
        const expr = `(() => { const els = [...document.querySelectorAll(${JSON.stringify(selector)})].filter(e => e.textContent.trim().includes(${JSON.stringify(text)}) && !e.disabled && e.offsetParent !== null); if (!els.length) return false; els[0].scrollIntoView({block: 'center'}); els[0].click(); return true; })()`;
        await page.waitFor(expr, { timeout, label: `${selector} "${text}"` });
      },
      async setFiles(selector, files) {
        const { root } = await s("DOM.getDocument", { depth: -1 });
        const { nodeId } = await s("DOM.querySelector", { nodeId: root.nodeId, selector });
        if (!nodeId) throw new Error(`No element ${selector}`);
        await s("DOM.setFileInputFiles", { nodeId, files });
      },
      async mouse(type, x, y, buttons = 1) {
        await s("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons: type === "mouseReleased" ? 0 : buttons, clickCount: 1 });
      },
      async stroke(points) {
        const [first, ...rest] = points;
        await page.mouse("mouseMoved", first.x, first.y, 0);
        await page.mouse("mousePressed", first.x, first.y);
        for (const p of rest) { await page.mouse("mouseMoved", p.x, p.y); await sleep(12); }
        const last = points[points.length - 1];
        await page.mouse("mouseReleased", last.x, last.y);
      },
      async screenshot(path) {
        const { data } = await s("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
        writeFileSync(path, Buffer.from(data, "base64"));
      },
      async fullScreenshot(path) {
        const { cssContentSize } = await s("Page.getLayoutMetrics");
        const { data } = await s("Page.captureScreenshot", { format: "png", captureBeyondViewport: true,
          clip: { x: 0, y: 0, width: cssContentSize.width, height: Math.min(cssContentSize.height, 4000), scale: 1 } });
        writeFileSync(path, Buffer.from(data, "base64"));
      },
    };
    return page;
  }

  async function close() {
    try { await send("Browser.close"); } catch { /* already gone */ }
    proc.kill();
    await sleep(300);
    try { rmSync(profile, { recursive: true, force: true }); } catch { /* locked on Windows */ }
  }

  return { newPage, close };
}
