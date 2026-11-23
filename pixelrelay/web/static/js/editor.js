// Merge editor: preview the composite, refine the mask with brush and eraser,
// line up an export by hand. Works on the "work size" grid (long edge <= 2048);
// the server repeats the blend at full resolution on submit.
//
// Mask values: 255 = use the contributor's edit, 0 = keep the original.

import { alertBox, clear, fileUrl, h, icon } from "./core.js";

const MAX_UNDO = 40;

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = "async";
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("Couldn't load an image. Reload the page."));
    img.src = url;
  });
}

function canvasOf(w, h) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return c;
}

function cssColor(name) {
  const probe = document.createElement("span");
  probe.style.color = `var(${name})`;
  document.body.append(probe);
  const rgb = getComputedStyle(probe).color.match(/\d+(\.\d+)?/g).map(Number);
  probe.remove();
  return rgb.slice(0, 3);
}

export class MergeEditor {
  constructor(draft, handlers) {
    this.draft = draft;
    this.handlers = handlers; // { onDetect(sens), onAlign(values|null), onChange() }
    [this.W, this.H] = draft.work_size;
    this.tool = "brush";
    this.view = "result";
    this.overlay = true;
    this.opacity = 0.45;
    this.size = Math.max(8, Math.round(Math.max(this.W, this.H) / 30));
    this.softness = 0.5;
    this.undoStack = [];
    this.redoStack = [];
    this.dirty = false;
    this.z = 1; this.tx = 0; this.ty = 0;
    this.pointers = new Map();
    this.align = null;
    this.root = h("div.editor");
  }

  // ------------------------------------------------------------ setup

  async load() {
    const { W, H } = this;
    const [base, edit, mask] = await Promise.all([
      loadImage(fileUrl(this.draft.base_preview_asset_id)),
      loadImage(fileUrl(this.draft.edit_preview_asset_id)),
      this.draft.mask ? loadImage(fileUrl(this.draft.mask.asset_id)) : null,
    ]);
    this.baseC = canvasOf(W, H);
    this.editC = canvasOf(W, H);
    this.compC = canvasOf(W, H);
    this.ovC = canvasOf(W, H);
    const read = (c, img) => {
      const ctx = c.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(img, 0, 0, W, H);
      return ctx.getImageData(0, 0, W, H);
    };
    this.baseD = read(this.baseC, base).data;
    this.editD = read(this.editC, edit).data;
    this.mask = new Uint8ClampedArray(W * H);
    if (mask) this.mask.set(this.maskFromImage(mask));
    this.compCtx = this.compC.getContext("2d");
    this.compImg = this.compCtx.createImageData(W, H);
    this.ovCtx = this.ovC.getContext("2d");
    this.ovImg = this.ovCtx.createImageData(W, H);
    const [r, g, b] = cssColor("--magenta");
    const ov = this.ovImg.data;
    for (let i = 0; i < W * H; i++) { ov[i * 4] = r; ov[i * 4 + 1] = g; ov[i * 4 + 2] = b; }
    this.ring = cssColor("--cyan-ink");
    this.stroke0 = new Uint8ClampedArray(W * H);
    this.strokeS = new Uint8ClampedArray(W * H);
    this.recompute(0, 0, W, H);
    this.build();
    return this;
  }

