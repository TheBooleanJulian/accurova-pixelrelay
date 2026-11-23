// Contributor app: /r/{slug}, /r/{slug}/photo/{id}, /r/{slug}/edits
import {
  ApiError, alertBox, api, badge, clear, confirmDialog, fileUrl, fmtDate, fmtTime, h, icon,
  pollUntil, progressBar, toast, topbar, upload,
} from "./core.js";
import { compareView } from "./compare.js";
import { MergeEditor } from "./editor.js";

const app = document.getElementById("app");
const slug = location.pathname.split("/")[2];
const base = `/api/rooms/${slug}`;
let room = null;

const LABELS = {
  assigned: ["To do", "cyan"], editing: ["Editing", "cyan"], submitted: ["Submitted", "magenta"],
  needs_changes: ["Needs changes", "warning"], approved: ["Approved", "success"], final: ["Final version", "success"],
  reassigned: ["Reassigned", null],
};
const REV_LABELS = { submitted: ["Waiting for review", "magenta"], needs_changes: ["Needs changes", "warning"], approved: ["Approved", "success"] };

function go(path) { history.pushState(null, "", path); route(); }
window.addEventListener("popstate", () => route());

function frame(...content) {
  const actions = room && room.state !== "need_pin"
    ? [h("button.btn.btn--ghost.btn--sm", { type: "button", onclick: leave, title: "Leave this project on this device" }, icon("logout"), h("span.sr-only", "Leave"))]
    : [];
  clear(app, topbar({ home: room && room.state === "ready" ? `/r/${slug}` : "/", actions }),
    h("main.main.main--narrow", ...content), h("footer.footer", "PixelRelay by Accurova · Collaborate. Create. Preserve."));
}

async function leave() {
  if (!(await confirmDialog("Leave this project on this device?", "You'll need the Room PIN to come back. Your edits are kept.", { confirm: "Leave" }))) return;
  await api(`${base}/leave`, { method: "POST" });
  room = null;
  go(`/r/${slug}`);
}

async function route() {
  try {
    room = await api(base);
  } catch (err) {
    frame(alertBox("danger", "This project link is not valid", "Check the link with your organiser."));
    return;
  }
  const parts = location.pathname.split("/").slice(3);
  if (room.state === "need_pin") return renderPin();
  if (room.state === "need_name") return renderNames();
  if (parts[0] === "photo" && parts[1]) return renderPhoto(parts[1]);
  if (parts[0] === "edits") return renderHome("edits");
  return renderHome("photos");
}

// ---------------------------------------------------------------- join

function renderPin() {
  const input = h("input.input.pin-input#pin", {
    inputmode: "numeric", autocomplete: "one-time-code", maxlength: 7, pattern: "[0-9 ]*", required: true,
    "aria-describedby": "pin-help", placeholder: "000000",
  });
  const err = h("div.col");
  const btn = h("button.btn.btn--primary.btn--lg.btn--block", { type: "submit" }, "Enter");
  let timer = null;
  const form = h("form.col", {
    onsubmit: async (e) => {
      e.preventDefault();
      btn.disabled = true;
      clear(err);
      try {
        await api(`${base}/join`, { method: "POST", body: { pin: input.value } });
        route();
      } catch (ex) {
        input.setAttribute("aria-invalid", "true");
        if (ex.status === 429 && ex.retryAfter) {
          let left = ex.retryAfter;
          const line = h("div.field__error");
          clear(err, line);
          const tick = () => {
            line.textContent = `x Too many tries. Try again in ${left} s.`;
            if (left-- <= 0) { clearInterval(timer); btn.disabled = false; clear(err); }
          };
          tick();
          timer = setInterval(tick, 1000);
          return;
        }
        clear(err, h("div.field__error", `x ${ex.message}`));
      }
      btn.disabled = false;
    },
  },
  h("label.field", h("span.label", "Room PIN"), input,
    h("span.field__help#pin-help", "6 digits, from the message your organiser sent.")),
  err, btn);
  frame(
    h("div.page-head", h("h1.display-lg", "Join the edit room"),
      h("p.muted", "No account needed. Enter the PIN, then choose your name.")),
    h("section.panel.panel--bracket.col", form));
  input.focus();
}

