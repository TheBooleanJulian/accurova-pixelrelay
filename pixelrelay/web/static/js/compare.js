// Before/after slider: drag (or use the range input / arrow keys) to compare.
import { h } from "./core.js";

export function compareView(beforeUrl, afterUrl, { before = "Original", after = "Edit" } = {}) {
  const top = h("div.compare__top", h("img", { src: afterUrl, alt: after }));
  const bar = h("div.compare__bar");
  const range = h("input.compare__range", { type: "range", min: 0, max: 100, value: 50, "aria-label": `Compare ${before} and ${after}` });
  const wrap = h("div.compare",
    h("img", { src: beforeUrl, alt: before }),
    top, bar,
    h("div.compare__labels", h("span.badge", before), h("span.badge.badge--cyan", after)));
  const set = (pct) => {
    pct = Math.max(0, Math.min(100, pct));
    top.style.clipPath = `inset(0 0 0 ${pct}%)`;
    bar.style.left = `${pct}%`;
    range.value = String(pct);
  };
  range.addEventListener("input", () => set(Number(range.value)));
  let dragging = false;
  const move = (e) => {
    const r = wrap.getBoundingClientRect();
    set(((e.clientX - r.left) / r.width) * 100);
  };
  wrap.addEventListener("pointerdown", (e) => { dragging = true; wrap.setPointerCapture(e.pointerId); move(e); });
  wrap.addEventListener("pointermove", (e) => { if (dragging) move(e); });
  wrap.addEventListener("pointerup", () => (dragging = false));
  set(50);
  return h("div.col", { style: { gap: "8px" } }, wrap, range);
}