  maskFromImage(img) {
    const c = canvasOf(this.W, this.H);
    const ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, this.W, this.H);
    const d = ctx.getImageData(0, 0, this.W, this.H).data;
    const out = new Uint8ClampedArray(this.W * this.H);
    for (let i = 0; i < out.length; i++) out[i] = d[i * 4];
    return out;
  }

  // ------------------------------------------------------------ pixels

  recompute(x0, y0, x1, y1) {
    const { W, H, mask, baseD: b, editD: e } = this;
    x0 = Math.max(0, x0 | 0); y0 = Math.max(0, y0 | 0);
    x1 = Math.min(W, Math.ceil(x1)); y1 = Math.min(H, Math.ceil(y1));
    if (x1 <= x0 || y1 <= y0) return;
    const c = this.compImg.data;
    const ov = this.ovImg.data;
    for (let y = y0; y < y1; y++) {
      let i = y * W + x0;
      for (let x = x0; x < x1; x++, i++) {
        const m = mask[i];
        const p = i * 4;
        if (m === 0) { c[p] = b[p]; c[p + 1] = b[p + 1]; c[p + 2] = b[p + 2]; }
        else if (m === 255) { c[p] = e[p]; c[p + 1] = e[p + 1]; c[p + 2] = e[p + 2]; }
        else {
          const a = m / 255;
          c[p] = b[p] + (e[p] - b[p]) * a;
          c[p + 1] = b[p + 1] + (e[p + 1] - b[p + 1]) * a;
          c[p + 2] = b[p + 2] + (e[p + 2] - b[p + 2]) * a;
        }
        c[p + 3] = 255;
        ov[p + 3] = m;
      }
    }
    this.compCtx.putImageData(this.compImg, 0, 0, x0, y0, x1 - x0, y1 - y0);
    this.ovCtx.putImageData(this.ovImg, 0, 0, x0, y0, x1 - x0, y1 - y0);
    this.requestDraw();
  }

  // ------------------------------------------------------------ history

  pushHistory(x, y, w, h, before) {
    const after = this.copyRect(this.mask, x, y, w, h);
    this.undoStack.push({ x, y, w, h, before, after });
    if (this.undoStack.length > MAX_UNDO) this.undoStack.shift();
    this.redoStack = [];
    this.changed();
  }

  copyRect(src, x, y, w, h) {
    const out = new Uint8ClampedArray(w * h);
    for (let r = 0; r < h; r++) out.set(src.subarray((y + r) * this.W + x, (y + r) * this.W + x + w), r * w);
    return out;
  }

  writeRect(data, x, y, w, h) {
    for (let r = 0; r < h; r++) this.mask.set(data.subarray(r * w, r * w + w), (y + r) * this.W + x);
    this.recompute(x, y, x + w, y + h);
  }

  undo() {
    const e = this.undoStack.pop();
    if (!e) return;
    this.writeRect(e.before, e.x, e.y, e.w, e.h);
    this.redoStack.push(e);
    this.changed();
  }

  redo() {
    const e = this.redoStack.pop();
    if (!e) return;
    this.writeRect(e.after, e.x, e.y, e.w, e.h);
    this.undoStack.push(e);
    this.changed();
  }

  globalOp(fn) {
    const before = this.mask.slice();
    fn(this.mask);
    this.recompute(0, 0, this.W, this.H);
    this.pushHistory(0, 0, this.W, this.H, before);
  }

  invert() { this.globalOp((m) => { for (let i = 0; i < m.length; i++) m[i] = 255 - m[i]; }); }
  resetMask() { this.globalOp((m) => m.fill(0)); }
  fillMask() { this.globalOp((m) => m.fill(255)); }

  async replaceMask(maskAssetId) {
    const img = await loadImage(fileUrl(maskAssetId));
    const next = this.maskFromImage(img);
    this.globalOp((m) => m.set(next));
  }

  async replaceEdit(editAssetId) {
    const img = await loadImage(fileUrl(editAssetId));
    const ctx = this.editC.getContext("2d", { willReadFrequently: true });
    ctx.clearRect(0, 0, this.W, this.H);
    ctx.drawImage(img, 0, 0, this.W, this.H);
    this.editD = ctx.getImageData(0, 0, this.W, this.H).data;
    this.recompute(0, 0, this.W, this.H);
  }

  changed() {
    this.dirty = true;
    this.refreshButtons();
    if (this.handlers.onChange) this.handlers.onChange();
  }

  markSaved() { this.dirty = false; this.refreshButtons(); }

  toPngBlob() {
    const c = canvasOf(this.W, this.H);
    const ctx = c.getContext("2d");
    const img = ctx.createImageData(this.W, this.H);
    const d = img.data;
    for (let i = 0; i < this.mask.length; i++) {
      const v = this.mask[i];
      d[i * 4] = v; d[i * 4 + 1] = v; d[i * 4 + 2] = v; d[i * 4 + 3] = 255;
    }
    ctx.putImageData(img, 0, 0);
    return new Promise((resolve) => c.toBlob(resolve, "image/png"));
  }

  // ------------------------------------------------------------ painting

  beginStroke() {
    this.stroke0.set(this.mask);
    this.strokeS.fill(0);
    this.box = [Infinity, Infinity, -Infinity, -Infinity];
    this.last = null;
  }

  stamp(cx, cy, strength) {
    const r = this.size / 2;
    const hard = 1 - this.softness;
    const { W, H, mask, stroke0: m0, strokeS: S } = this;
    const add = this.tool === "brush";
    const x0 = Math.max(0, Math.floor(cx - r)), x1 = Math.min(W, Math.ceil(cx + r));
    const y0 = Math.max(0, Math.floor(cy - r)), y1 = Math.min(H, Math.ceil(cy + r));
    if (x1 <= x0 || y1 <= y0) return;
    for (let y = y0; y < y1; y++) {
      const dy = y + 0.5 - cy;
      let i = y * W + x0;
      for (let x = x0; x < x1; x++, i++) {
        const dx = x + 0.5 - cx;
        const d = Math.sqrt(dx * dx + dy * dy) / r;
        if (d >= 1) continue;
        let v = d <= hard ? 1 : (1 - d) / (1 - hard);
        v = v * v * (3 - 2 * v); // smoothstep falloff
        const s = Math.round(v * strength * 255);
        if (s <= S[i]) continue;
        S[i] = s;
        mask[i] = add ? m0[i] + ((255 - m0[i]) * s) / 255 : (m0[i] * (255 - s)) / 255;
      }
    }
    const b = this.box;
    b[0] = Math.min(b[0], x0); b[1] = Math.min(b[1], y0); b[2] = Math.max(b[2], x1); b[3] = Math.max(b[3], y1);
    this.recompute(x0, y0, x1, y1);
  }

  strokeTo(p, strength) {
    if (!this.last) { this.stamp(p.x, p.y, strength); this.last = p; return; }
    const dx = p.x - this.last.x, dy = p.y - this.last.y;
    const dist = Math.hypot(dx, dy);
    const step = Math.max(1, this.size * 0.2);
    for (let t = step; t <= dist; t += step) this.stamp(this.last.x + (dx * t) / dist, this.last.y + (dy * t) / dist, strength);
    if (dist >= step) this.last = { x: this.last.x + (dx * Math.floor(dist / step) * step) / dist, y: this.last.y + (dy * Math.floor(dist / step) * step) / dist };
  }

  endStroke() {
    const [x0, y0, x1, y1] = this.box;
    if (x1 <= x0) return;
    const w = x1 - x0, hh = y1 - y0;
    this.pushHistory(x0, y0, w, hh, this.copyRect(this.stroke0, x0, y0, w, hh));
  }

  // ------------------------------------------------------------ view

  toImage(sx, sy) { return { x: (sx - this.tx) / this.z, y: (sy - this.ty) / this.z }; }

  fit() {
    const r = this.canvas.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) return false;
    this.z = Math.min(r.width / this.W, r.height / this.H) * 0.98;
    this.tx = (r.width - this.W * this.z) / 2;
    this.ty = (r.height - this.H * this.z) / 2;
    this.requestDraw();
    return true;
  }

  zoomAt(factor, sx, sy) {
    const r = this.canvas.getBoundingClientRect();
    const fitZ = Math.min(r.width / this.W, r.height / this.H) * 0.98;
    const nz = Math.min(16, Math.max(fitZ * 0.5, this.z * factor));
    this.tx = sx - ((sx - this.tx) * nz) / this.z;
    this.ty = sy - ((sy - this.ty) * nz) / this.z;
    this.z = nz;
    this.requestDraw();
  }

  zoomCenter(factor) {
    const r = this.canvas.getBoundingClientRect();
    this.zoomAt(factor, r.width / 2, r.height / 2);
  }

  requestDraw() {
    if (this.drawQueued || !this.canvas) return;
    this.drawQueued = true;
    requestAnimationFrame(() => { this.drawQueued = false; this.draw(); });
  }

  draw() {
    const c = this.canvas;
    const dpr = window.devicePixelRatio || 1;
    const r = c.getBoundingClientRect();
    if (c.width !== Math.round(r.width * dpr) || c.height !== Math.round(r.height * dpr)) {
      c.width = Math.round(r.width * dpr);
      c.height = Math.round(r.height * dpr);
    }
    const ctx = c.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, c.width, c.height);
    ctx.setTransform(dpr * this.z, 0, 0, dpr * this.z, dpr * this.tx, dpr * this.ty);
    ctx.imageSmoothingEnabled = this.z < 3;
    if (this.align) {
      this.drawAlign(ctx);
    } else {
      const src = this.peek || this.view === "original" ? this.baseC : this.view === "edit" ? this.editC : this.compC;
      ctx.drawImage(src, 0, 0);
      if (this.overlay && this.view === "result" && !this.peek) {
        ctx.globalAlpha = this.opacity;
        ctx.drawImage(this.ovC, 0, 0);
        ctx.globalAlpha = 1;
      }
    }
    if (this.cursor && !this.align && this.tool !== "move") {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.beginPath();
      ctx.arc(this.cursor.x, this.cursor.y, Math.max(2, (this.size / 2) * this.z), 0, Math.PI * 2);
      ctx.strokeStyle = `rgb(${this.ring.join(",")})`;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }

  // ------------------------------------------------------------ line up by hand

  async startAlign() {
    if (!this.uploadImg) this.uploadImg = await loadImage(fileUrl(this.draft.upload_asset_id));
    const al = this.draft.alignment || {};
    const manual = this.draft.manual_align || {};
    const [Wb, Hb] = al.base_size || [this.W, this.H];
    const p = al.placement || { rotate: 0, sx: 1, ox: 0, oy: 0 };
    if (manual && manual.scale) this.align = { ...manual };
    else {
      const q = p.rotate || 0;
      const erW = q % 2 ? this.uploadImg.naturalHeight : this.uploadImg.naturalWidth;
      this.align = { rotate: q, scale: (p.sx || 1) / (Wb / erW), dx: (p.ox || 0) / Wb, dy: (p.oy || 0) / Hb };
    }
    this.align.scale = Math.round(this.align.scale * 1000) / 1000;
    this.requestDraw();
  }

  stopAlign() { this.align = null; this.requestDraw(); }

  alignBox() {
    const a = this.align;
    const img = this.uploadImg;
    const erW = a.rotate % 2 ? img.naturalHeight : img.naturalWidth;
    const erH = a.rotate % 2 ? img.naturalWidth : img.naturalHeight;
    const bw = this.W * a.scale;
    const bh = (bw * erH) / erW;
    return { x: a.dx * this.W, y: a.dy * this.H, w: bw, h: bh };
  }

  drawAlign(ctx) {
    ctx.drawImage(this.baseC, 0, 0);
    const b = this.alignBox();
    const q = this.align.rotate;
    ctx.save();
    ctx.globalAlpha = 0.5;
    ctx.translate(b.x + b.w / 2, b.y + b.h / 2);
    ctx.rotate((-q * Math.PI) / 2); // the server turns the edit counter-clockwise by q quarters
    const iw = q % 2 ? b.h : b.w, ih = q % 2 ? b.w : b.h;
    ctx.drawImage(this.uploadImg, -iw / 2, -ih / 2, iw, ih);
    ctx.restore();
    ctx.strokeStyle = `rgb(${this.ring.join(",")})`;
    ctx.lineWidth = 2 / this.z;
    ctx.strokeRect(b.x, b.y, b.w, b.h);
  }

  // ------------------------------------------------------------ input

  bindInput() {
    const c = this.canvas;
    const pos = (e) => { const r = c.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
    let mode = null; // paint | pan | pinch | drag-align
    let panStart = null;
    let pinch = null;

    const startPinch = () => {
      const [a, b] = [...this.pointers.values()];
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2 };
    };

    c.addEventListener("pointerdown", (e) => {
      c.setPointerCapture(e.pointerId);
      const p = pos(e);
      this.pointers.set(e.pointerId, p);
      if (this.pointers.size === 2) {
        if (mode === "paint") this.endStroke();
        mode = "pinch";
        startPinch();
        return;
      }
      if (this.pointers.size > 2) return;
      if (this.align) { mode = "drag-align"; panStart = { p, dx: this.align.dx, dy: this.align.dy }; return; }
      if (this.tool === "move" || e.button === 1 || this.spaceDown) {
        mode = "pan"; panStart = { p, tx: this.tx, ty: this.ty }; return;
      }
      if (e.button !== 0 || this.view !== "result") {
        if (this.view !== "result") this.handlers.notice?.("Switch to Result to paint.");
        return;
      }
      mode = "paint";
      this.beginStroke();
      this.strokeTo(this.toImage(p.x, p.y), this.strength(e));
    });

    c.addEventListener("pointermove", (e) => {
      const p = pos(e);
      this.cursor = e.pointerType === "touch" ? null : p;
      if (!this.pointers.has(e.pointerId)) { this.requestDraw(); return; }
      this.pointers.set(e.pointerId, p);
      if (mode === "pinch" && this.pointers.size === 2) {
        const [a, b] = [...this.pointers.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
        this.tx += cx - pinch.cx; this.ty += cy - pinch.cy;
        if (pinch.d > 0) this.zoomAt(d / pinch.d, cx, cy);
        pinch = { d, cx, cy };
      } else if (mode === "pan") {
        this.tx = panStart.tx + p.x - panStart.p.x;
        this.ty = panStart.ty + p.y - panStart.p.y;
        this.requestDraw();
      } else if (mode === "drag-align") {
        this.align.dx = panStart.dx + (p.x - panStart.p.x) / this.z / this.W;
        this.align.dy = panStart.dy + (p.y - panStart.p.y) / this.z / this.H;
        this.handlers.onAlignChange?.();
        this.requestDraw();
      } else if (mode === "paint") {
        const events = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
        for (const ce of events) { const cp = pos(ce); this.strokeTo(this.toImage(cp.x, cp.y), this.strength(ce)); }
      } else {
        this.requestDraw();
      }
    });

    const end = (e) => {
      this.pointers.delete(e.pointerId);
      if (mode === "paint") this.endStroke();
      if (this.pointers.size === 0) mode = null;
      else if (mode === "pinch" && this.pointers.size === 1) mode = null;
    };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
    c.addEventListener("pointerleave", () => { this.cursor = null; this.requestDraw(); });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const p = pos(e);
      this.zoomAt(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), p.x, p.y);
    }, { passive: false });
    c.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  strength(e) { return e.pointerType === "pen" && e.pressure > 0 ? 0.25 + 0.75 * e.pressure : 1; }

  bindKeys() {
    this.onKey = (e) => {
      if (!document.body.contains(this.root)) { window.removeEventListener("keydown", this.onKey); return; }
      if (e.target.closest && e.target.closest("input, textarea, select")) return;
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && k === "z") { e.preventDefault(); e.shiftKey ? this.redo() : this.undo(); }
      else if ((e.ctrlKey || e.metaKey) && k === "y") { e.preventDefault(); this.redo(); }
      else if (e.ctrlKey || e.metaKey || e.altKey) return;
      else if (k === "b") this.setTool("brush");
      else if (k === "e") this.setTool("erase");
      else if (k === "h") this.setTool("move");
      else if (k === "[") this.setSize(this.size / 1.25);
      else if (k === "]") this.setSize(this.size * 1.25);
      else if (k === "o") this.setOverlay(!this.overlay);
      else if (k === " ") { this.spaceDown = true; e.preventDefault(); }
      else if (k === "\\") { this.peek = true; this.requestDraw(); }
    };
    this.onKeyUp = (e) => {
      if (e.key === " ") this.spaceDown = false;
      if (e.key === "\\") { this.peek = false; this.requestDraw(); }
    };
    window.addEventListener("keydown", this.onKey);
    window.addEventListener("keyup", this.onKeyUp);
  }

  // ------------------------------------------------------------ UI

  setTool(t) { this.tool = t; this.stage.dataset.tool = t; this.refreshButtons(); }
  setView(v) { this.view = v; this.refreshButtons(); this.requestDraw(); }
  setOverlay(on) { this.overlay = on; if (this.overlayToggle) this.overlayToggle.checked = on; this.requestDraw(); }
  setSize(s) {
    this.size = Math.max(2, Math.min(Math.max(this.W, this.H) / 2, Math.round(s)));
    if (this.sizeInput) { this.sizeInput.value = String(this.size); this.sizeOut.textContent = `${this.size} px`; }
    this.requestDraw();
  }

  build() {
    this.canvas = h("canvas", { "aria-label": "Edit preview. Paint to use your edit, erase to keep the original." });
    this.busy = h("div.editor__busy", { hidden: true });
    const zoomBtn = (name, label, fn) => h("button.btn.btn--sm.btn--icon", { type: "button", "aria-label": label, title: label, onclick: fn }, icon(name));
    this.stage = h("div.editor__stage.grid-bg", { dataset: { tool: this.tool } },
      this.canvas,
      h("div.editor__hud", h("span.badge.badge--magenta", "Pink = your edit is used")),
      h("div.editor__zoom",
        zoomBtn("zoomOut", "Zoom out", () => this.zoomCenter(1 / 1.4)),
        zoomBtn("fit", "Fit to screen", () => this.fit()),
        zoomBtn("zoomIn", "Zoom in", () => this.zoomCenter(1.4))),
      this.busy);
    // Size the stage to the photo so phones don't waste space on letterboxing.
    this.stage.style.aspectRatio = `${this.W} / ${this.H}`;

    const viewBtn = (v, text) => h("button.btn", { type: "button", "aria-pressed": "false", dataset: { view: v }, onclick: () => this.setView(v) }, text);
    this.viewBtns = [viewBtn("result", "Result"), viewBtn("original", "Original"), viewBtn("edit", "Your edit")];
    this.overlayToggle = h("input", { type: "checkbox", role: "switch", checked: this.overlay, onchange: (e) => this.setOverlay(e.target.checked) });
    const opacity = h("input", { type: "range", min: 10, max: 90, value: Math.round(this.opacity * 100), "aria-label": "Highlight strength",
      oninput: (e) => { this.opacity = Number(e.target.value) / 100; this.requestDraw(); } });

    const toolBtn = (t, ic, text, key) => h("button.btn", { type: "button", "aria-pressed": "false", dataset: { tool: t }, title: `${text} (${key})`, onclick: () => this.setTool(t) }, icon(ic), text);
    this.toolBtns = [toolBtn("brush", "brush", "Add edit", "B"), toolBtn("erase", "eraser", "Keep original", "E"), toolBtn("move", "move", "Move", "H")];
    this.sizeOut = h("span.mono-sm", `${this.size} px`);
    this.sizeInput = h("input", { type: "range", min: 2, max: Math.round(Math.max(this.W, this.H) / 4), value: this.size, "aria-label": "Brush size",
      oninput: (e) => this.setSize(Number(e.target.value)) });
    const softOut = h("span.mono-sm", `${Math.round(this.softness * 100)}%`);
    const soft = h("input", { type: "range", min: 0, max: 100, value: Math.round(this.softness * 100), "aria-label": "Brush softness",
      oninput: (e) => { this.softness = Number(e.target.value) / 100; softOut.textContent = `${e.target.value}%`; } });

    this.undoBtn = h("button.btn", { type: "button", title: "Undo (Ctrl+Z)", onclick: () => this.undo() }, icon("undo"), "Undo");
    this.redoBtn = h("button.btn", { type: "button", title: "Redo (Ctrl+Y)", onclick: () => this.redo() }, icon("redo"), "Redo");

    clear(this.root,
      this.stage,
      h("div.toolbar",
        h("div.seg", { role: "group", "aria-label": "View" }, this.viewBtns),
        h("label.toggle", this.overlayToggle, "Highlight"),
        h("label.slider", h("span.label", "Highlight strength"), opacity)),
      h("div.toolbar",
        h("div.seg", { role: "group", "aria-label": "Tool" }, this.toolBtns),
        h("label.slider", h("span.label", h("span", "Size"), this.sizeOut), this.sizeInput),
        h("label.slider", h("span.label", h("span", "Softness"), softOut), soft)),
      h("p.tool-help", "Add edit paints your changes in. Keep original paints them out. Two fingers zoom and move on a phone."),
      h("div.toolbar",
        this.undoBtn, this.redoBtn,
        h("button.btn", { type: "button", title: "Swap edited and original areas", onclick: () => this.invert() }, icon("invert"), "Invert"),
        h("button.btn", { type: "button", title: "Keep the original everywhere", onclick: () => this.resetMask() }, icon("reset"), "Clear"),
        h("button.btn", { type: "button", title: "Use your edit everywhere", onclick: () => this.fillMask() }, icon("layers"), "Use whole edit")));

    this.bindInput();
    this.bindKeys();
    new ResizeObserver(() => {
      if (this.fitted) this.requestDraw();
      else this.fitted = this.fit();
    }).observe(this.stage);
    this.refreshButtons();
  }

  refreshButtons() {
    if (!this.viewBtns) return;
    for (const b of this.viewBtns) b.setAttribute("aria-pressed", String(b.dataset.view === this.view));
    for (const b of this.toolBtns) b.setAttribute("aria-pressed", String(b.dataset.tool === this.tool));
    this.undoBtn.disabled = !this.undoStack.length;
    this.redoBtn.disabled = !this.redoStack.length;
  }

  setBusy(message) {
    if (!message) { this.busy.hidden = true; return; }
    clear(this.busy, alertBox("live", message, "This takes a few seconds."));
    this.busy.hidden = false;
  }
}