async function renderNames() {
  const { people } = await api(`${base}/people`);
  const err = h("div.col");
  frame(
    h("div.page-head", h("span.label", room.project.name), h("h1.display-lg", "Who are you?"),
      h("p.muted", "Choose your name. This device will remember you for this project.")),
    err,
    people.length
      ? h("div.choices", people.map((p) => h("button.choice", {
        type: "button", disabled: p.invite_only,
        onclick: async () => {
          try { await api(`${base}/identify`, { method: "POST", body: { member_id: p.id } }); route(); }
          catch (ex) { clear(err, alertBox("danger", "Couldn't choose that name", ex.message)); }
        },
      }, h("span", p.name), p.invite_only ? h("span.small.muted", "Uses a personal link") : icon("next"))))
      : alertBox("info", "No names yet", "The organiser hasn't added participants. Ask them to add you."),
    h("p.small.muted", "Not on the list? Ask the organiser to add you. Never choose someone else's name."));
}

// ---------------------------------------------------------------- home

function availabilityPanel(onDone) {
  const choose = async (opted, capacity) => {
    try {
      room = await api(`${base}/me/availability`, { method: "POST", body: { opted_in: opted, capacity } });
      onDone();
    } catch (ex) { toast("Couldn't save", ex.message, "danger"); }
  };
  return h("section.panel.panel--bracket.col",
    h("h2.title", `Hi ${room.me.name}. How many photos can you edit?`),
    h("p.small.muted", "The organiser uses this to share photos out fairly. You can change it later."),
    h("div.capacity", [1, 2, 3, 5].map((n) => h("button.btn", { type: "button", onclick: () => choose(true, n) }, String(n))),
      h("button.btn", { type: "button", onclick: () => choose(true, null) }, "Any")),
    h("button.btn.btn--ghost", { type: "button", onclick: () => choose(false, 0) }, "Not editing this time"));
}

async function renderHome(tab) {
  const me = room.me;
  const content = h("div.col", h("p.muted", "Loading..."));
  const needsAnswer = !me.opted_in && me.capacity === null;
  const willing = me.opted_in ? (me.capacity === null ? "any number of photos" : `up to ${me.capacity} photo${me.capacity === 1 ? "" : "s"}`) : "not editing this time";
  const tabs = h("div.tabs", { role: "tablist" },
    h("button.tab", { role: "tab", "aria-selected": String(tab === "photos"), onclick: () => go(`/r/${slug}`) }, "My photos"),
    h("button.tab", { role: "tab", "aria-selected": String(tab === "edits"), onclick: () => go(`/r/${slug}/edits`) }, "My edits"));
  const prog = room.progress;
  frame(
    h("div.page-head",
      h("span.label", "Edit room"),
      h("h1.display-lg", room.project.name),
      room.project.deadline ? h("p.mono-sm.muted", `Due ${fmtDate(room.project.deadline)}`) : null),
    needsAnswer ? availabilityPanel(() => renderHome(tab)) : null,
    tabs, content,
    prog.total ? h("section.panel", progressBar("Group progress", prog.done, prog.total, { caption: `${prog.done} of ${prog.total} photos done` })) : null,
    h("section.col",
      h("p.small.muted", `You're editing as ${me.name}. You said: ${willing}.`),
      h("div.row",
        !needsAnswer ? h("button.btn.btn--sm", { type: "button", onclick: () => { room.me.opted_in = false; room.me.capacity = null; renderHome(tab); } }, "Change how many") : null,
        room.via === "pin" ? h("button.btn.btn--sm.btn--ghost", {
          type: "button",
          onclick: async () => { await api(`${base}/not-me`, { method: "POST" }); route(); },
        }, `This isn't me`) : null)));

  try {
    if (tab === "edits") await fillEdits(content);
    else await fillPhotos(content, me);
  } catch (ex) {
    clear(content, alertBox("danger", "Couldn't load", ex.message));
  }
}

const NEXT_ACTION = {
  assigned: "Download and edit", editing: "Continue", needs_changes: "See what to change",
  submitted: "View your edit", approved: "View", final: "View",
};

