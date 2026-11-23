// Shared helpers: API calls, DOM building, icons, toasts, theme, formatting.

export class ApiError extends Error {
  constructor(status, message, retryAfter) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

export async function api(path, { method = "GET", body, form, signal } = {}) {
  const headers = { "X-PixelRelay": "1" };
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, { method, headers, body: payload, credentials: "same-origin", signal });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError(0, "Couldn't reach PixelRelay. Check your connection and try again.");
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!res.ok) {
    const msg = (data && typeof data.detail === "string" && data.detail) || "Something went wrong. Try again.";
    throw new ApiError(res.status, msg, Number(res.headers.get("Retry-After")) || 0);
  }
  return data;
}

// Upload with progress (fetch has no upload progress events).
export function upload(path, file, { fields = {}, onProgress } = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.setRequestHeader("X-PixelRelay", "1");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { data = null; }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError(xhr.status, (data && data.detail) || "Upload failed. Try again."));
    };
    xhr.onerror = () => reject(new ApiError(0, "Upload failed. Check your connection and try again."));
    const fd = new FormData();
    fd.append("file", file, file.name || "file");
    for (const [k, v] of Object.entries(fields)) fd.append(k, v);
    xhr.send(fd);
  });
}

// h("div.panel#id", {attrs}, children...) — tiny element builder. Text is always escaped.
export function h(tag, attrs, ...children) {
  if (attrs === null || typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs)) {
    if (attrs !== undefined && attrs !== null) children.unshift(attrs);
    attrs = {};
  }
  const [name, ...rest] = tag.split(/(?=[.#])/);
  const el = document.createElement(name || "div");
  for (const part of rest) {
    if (part[0] === ".") el.classList.add(part.slice(1));
    else if (part[0] === "#") el.id = part.slice(1);
  }
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "class") el.className += (el.className ? " " : "") + v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

// Lucide outline icons (ISC licence), 24px viewBox, stroke currentColor.
const ICONS = {
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5a5.5 5.5 0 0 1-5.5 5.5H11"/>',
  redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5A5.5 5.5 0 0 0 4 14.5A5.5 5.5 0 0 0 9.5 20H13"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  zoomIn: '<circle cx="11" cy="11" r="8"/><line x1="21" x2="16.65" y1="21" y2="16.65"/><line x1="11" x2="11" y1="8" y2="14"/><line x1="8" x2="14" y1="11" y2="11"/>',
  zoomOut: '<circle cx="11" cy="11" r="8"/><line x1="21" x2="16.65" y1="21" y2="16.65"/><line x1="8" x2="14" y1="11" y2="11"/>',
  fit: '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>',
  brush: '<path d="m9.06 11.9 8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/>',
  eraser: '<path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/><path d="M22 21H7"/><path d="m5 11 9 9"/>',
  move: '<polyline points="5 9 2 12 5 15"/><polyline points="9 5 12 2 15 5"/><polyline points="15 19 12 22 9 19"/><polyline points="19 9 22 12 19 15"/><line x1="2" x2="22" y1="12" y2="12"/><line x1="12" x2="12" y1="2" y2="22"/>',
  invert: '<circle cx="12" cy="12" r="10"/><path d="M12 18a6 6 0 0 0 0-12v12z"/>',
  reset: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
  back: '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
  next: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
  image: '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  copy: '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  layers: '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
};

export function icon(name, size = 16) {
  const span = document.createElement("span");
  span.style.display = "inline-flex";
  span.setAttribute("aria-hidden", "true");
  span.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
  return span;
}

// Brand mark: two pixel clusters handing off (placeholder until a final logo exists).
export function brandMark() {
  const span = document.createElement("span");
  span.className = "brand__mark";
  span.setAttribute("aria-hidden", "true");
  span.innerHTML = `<svg viewBox="0 0 24 24" width="22" height="22"><defs><linearGradient id="prg" x1="0" x2="1" y1="0" y2="1"><stop offset="0" style="stop-color:var(--cyan)"/><stop offset="1" style="stop-color:var(--magenta)"/></linearGradient></defs>
  <rect x="2" y="2" width="6" height="6" rx="1" fill="url(#prg)"/><rect x="9" y="2" width="6" height="6" rx="1" fill="url(#prg)" opacity=".75"/><rect x="2" y="9" width="6" height="6" rx="1" fill="url(#prg)" opacity=".75"/>
  <rect x="9" y="9" width="6" height="6" rx="1" fill="url(#prg)"/><rect x="16" y="16" width="6" height="6" rx="1" fill="url(#prg)"/><rect x="16" y="9" width="6" height="6" rx="1" fill="url(#prg)" opacity=".5"/><rect x="9" y="16" width="6" height="6" rx="1" fill="url(#prg)" opacity=".5"/></svg>`;
  return span;
}

export function topbar({ home = "/", actions = [] } = {}) {
  return h("header.topbar",
    h("a.brand", { href: home, "aria-label": "PixelRelay by Accurova, home" },
      brandMark(), h("span.brand__name", "PixelRelay"), h("span.brand__by", "by Accurova")),
    h("div.topbar__actions", ...actions, themeButton()));
}

// Theme: follow the OS first, then remember the person's choice on this device.
function storedTheme() { try { return localStorage.getItem("pr-theme"); } catch { return null; } }
export function applyTheme() {
  const t = storedTheme();
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
}
function currentTheme() {
  return document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
}
function themeButton() {
  const btn = h("button.btn.btn--ghost.btn--icon", { type: "button", "aria-label": "Switch light or dark theme" });
  const paint = () => clear(btn, icon(currentTheme() === "dark" ? "sun" : "moon"));
  btn.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("pr-theme", next); } catch { /* private mode */ }
    paint();
  });
  paint();
  return btn;
}