async function fillPhotos(el, me) {
  const { photos } = await api(`${base}/photos`);
  if (!photos.length) {
    clear(el, h("div.empty.panel", icon("image", 20), h("span.title", "No photos for you yet"),
      h("span.small", me.opted_in ? "The organiser will assign photos. Check back soon." : "You're not editing this time. Change that below if you'd like to help.")));
    return;
  }
  clear(el, h("div.cards", photos.map((p) => {
    const [label, kind] = LABELS[p.state] || [p.state_label, null];
    const action = p.draft && p.draft.status !== "submitted" ? "Continue merging" : NEXT_ACTION[p.state] || "Open";
    return h("a.card", { href: `/r/${slug}/photo/${p.id}`, onclick: (e) => { e.preventDefault(); go(`/r/${slug}/photo/${p.id}`); } },
      p.thumb_asset_id ? h("img.card__thumb", { src: fileUrl(p.thumb_asset_id), alt: "", loading: "lazy" }) : h("div.card__thumb"),
      h("div.card__body", h("span.card__title", p.title), h("div.row", badge(label, kind)), h("span.small.muted", action)),
      icon("next"));
  })));
}

async function fillEdits(el) {
  const { edits } = await api(`${base}/edits`);
  if (!edits.length) {
    clear(el, h("div.empty.panel", icon("layers", 20), h("span.title", "No edits yet"), h("span.small", "Edits you submit appear here, with every earlier version kept.")));
    return;
  }
  clear(el, h("div.cards", edits.map((r) => {
    const [label, kind] = r.is_final ? ["Final version", "success"] : REV_LABELS[r.status] || [r.status, null];
    return h("div.card",
      h("img.card__thumb", { src: fileUrl(r.composite_preview_asset_id || r.thumb_asset_id), alt: "", loading: "lazy" }),
      h("div.card__body",
        h("span.card__title", r.photo_title),
        h("div.row", badge(label, kind), h("span.mono-sm.muted", `Edit ${r.number} · ${fmtTime(r.created_at)}`)),
        h("div.row",
          h("button.btn.btn--sm", { type: "button", onclick: () => showRevision(r) }, "View"),
          r.can_reopen ? h("button.btn.btn--sm", { type: "button", onclick: () => reopen(r) }, "Adjust") : null)));
  })));
}

async function showRevision(r) {
  const photo = await api(`${base}/photos/${r.photo_id}`);
  const dlg = h("dialog.dialog", { "aria-label": r.photo_title },
    h("div.dialog__head", h("span.title", `${r.photo_title} · Edit ${r.number}`),
      h("button.btn.btn--ghost.btn--icon", { type: "button", "aria-label": "Close", onclick: () => dlg.close() }, icon("x"))),
    h("div.dialog__body",
      compareView(fileUrl(photo.preview_asset_id), fileUrl(r.composite_preview_asset_id), { before: "Original", after: "Your edit" }),
      h("p.mono-sm.muted", `Saved at the original size: ${r.composite.width} x ${r.composite.height}. Your upload: ${r.upload.width} x ${r.upload.height} ${r.upload.format}.`),
      h("a.btn", { href: fileUrl(r.composite_asset_id, true) }, icon("download"), "Download full size")));
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
}

async function reopen(r) {
  try {
    await api(`${base}/revisions/${r.id}/reopen`, { method: "POST" });
    go(`/r/${slug}/photo/${r.photo_id}`);
  } catch (ex) { toast("Couldn't reopen", ex.message, "danger"); }
}

// ---------------------------------------------------------------- photo

async function renderPhoto(photoId) {
  let photo;
  try { photo = await api(`${base}/photos/${photoId}`); }
  catch (ex) {
    frame(h("a.crumb", { href: `/r/${slug}`, onclick: back }, icon("back"), "My photos"), alertBox("danger", "Photo not available", ex.message));
    return;
  }
  const [label, kind] = LABELS[photo.state] || [photo.state_label, null];
  const body = h("div.col");
  frame(
    h("a.crumb", { href: `/r/${slug}`, onclick: back }, icon("back"), "My photos"),
    h("div.page-head", h("h1.heading", photo.title), h("div.row", badge(label, kind), h("span.mono-sm.muted", `${photo.width} x ${photo.height} ${photo.format}`))),
    photo.change_note ? alertBox("warning", "The organiser asked for changes", photo.change_note) : null,
    !photo.active ? alertBox("info", "This photo was given to someone else", "Your earlier edits are kept in My edits.") : null,
    body);
  await fillPhotoBody(body, photo);
}

function back(e) { e.preventDefault(); go(`/r/${slug}`); }

async function fillPhotoBody(el, photo) {
  const draft = photo.draft;
  const latest = photo.latest_revision;

  if (draft && (draft.status === "processing" || draft.status === "submitting")) {
    clear(el, processingPanel(draft.status === "submitting" ? "Saving your edit at full size" : "Comparing your edit with the original"));
    await pollDraft(draft.id);
    return renderPhoto(photo.id);
  }
  if (draft && (draft.status === "ready" || draft.status === "blocked")) return mountEditor(el, photo, draft);

  const steps = stepsPanel(photo, draft);
  if (latest && (!draft || draft.status === "submitted")) {
    const [lab, k] = photo.state === "final" ? ["Final version", "success"] : REV_LABELS[latest.status] || [latest.status, null];
    clear(el,
      h("section.panel.col",
        h("div.spread", h("h2.panel__title", `Your latest edit · Edit ${latest.number}`), badge(lab, k)),
        compareView(fileUrl(photo.preview_asset_id), fileUrl(latest.composite_preview_asset_id), { before: "Original", after: "Your edit" }),
        photo.active ? h("div.row",
          h("button.btn", { type: "button", onclick: () => reopen({ id: latest.id, photo_id: photo.id }) }, "Adjust this edit")) : null),
      photo.active ? steps : null);
    return;
  }
  clear(el, draft && draft.status === "failed"
    ? alertBox("danger", "Couldn't process your edit", draft.error || "Upload it again.") : null, steps);
}

function processingPanel(title) {
  return h("section.panel.col", alertBox("live", title, "This usually takes a few seconds. You can leave this page; it carries on."),
    h("div.progress.progress--indeterminate", h("div.progress__track", h("div.progress__fill"))));
}

function pollDraft(id) {
  return pollUntil(() => api(`${base}/drafts/${id}`), (d) => !["processing", "submitting"].includes(d.status), { ms: 1200 });
}

function stepsPanel(photo, draft) {
  const fileInput = h("input", { type: "file", accept: "image/jpeg,image/png,image/webp,image/tiff", hidden: true });
  const status = h("div.col");
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    if (!file) return;
    const bar = h("div.progress__fill");
    const meta = h("span", "0%");
    clear(status, h("div.progress", h("div.progress__meta", h("span", `Uploading ${file.name}`), meta), h("div.progress__track", bar)));
    try {
      const d = await upload(`${base}/photos/${photo.id}/uploads`, file, {
        onProgress: (f) => { bar.style.width = `${Math.round(f * 100)}%`; meta.textContent = `${Math.round(f * 100)}%`; },
      });
      clear(status, processingPanel("Comparing your edit with the original"));
      await pollDraft(d.id);
      renderPhoto(photo.id);
    } catch (ex) {
      clear(status, alertBox("danger", "Upload failed", ex.message));
    }
  });
  const downloaded = photo.downloaded;
  const step = (n, title, help, action, state) =>
    h(`div.step${state ? ".step--" + state : ""}`, h("span.step__n", state === "done" ? "✓" : String(n)),
      h("div.col", { style: { gap: "6px" } }, h("span.title", title), h("span.small.muted", help), action));
  return h("section.panel.col",
    h("h2.panel__title", "Your next steps"),
    step(1, "Download the original", "Always start from this file, not a screenshot or a chat copy.",
      h("div", h("a.btn" + (downloaded ? "" : ".btn--primary"), {
        href: `${base}/photos/${photo.id}/original`, download: "",
        onclick: () => setTimeout(() => renderPhoto(photo.id), 1500),
      }, icon("download"), "Download original")), downloaded ? "done" : "current"),
    step(2, "Edit in your usual app", "Lightroom, Photoshop, Snapseed, Meitu: anything. Don't crop or rotate. Export as JPEG or PNG at full size if you can.", null, downloaded ? "current" : null),
    step(3, "Upload your edit", "Smart Merge finds what you changed and puts it onto the full-resolution original.",
      h("div.col", h("div", h("button.btn" + (downloaded ? ".btn--primary" : ""), { type: "button", onclick: () => fileInput.click() }, icon("upload"), draft ? "Upload again" : "Upload your edit")), fileInput, status)),
    step(4, "Check and submit", "Look at the result, fix anything with the brush, then submit."));
}