let toastBox;
export function toast(title, body = "", kind = "info") {
  if (!toastBox) { toastBox = h("div.toasts", { role: "status", "aria-live": "polite" }); document.body.append(toastBox); }
  const t = alertBox(kind, title, body);
  toastBox.append(t);
  setTimeout(() => t.remove(), kind === "danger" ? 7000 : 4000);
}

const GLYPH = { info: "i", live: "~", success: "✓", warning: "!", danger: "x" };
export function alertBox(kind, title, body, ...extra) {
  return h(`div.alert.alert--${kind}`, { role: kind === "danger" ? "alert" : null },
    h("span.alert__icon", GLYPH[kind] || "i"),
    h("div.col", { style: { gap: "4px" } }, h("div.alert__title", title), body ? h("div.alert__body", body) : null, ...extra));
}

export function badge(text, kind) { return h(`span.badge${kind ? ".badge--" + kind : ""}`, text); }

export function progressBar(label, value, total, { kind, caption } = {}) {
  const pct = total ? Math.round((100 * value) / total) : 0;
  const fill = h("div.progress__fill");
  fill.style.width = `${pct}%`;
  return h(`div.progress${kind ? ".progress--" + kind : ""}`,
    h("div.progress__meta", h("span", label), h("span", caption || `${value}/${total} · ${pct}%`)),
    h("div.progress__track", { role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": pct, "aria-label": label }, fill));
}

export function fileUrl(assetId, download = false) {
  return assetId ? `/api/files/${encodeURIComponent(assetId)}${download ? "?download=1" : ""}` : "";
}

export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}

export async function copyText(text, what = "Copied") {
  try {
    await navigator.clipboard.writeText(text);
    toast(what, "", "success");
  } catch {
    window.prompt("Copy this:", text);
  }
}

export function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// Poll fn() every `ms` until done(result) is true. Returns the final result.
export async function pollUntil(fn, done, { ms = 1200, timeout = 10 * 60 * 1000 } = {}) {
  const start = Date.now();
  for (;;) {
    const r = await fn();
    if (done(r)) return r;
    if (Date.now() - start > timeout) throw new ApiError(0, "This is taking longer than expected. Reload the page to check again.");
    await sleep(ms);
  }
}

export function confirmDialog(title, body, { confirm = "Confirm", danger = false } = {}) {
  return new Promise((resolve) => {
    const dlg = h("dialog.dialog", { "aria-label": title, style: { width: "min(440px, calc(100vw - 16px))" } },
      h("div.dialog__body",
        h("div.title", title), body ? h("p.muted", body) : null,
        h("div.row", { style: { justifyContent: "flex-end" } },
          h("button.btn", { type: "button", onclick: () => { dlg.close(); resolve(false); } }, "Cancel"),
          h(`button.btn${danger ? ".btn--danger" : ".btn--primary"}`, { type: "button", onclick: () => { dlg.close(); resolve(true); } }, confirm))));
    dlg.addEventListener("close", () => { dlg.remove(); resolve(false); });
    document.body.append(dlg);
    dlg.showModal();
  });
}

export const STATE_INFO = {
  unassigned: ["Unassigned", null],
  assigned: ["Assigned", "cyan"],
  editing: ["Editing", "cyan"],
  submitted: ["Awaiting review", "magenta"],
  needs_changes: ["Needs changes", "warning"],
  approved: ["Approved", "success"],
  final: ["Final", "success"],
};

export function stateBadge(state, label) {
  const [text, kind] = STATE_INFO[state] || [label || state, null];
  return badge(label || text, kind);
}

applyTheme();