// ---------------------------------------------------------------- editor

async function mountEditor(el, photo, draft) {
  let alignUi = null;
  const warnings = h("div.col");
  const holder = h("div.col", processingPanel("Loading the editor"));
  const smart = h("section.panel.col");
  const lineUp = h("section.panel.col");
  const submitBox = h("section.panel.col");
  clear(el, warnings, holder, smart, lineUp, submitBox);

  const editor = new MergeEditor(draft, {
    notice: (m) => toast(m),
    onAlignChange: () => alignUi && alignUi.sync(),
  });
  try { await editor.load(); }
  catch (ex) { clear(holder, alertBox("danger", "Couldn't open the editor", ex.message)); return; }
  clear(holder, editor.root);

  const level = { info: "info", warning: "warning", blocker: "danger" };
  const showWarnings = (d) => clear(warnings, ...(d.warnings || []).map((w) =>
    alertBox(level[w.level] || "info", w.level === "blocker" ? "Needs your attention" : w.level === "warning" ? "Check this" : "Note", w.message)));
  showWarnings(draft);

  const refreshDraft = async (startMsg, call) => {
    editor.setBusy(startMsg);
    try {
      await call();
      const d = await pollDraft(draft.id);
      if (d.status === "failed") throw new ApiError(0, d.error || "Processing failed.");
      const editChanged = d.edit_preview_asset_id !== draft.edit_preview_asset_id;
      Object.assign(draft, d);
      editor.draft = draft;
      if (editChanged) await editor.replaceEdit(d.edit_preview_asset_id);
      if (d.mask) await editor.replaceMask(d.mask.asset_id);
      showWarnings(d);
      renderSmart(); renderSubmit();
    } catch (ex) { toast("Couldn't update", ex.message, "danger"); }
    editor.setBusy(null);
  };

  function renderSmart() {
    const changed = draft.analysis && typeof draft.analysis.changed_fraction === "number"
      ? `${Math.round(draft.analysis.changed_fraction * 100)}% of the picture changed.` : "";
    const sens = (s, t) => h("button.btn", { type: "button", "aria-pressed": String(draft.sensitivity === s),
      onclick: () => refreshDraft("Finding your changes", () => api(`${base}/drafts/${draft.id}/smart`, { method: "POST", body: { sensitivity: s } })) }, t);
    clear(smart,
      h("h2.panel__title", "Smart Merge", changed ? h("span.mono-sm", changed) : null),
      h("p.small.muted", "Smart Merge compares your edit with the original and selects what changed. Choose how fine to look, then fix anything with the brush. Detecting again replaces your brush work (Undo brings it back)."),
      h("div.seg", { role: "group", "aria-label": "Sensitivity" }, sens("low", "Low"), sens("medium", "Medium"), sens("high", "High")));
  }


  function renderLineUp() {
    const blocked = draft.status === "blocked";
    const start = h(blocked ? "button.btn.btn--primary" : "button.btn", { type: "button", onclick: openAlign }, "Line up by hand");
    clear(lineUp,
      h("h2.panel__title", "Line-up"),
      h("p.small.muted", blocked
        ? "Your edit couldn't be lined up with the original automatically. Line it up by hand, or upload an export without cropping or rotation."
        : "If parts look doubled or shifted, line your edit up by hand."),
      h("div.row", start,
        draft.alignment && draft.alignment.placement && draft.alignment.placement.method === "manual"
          ? h("button.btn.btn--ghost", { type: "button", onclick: () => refreshDraft("Lining up automatically", () => api(`${base}/drafts/${draft.id}/align`, { method: "POST", body: { auto: true } })) }, "Use automatic line-up")
          : null));
  }

  async function openAlign() {
    await editor.startAlign();
    const a = editor.align;
    const scaleOut = h("span.mono-sm");
    const scale = h("input", { type: "range", min: 20, max: 300, step: 0.5, "aria-label": "Size of your edit",
      oninput: (e) => { a.scale = Number(e.target.value) / 100; sync(); editor.requestDraw(); } });
    const nudge = (dx, dy, label) => h("button.btn.btn--sm", { type: "button", "aria-label": label,
      onclick: () => { a.dx += dx / editor.W; a.dy += dy / editor.H; sync(); editor.requestDraw(); } }, label);
    const sync = () => { scale.value = String(a.scale * 100); scaleOut.textContent = `${(a.scale * 100).toFixed(1)}%`; };
    alignUi = { sync };
    sync();
    clear(lineUp,
      h("h2.panel__title", "Line up by hand"),
      h("p.small.muted", "Your edit shows on top at half strength. Drag it until it sits exactly over the original, then apply."),
      h("div.toolbar",
        h("button.btn", { type: "button", onclick: () => { a.rotate = (a.rotate + 1) % 4; editor.requestDraw(); } }, icon("refresh"), "Rotate"),
        h("label.slider", h("span.label", h("span", "Size"), scaleOut), scale)),
      h("div.row", nudge(0, -1, "Up"), nudge(0, 1, "Down"), nudge(-1, 0, "Left"), nudge(1, 0, "Right")),
      h("div.row",
        h("button.btn.btn--primary", { type: "button", onclick: async () => {
          const values = { rotate: a.rotate, scale: a.scale, dx: a.dx, dy: a.dy };
          editor.stopAlign(); alignUi = null;
          await refreshDraft("Lining up your edit", () => api(`${base}/drafts/${draft.id}/align`, { method: "POST", body: values }));
          renderLineUp();
        } }, "Apply line-up"),
        h("button.btn", { type: "button", onclick: () => { editor.stopAlign(); alignUi = null; renderLineUp(); } }, "Cancel")));
  }

  function renderSubmit() {
    const note = h("textarea.input", { maxlength: 500, placeholder: "Optional: anything the organiser should know", rows: 2 });
    const btn = h("button.btn.btn--primary.btn--lg.btn--block", { type: "button", disabled: draft.status === "blocked" && !(draft.alignment.placement && draft.alignment.placement.method === "manual") }, icon("check"), "Submit edit");
    const status = h("div.col");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      clear(status);
      try {
        let maskId = draft.mask && draft.mask.id;
        if (editor.dirty || !maskId) {
          const blob = await editor.toPngBlob();
          const res = await upload(`${base}/drafts/${draft.id}/mask`, new File([blob], "mask.png", { type: "image/png" }),
            { fields: { source: draft.mask && draft.mask.source === "smart" ? "hybrid" : "manual" } });
          maskId = res.mask_id;
          editor.markSaved();
        }
        await api(`${base}/drafts/${draft.id}/submit`, { method: "POST", body: { mask_id: maskId, note: note.value } });
        clear(el, processingPanel("Saving your edit at full size"));
        const d = await pollDraft(draft.id);
        if (d.status !== "submitted") throw new ApiError(0, d.error || "Couldn't save your edit. Try again.");
        submitted(el, photo);
      } catch (ex) {
        clear(status, alertBox("danger", "Couldn't submit", ex.message));
        btn.disabled = false;
      }
    });
    clear(submitBox,
      h("h2.panel__title", "Submit"),
      h("p.small.muted", "Your edit is saved at the original's full size. The original and all earlier edits stay untouched."),
      h("label.field", h("span.label", "Note"), note), status, btn);
  }

  renderSmart(); renderLineUp(); renderSubmit();
}

async function submitted(el, photo) {
  const { photos } = await api(`${base}/photos`);
  const next = photos.find((p) => p.id !== photo.id && ["assigned", "editing", "needs_changes"].includes(p.state));
  clear(el, h("section.panel.panel--bracket.col",
    alertBox("success", "Edit submitted", "The organiser will review it. You can still adjust it from My edits."),
    h("div.row",
      next ? h("button.btn.btn--primary", { type: "button", onclick: () => go(`/r/${slug}/photo/${next.id}`) }, "Next photo", icon("next")) : null,
      h("button.btn", { type: "button", onclick: () => go(`/r/${slug}`) }, "My photos"))));
}

route();
