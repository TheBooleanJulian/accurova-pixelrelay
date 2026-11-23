// Owner app: /owner, /owner/new, /owner/p/{id}?tab=...
import {
  alertBox, api, badge, clear, confirmDialog, copyText, fileUrl, fmtDate, fmtTime, h, icon,
  pollUntil, progressBar, STATE_INFO, stateBadge, toast, topbar, upload,
} from "./core.js";
import { compareView } from "./compare.js";

const app = document.getElementById("app");
const TABS = [["overview", "Overview"], ["photos", "Photos"], ["participants", "Participants"], ["assignments", "Assignments"], ["review", "Review"]];

function go(path) { history.pushState(null, "", path); route(); }
window.addEventListener("popstate", () => route());
const link = (href, ...kids) => h("a", { href, onclick: (e) => { e.preventDefault(); go(href); } }, ...kids);
const crumbTo = (href, text) => { const a = link(href, icon("back"), text); a.className = "crumb"; return a; };

function frame(...content) {
  clear(app,
    topbar({ home: "/owner", actions: [h("button.btn.btn--ghost.btn--sm", { type: "button", onclick: signOut, title: "Sign out" }, icon("logout"), h("span.sr-only", "Sign out"))] }),
    h("main.main", ...content));
}

async function signOut() {
  await api("/api/auth/logout", { method: "POST" });
  location.href = "/login";
}

async function route() {
  try { await api("/api/auth/me"); }
  catch { location.replace("/login"); return; }
  const parts = location.pathname.split("/").slice(2);
  if (parts[0] === "new") return renderNew();
  if (parts[0] === "p" && parts[1]) return renderProject(parts[1], new URLSearchParams(location.search).get("tab") || "overview");
  return renderList();
}

// ---------------------------------------------------------------- list / new

async function renderList() {
  const { projects } = await api("/api/projects");
  frame(
    h("div.spread", h("div.page-head", h("span.label", "Organiser"), h("h1.display-lg", "Projects")),
      h("button.btn.btn--primary", { type: "button", onclick: () => go("/owner/new") }, icon("plus"), "New project")),
    projects.length
      ? h("div.cards", projects.map((p) => link(`/owner/p/${p.id}`,
        h("div.card__body",
          h("span.card__title", p.name),
          h("span.mono-sm.muted", [p.shoot_date ? `Shot ${fmtDate(p.shoot_date)}` : null, `${p.participants} people`, `${p.progress.total} photos`].filter(Boolean).join(" · ")),
          progressBar("Done", p.progress.done, p.progress.total)),
        p.status === "archived" ? badge("Archived") : icon("next"))).map((a) => { a.className = "card"; return a; }))
      : h("div.empty.panel", icon("image", 20), h("span.title", "No projects yet"), h("span.small", "Create one for your next group shoot."),
        h("button.btn.btn--primary", { type: "button", onclick: () => go("/owner/new") }, "New project")));
}

function renderNew() {
  const name = h("input.input#name", { required: true, maxlength: 120, placeholder: "Winter group shoot" });
  const date = h("input.input", { type: "date" });
  const deadline = h("input.input", { type: "date" });
  const people = h("textarea.input", { rows: 5, placeholder: "Ava\nBen\nCy" });
  const err = h("div.col");
  const btn = h("button.btn.btn--primary.btn--lg", { type: "submit" }, "Create project");
  frame(
    crumbTo("/owner", "Projects"),
    h("div.page-head", h("h1.display-lg", "New project"), h("p.muted", "Name it and list who's editing. You'll add photos next.")),
    h("form.panel.col", {
      onsubmit: async (e) => {
        e.preventDefault();
        btn.disabled = true;
        try {
          const p = await api("/api/projects", { method: "POST", body: {
            name: name.value, shoot_date: date.value || null, deadline: deadline.value || null,
            participants: people.value.split("\n").map((s) => s.trim()).filter(Boolean),
          } });
          go(`/owner/p/${p.id}?tab=photos`);
        } catch (ex) { clear(err, alertBox("danger", "Couldn't create the project", ex.message)); btn.disabled = false; }
      },
    },
    h("label.field", h("span.label", "Project name"), name),
    h("div.row", h("label.field.grow", h("span.label", "Shoot date (optional)"), date), h("label.field.grow", h("span.label", "Edits due (optional)"), deadline)),
    h("label.field", h("span.label", "Participants"), people, h("span.field__help", "One name per line, as people will recognise themselves. You can add more later.")),
    err, h("div", btn)));
  name.focus();
}

// ---------------------------------------------------------------- project

let current = null; // { project, tab }

async function renderProject(id, tab) {
  let project;
  try { project = await api(`/api/projects/${id}`); }
  catch (ex) { frame(crumbTo("/owner", "Projects"), alertBox("danger", "Project not found", ex.message)); return; }
  current = { project, tab };
  const body = h("div.col", { style: { gap: "var(--space-5)" } });
  const tabs = h("div.tabs", { role: "tablist" }, TABS.map(([key, label]) => h("button.tab", {
    role: "tab", "aria-selected": String(key === tab),
    onclick: () => go(`/owner/p/${id}?tab=${key}`),
    onkeydown: (e) => {
      const i = TABS.findIndex(([k]) => k === tab);
      if (e.key === "ArrowRight") go(`/owner/p/${id}?tab=${TABS[(i + 1) % TABS.length][0]}`);
      if (e.key === "ArrowLeft") go(`/owner/p/${id}?tab=${TABS[(i + TABS.length - 1) % TABS.length][0]}`);
    },
  }, label, key === "review" && project.progress.states.submitted ? h("span.count", `(${project.progress.states.submitted})`) : null)));
  frame(
    crumbTo("/owner", "Projects"),
    h("div.spread",
      h("div.page-head", h("h1.display-lg", project.name),
        h("span.mono-sm.muted", [project.shoot_date ? `Shot ${fmtDate(project.shoot_date)}` : null, project.deadline ? `Edits due ${fmtDate(project.deadline)}` : null].filter(Boolean).join(" · ") || "No dates set")),
      h("button.btn.btn--sm", { type: "button", onclick: () => editDetails(project) }, "Edit details")),
    project.status === "archived" ? alertBox("info", "Archived", "Contributors can't open this project. Edit details to make it active again.") : null,
    pinPanel(project),
    tabs, body);
  const fill = { overview: fillOverview, photos: fillPhotos, participants: fillParticipants, assignments: fillAssignments, review: fillReview }[tab] || fillOverview;
  try { await fill(body, project); }
  catch (ex) { clear(body, alertBox("danger", "Couldn't load", ex.message)); }
}

function reload() { renderProject(current.project.id, current.tab); }

function pinPanel(p) {
  const message = `Join our photo edit room on PixelRelay:\n${p.room_url}\nRoom PIN: ${p.pin}`;
  return h("section.panel.panel--bracket.col",
    h("div.spread",
      h("div.col", { style: { gap: "4px" } }, h("span.label", "Room PIN"), h("span.pin", p.pin)),
      h("div.row",
        h("button.btn", { type: "button", onclick: () => copyText(message, "Invite message copied") }, icon("copy"), "Copy invite"),
        h("button.btn", { type: "button", onclick: () => copyText(p.room_url, "Link copied") }, "Copy link"),
        h("button.btn.btn--ghost", { type: "button", onclick: () => rotatePin(p) }, icon("refresh"), "New PIN"))),
    h("span.share-url", p.room_url),
    p.suspicious_pin_activity
      ? alertBox("warning", "Many wrong PIN attempts", "Someone may be guessing the PIN. Make a new PIN and send it to your group again.")
      : h("span.small.muted", "Share the link and PIN in your group chat. The PIN opens this project only; it can't change settings. People who joined with a PIN need the new one after you change it."));
}

async function rotatePin(p) {
  if (!(await confirmDialog("Make a new Room PIN?", "Everyone who joined with the old PIN will need the new one. Personal invitation links keep working.", { confirm: "New PIN" }))) return;
  await api(`/api/projects/${p.id}/pin/rotate`, { method: "POST" });
  toast("New PIN ready", "Send it to your group.", "success");
  reload();
}

function editDetails(p) {
  const name = h("input.input", { value: p.name, maxlength: 120 });
  const date = h("input.input", { type: "date", value: p.shoot_date || "" });
  const deadline = h("input.input", { type: "date", value: p.deadline || "" });
  const desc = h("textarea.input", { rows: 3, maxlength: 2000 }, p.description || "");
  const archived = h("input", { type: "checkbox", role: "switch", checked: p.status === "archived" });
  const err = h("div.col");
  const dlg = h("dialog.dialog", { "aria-label": "Edit details", style: { width: "min(520px, calc(100vw - 16px))" } },
    h("div.dialog__head", h("span.title", "Project details"), h("button.btn.btn--ghost.btn--icon", { type: "button", "aria-label": "Close", onclick: () => dlg.close() }, icon("x"))),
    h("form.dialog__body", {
      onsubmit: async (e) => {
        e.preventDefault();
        try {
          await api(`/api/projects/${p.id}`, { method: "PATCH", body: {
            name: name.value, shoot_date: date.value || null, deadline: deadline.value || null,
            description: desc.value, status: archived.checked ? "archived" : "active",
          } });
          dlg.close();
          reload();
        } catch (ex) { clear(err, alertBox("danger", "Couldn't save", ex.message)); }
      },
    },
    h("label.field", h("span.label", "Name"), name),
    h("div.row", h("label.field.grow", h("span.label", "Shoot date"), date), h("label.field.grow", h("span.label", "Edits due"), deadline)),
    h("label.field", h("span.label", "Notes for contributors"), desc),
    h("label.toggle", archived, "Archived"),
    err, h("div.row", h("button.btn.btn--primary", { type: "submit" }, "Save"))));
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
}

// ---------------------------------------------------------------- overview

const EVENT_TEXT = {
  project_created: "Project created", participants_added: "Participants added", participant_removed: "Participant removed",
  photo_added: "Photo added", photo_assigned: "Photo assigned", photo_unassigned: "Photo unassigned",
  pin_rotated: "Room PIN changed", participant_identified: "Joined", invitation_redeemed: "Joined by invitation",
  availability_set: "Set availability", edit_uploaded: "Uploaded an edit", edit_submitted: "Submitted an edit",
  edit_reopened: "Reopened an edit", approved: "Edit approved", changes_requested: "Changes requested",
  final_selected: "Final version chosen", final_cleared: "Final version cleared", finals_exported: "Finals exported",
  suspicious_pin_attempts: "Many wrong PIN attempts", device_revoked: "Device signed out", devices_revoked: "Devices signed out",
  project_updated: "Details updated",
};

async function fillOverview(el, p) {
  const s = p.progress.states;
  const [{ members }, { events }] = await Promise.all([api(`/api/projects/${p.id}/members`), api(`/api/projects/${p.id}/activity`)]);
  const tabLink = (tab, text) => h("button.btn.btn--primary", { type: "button", onclick: () => go(`/owner/p/${p.id}?tab=${tab}`) }, text);
  let next;
  if (!members.length) next = alertBox("info", "Next: add participants", "List the people who'll edit.", tabLink("participants", "Add participants"));
  else if (!p.progress.total) next = alertBox("info", "Next: add photographs", "Upload the originals you want edited.", tabLink("photos", "Add photographs"));
  else if (s.unassigned) next = alertBox("info", `Next: share out ${s.unassigned} photo${s.unassigned === 1 ? "" : "s"}`, "Preview an even split, adjust it, then publish.", tabLink("assignments", "Share out photos"));
  else if (s.submitted) next = alertBox("live", `${s.submitted} edit${s.submitted === 1 ? "" : "s"} waiting for review`, "Approve, ask for changes, or choose final versions.", tabLink("review", "Review edits"));
  else if (p.progress.done === p.progress.total) next = alertBox("success", "Everything is done", "Choose final versions and export them.", tabLink("review", "Export finals"));
  else next = alertBox("info", "Edits in progress", "Contributors are working. Their edits appear in Review as they submit.");
  const stat = (n, label) => h("div.panel", h("div.stat", String(n)), h("span.label", label));
  clear(el,
    next,
    h("section.panel.col", progressBar("Done (approved or final)", p.progress.done, p.progress.total),
      progressBar("Submitted", p.progress.submitted, p.progress.total)),
    h("div.stats", stat(s.unassigned, "Unassigned"), stat(s.assigned + s.editing + s.needs_changes, "In progress"),
      stat(s.submitted, "Awaiting review"), stat(s.approved + s.final, "Done")),
    h("section.panel.col",
      h("h2.panel__title", "Activity"),
      events.length ? h("div.table-wrap", h("table.table", h("tbody", events.slice(0, 40).map((e) => h("tr",
        h("td", fmtTime(e.created_at)),
        h("td", EVENT_TEXT[e.kind] || e.kind),
        h("td", e.actor_name || (e.actor_type === "owner" ? "You" : e.actor_type === "system" ? "System" : "")),
        h("td", e.photo_title || ""))))))
        : h("p.small.muted", "Nothing yet.")));
}

// ---------------------------------------------------------------- photos

let photoFilter = "all";

async function fillPhotos(el, p) {
  const { photos } = await api(`/api/projects/${p.id}/photos`);
  const input = h("input", { type: "file", multiple: true, accept: "image/jpeg,image/png,image/webp,image/tiff", hidden: true });
  const queue = h("div.uploads");
  const drop = h("div.drop", { role: "button", tabindex: 0, onclick: () => input.click(),
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } } },
  icon("upload", 20), h("span.title", "Add photographs"), h("span.small.muted", "Drop originals here or choose files. JPEG, PNG, WebP or TIFF. Originals are kept exactly as uploaded."));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drop--over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("drop--over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("drop--over"); uploadMany([...e.dataTransfer.files]); });
  input.addEventListener("change", () => uploadMany([...input.files]));

  async function uploadMany(files) {
    let ok = 0;
    for (const f of files) {
      const fill = h("div.progress__fill");
      const meta = h("span", "waiting");
      const row = h("div.progress", h("div.progress__meta", h("span", f.name), meta), h("div.progress__track", fill));
      queue.append(row);
      try {
        await upload(`/api/projects/${p.id}/photos`, f, { onProgress: (x) => { fill.style.width = `${Math.round(x * 100)}%`; meta.textContent = `${Math.round(x * 100)}%`; } });
        meta.textContent = "added";
        row.classList.add("progress--success");
        ok++;
      } catch (ex) {
        meta.textContent = ex.message;
        row.classList.add("progress--danger");
        fill.style.width = "100%";
      }
    }
    if (ok) { toast(`${ok} photo${ok === 1 ? "" : "s"} added`, "Previews appear in a moment.", "success"); setTimeout(reload, 800); }
  }

  const filters = [["all", "All"], ...Object.entries(STATE_INFO).map(([k, [t]]) => [k, t]), ["off", "Not for editing"]];
  const shown = photos.filter((x) => photoFilter === "all" || (photoFilter === "off" ? !x.available : x.state === photoFilter));
  const count = (k) => k === "all" ? photos.length : k === "off" ? photos.filter((x) => !x.available).length : photos.filter((x) => x.state === k).length;
  clear(el,
    drop, input, queue,
    photos.length ? h("div.chips", { role: "group", "aria-label": "Filter photos" }, filters.filter(([k]) => k === "all" || count(k)).map(([k, t]) =>
      h("button.chip", { type: "button", "aria-pressed": String(photoFilter === k), onclick: () => { photoFilter = k; fillPhotos(el, p); } }, `${t} ${count(k)}`))) : null,
    photos.length
      ? h("div.gallery", shown.map((x) => photoTile(p, x)))
      : h("p.small.muted", "No photos yet."));
  if (photos.some((x) => x.processing === "pending")) setTimeout(() => { if (current && current.tab === "photos") fillPhotos(el, p); }, 2000);
}

function photoTile(p, x) {
  return h(`button.tile${x.available ? "" : ".tile--off"}`, { type: "button", onclick: () => openPhoto(p, x.id) },
    x.thumb_asset_id ? h("img", { src: fileUrl(x.thumb_asset_id), alt: "", loading: "lazy" })
      : h("div.tile__ph", x.processing === "failed" ? "Couldn't read" : "Preparing"),
    h("div.tile__meta", h("span.tile__name", x.title), h("div.row", stateBadge(x.state)),
      h("span.small.muted", x.assignee ? x.assignee.name : x.available ? "Nobody yet" : "Not for editing")));
}

async function openPhoto(p, photoId, selected) {
  const [d, { members }] = await Promise.all([api(`/api/projects/${p.id}/photos/${photoId}`), api(`/api/projects/${p.id}/members`)]);
  const photo = d.photo;
  const revs = d.revisions;
  const sel = revs.find((r) => r.id === selected) || revs.find((r) => r.id === d.final_revision_id) || revs[0];
  const close = () => dlg.close();
  const refresh = (rid) => { dlg.close(); openPhoto(p, photoId, rid); };

  const assignSel = h("select.input", { "aria-label": "Assigned to" },
    h("option", { value: "" }, "Nobody"),
    members.map((m) => h("option", { value: m.id, selected: photo.assignee && photo.assignee.id === m.id }, `${m.display_name}${m.opted_in ? "" : " (not opted in)"}`)));
  assignSel.addEventListener("change", async () => {
    const prev = photo.assignee;
    if (prev && revs.some((r) => r.member_id === prev.id)) {
      if (!(await confirmDialog(`Reassign from ${prev.name}?`, `${prev.name}'s submitted edits are kept and stay visible here.`, { confirm: "Reassign" }))) { assignSel.value = prev.id; return; }
    }
    try {
      await api(`/api/projects/${p.id}/photos/${photoId}/assignment`, { method: "PUT", body: { member_id: assignSel.value || null } });
      toast("Assignment saved", "", "success");
      refresh(sel && sel.id);
    } catch (ex) { toast("Couldn't assign", ex.message, "danger"); }
  });
  const avail = h("input", { type: "checkbox", role: "switch", checked: photo.available, onchange: async (e) => {
    await api(`/api/projects/${p.id}/photos/${photoId}`, { method: "PATCH", body: { available: e.target.checked } });
    toast(e.target.checked ? "Available for editing" : "Not for editing", "", "success");
  } });

  const decide = async (r, kind) => {
    try {
      if (kind === "approve") await api(`/api/projects/${p.id}/revisions/${r.id}/approve`, { method: "POST", body: {} });
      else if (kind === "changes") {
        const note = await askNote();
        if (note === null) return;
        await api(`/api/projects/${p.id}/revisions/${r.id}/request-changes`, { method: "POST", body: { note } });
      } else if (kind === "final") await api(`/api/projects/${p.id}/photos/${photoId}/final`, { method: "PUT", body: { revision_id: r.id } });
      else if (kind === "unfinal") await api(`/api/projects/${p.id}/photos/${photoId}/final`, { method: "PUT", body: { revision_id: null } });
      refresh(r.id);
    } catch (ex) { toast("Couldn't save", ex.message, "danger"); }
  };

  const REV = { submitted: ["Awaiting review", "magenta"], needs_changes: ["Needs changes", "warning"], approved: ["Approved", "success"] };
  const revList = revs.length ? h("div.col", revs.map((r) => {
    const isFinal = r.id === d.final_revision_id;
    const [t, k] = isFinal ? ["Final", "success"] : REV[r.status];
    return h(`div.panel${r.id === (sel && sel.id) ? ".panel--raised" : ""}.col`, { style: { gap: "8px" } },
      h("div.spread", h("span.title", `Edit ${r.number} · ${r.member_name}`), badge(t, k)),
      h("span.mono-sm.muted", `${fmtTime(r.created_at)} · upload ${r.upload.width}x${r.upload.height} ${r.upload.format} · saved ${r.composite.width}x${r.composite.height}`),
      r.note ? h("p.small", `"${r.note}"`) : null,
      r.warnings.filter((w) => w.level !== "info").map((w) => h("p.small.muted", `! ${w.message}`)),
      h("div.row",
        r.id !== (sel && sel.id) ? h("button.btn.btn--sm", { type: "button", onclick: () => refresh(r.id) }, "Compare") : null,
        r.status !== "approved" ? h("button.btn.btn--sm.btn--primary", { type: "button", onclick: () => decide(r, "approve") }, icon("check"), "Approve") : null,
        r.status !== "needs_changes" ? h("button.btn.btn--sm", { type: "button", onclick: () => decide(r, "changes") }, "Ask for changes") : null,
        r.status === "approved" && !isFinal ? h("button.btn.btn--sm", { type: "button", onclick: () => decide(r, "final") }, "Choose as final") : null,
        isFinal ? h("button.btn.btn--sm.btn--ghost", { type: "button", onclick: () => decide(r, "unfinal") }, "Clear final") : null,
        h("a.btn.btn--sm.btn--ghost", { href: fileUrl(r.composite_asset_id, true) }, icon("download"), "Full size")));
  })) : h("p.small.muted", "No edits submitted yet.");

  const dlg = h("dialog.dialog", { "aria-label": photo.title },
    h("div.dialog__head", h("div.row", h("span.title", photo.title), stateBadge(photo.state)),
      h("button.btn.btn--ghost.btn--icon", { type: "button", "aria-label": "Close", onclick: close }, icon("x"))),
    h("div.dialog__body",
      h("div.layout-2",
        h("div.col",
          sel ? compareView(fileUrl(photo.preview_asset_id), fileUrl(sel.composite_preview_asset_id), { before: "Original", after: `Edit ${sel.number}` })
            : photo.preview_asset_id ? h("img", { src: fileUrl(photo.preview_asset_id), alt: photo.title, style: { borderRadius: "6px" } }) : null,
          h("span.mono-sm.muted", `Original ${photo.width} x ${photo.height} ${photo.format}`),
          h("div.row", h("a.btn.btn--sm", { href: fileUrl(photo.original_asset_id, true) }, icon("download"), "Original"))),
        h("div.col",
          h("label.field", h("span.label", "Assigned to"), assignSel),
          h("label.toggle", avail, "Available for editing"),
          h("h3.label", "Edits"), revList,
          d.assignments.length ? h("details", h("summary.label", "Assignment history"),
            h("ul.small.muted", d.assignments.map((a) => h("li", `${a.member_name}: ${fmtTime(a.created_at)}${a.ended_at ? ` to ${fmtTime(a.ended_at)} (${a.ended_reason})` : " (current)"}${a.original_downloaded_at ? ", original downloaded" : ""}`)))) : null))));
  dlg.addEventListener("close", () => { dlg.remove(); if (current) reload(); });
  document.body.append(dlg);
  dlg.showModal();
}

function askNote() {
  return new Promise((resolve) => {
    const note = h("textarea.input", { rows: 3, maxlength: 500, placeholder: "For example: softer skin edges around the jaw" });
    let result = null;
    const dlg = h("dialog.dialog", { "aria-label": "Ask for changes", style: { width: "min(480px, calc(100vw - 16px))" } },
      h("form.dialog__body", { onsubmit: (e) => { e.preventDefault(); if (!note.value.trim()) return; result = note.value.trim(); dlg.close(); } },
        h("span.title", "What should change?"),
        h("p.small.muted", "The contributor sees this note next to the photo."),
        note,
        h("div.row", { style: { justifyContent: "flex-end" } },
          h("button.btn", { type: "button", onclick: () => dlg.close() }, "Cancel"),
          h("button.btn.btn--primary", { type: "submit" }, "Send"))));
    dlg.addEventListener("close", () => { dlg.remove(); resolve(result); });
    document.body.append(dlg);
    dlg.showModal();
    note.focus();
  });
}

// ---------------------------------------------------------------- participants

async function fillParticipants(el, p) {
  const { members } = await api(`/api/projects/${p.id}/members`);
  const names = h("textarea.input", { rows: 3, placeholder: "One name per line" });
  const err = h("div.col");
  const patch = async (m, body) => {
    try { await api(`/api/projects/${p.id}/members/${m.id}`, { method: "PATCH", body }); }
    catch (ex) { toast("Couldn't save", ex.message, "danger"); }
  };
  const rows = members.map((m) => {
    const cap = h("input.input.input--mono", { type: "number", min: 0, max: 500, value: m.capacity ?? "", placeholder: "Any", "aria-label": `How many photos ${m.display_name} can edit`, style: { width: "88px" } });
    cap.addEventListener("change", () => patch(m, cap.value === "" ? { clear_capacity: true } : { capacity: Number(cap.value) }));
    const opted = h("input", { type: "checkbox", role: "switch", checked: m.opted_in, "aria-label": `${m.display_name} is editing`, onchange: (e) => patch(m, { opted_in: e.target.checked }) });
    return h("tr",
      h("td", h("div.col", { style: { gap: "2px" } }, h("span", m.display_name), m.invite_only ? h("span.mono-sm.muted", "personal link only") : null)),
      h("td", h("label.toggle", opted, h("span.sr-only", "Editing"))),
      h("td", cap),
      h("td.num", String(m.assigned)),
      h("td.num", String(m.submitted_photos)),
      h("td", h("button.btn.btn--sm.btn--ghost", { type: "button", onclick: () => devices(p, m) }, `${m.devices} device${m.devices === 1 ? "" : "s"}`)),
      h("td", h("div.row",
        h("button.btn.btn--sm", { type: "button", onclick: () => invite(p, m) }, "Invite link"),
        h("button.btn.btn--sm.btn--danger", { type: "button", onclick: async () => {
          if (!(await confirmDialog(`Remove ${m.display_name}?`, "Their photos become unassigned. Everything they submitted is kept.", { confirm: "Remove", danger: true }))) return;
          await api(`/api/projects/${p.id}/members/${m.id}`, { method: "DELETE" });
          reload();
        } }, "Remove"))));
  });
  clear(el,
    h("section.panel.col",
      h("h2.panel__title", "Add participants"),
      h("form.col", { onsubmit: async (e) => {
        e.preventDefault();
        const list = names.value.split("\n").map((s) => s.trim()).filter(Boolean);
        if (!list.length) return;
        try { await api(`/api/projects/${p.id}/members`, { method: "POST", body: { names: list } }); reload(); }
        catch (ex) { clear(err, alertBox("danger", "Couldn't add", ex.message)); }
      } }, names, err, h("div", h("button.btn.btn--primary", { type: "submit" }, "Add")))),
    members.length ? h("section.panel.panel--flush", h("div.table-wrap", h("table.table",
      h("thead", h("tr", h("th", "Name"), h("th", "Editing"), h("th", "How many"), h("th.num", "Assigned"), h("th.num", "Submitted"), h("th", "Devices"), h("th", ""))),
      h("tbody", rows)))) : h("p.small.muted", "No participants yet."),
    h("p.small.muted", "People choose their own name after entering the PIN, and can say how many photos they'll edit. A personal invite link ties a name to one person's device: after it's used, that name can't be picked with the PIN."));
}

async function invite(p, m) {
  const { url } = await api(`/api/projects/${p.id}/members/${m.id}/invite`, { method: "POST" });
  await copyText(url, `Invite link for ${m.display_name} copied`);
  toast("Send it only to them", "It works once and expires in 30 days. Making a new one cancels the old one.", "info");
}

async function devices(p, m) {
  const { sessions } = await api(`/api/projects/${p.id}/members/${m.id}/sessions`);
  const dlg = h("dialog.dialog", { "aria-label": `${m.display_name}'s devices`, style: { width: "min(520px, calc(100vw - 16px))" } },
    h("div.dialog__head", h("span.title", `${m.display_name}'s devices`), h("button.btn.btn--ghost.btn--icon", { type: "button", "aria-label": "Close", onclick: () => dlg.close() }, icon("x"))),
    h("div.dialog__body",
      sessions.length ? sessions.map((s) => h("div.spread",
        h("div.col", { style: { gap: "2px" } }, h("span", s.device_label || "Device"), h("span.mono-sm.muted", `${s.via === "pin" ? "Room PIN" : "Invite link"} · last seen ${fmtTime(s.last_seen_at)}`)),
        h("button.btn.btn--sm.btn--danger", { type: "button", onclick: async () => { await api(`/api/projects/${p.id}/sessions/${s.id}/revoke`, { method: "POST" }); dlg.close(); toast("Device signed out", "", "success"); } }, "Sign out")))
        : h("p.small.muted", "No remembered devices."),
      sessions.length > 1 ? h("button.btn.btn--danger", { type: "button", onclick: async () => { await api(`/api/projects/${p.id}/members/${m.id}/sessions/revoke`, { method: "POST" }); dlg.close(); toast("All devices signed out", "", "success"); } }, "Sign out everywhere") : null));
  dlg.addEventListener("close", () => { dlg.remove(); reload(); });
  document.body.append(dlg);
  dlg.showModal();
}

// ---------------------------------------------------------------- assignments

async function fillAssignments(el, p) {
  const [{ photos }, { members }] = await Promise.all([api(`/api/projects/${p.id}/photos`), api(`/api/projects/${p.id}/members`)]);
  const planBox = h("div.col");
  const opted = members.filter((m) => m.opted_in);
  const memberSelect = (value, onchange, allowNone = true) => {
    const s = h("select.input", { style: { minWidth: "150px" } },
      allowNone ? h("option", { value: "" }, "Nobody") : null,
      members.map((m) => h("option", { value: m.id, selected: m.id === value }, m.display_name)));
    s.addEventListener("change", () => onchange(s.value));
    return s;
  };

  async function preview() {
    const plan = await api(`/api/projects/${p.id}/assignments/preview`, { method: "POST" });
    if (!plan.assignments.length) {
      clear(planBox, alertBox("warning", "Nothing to share out", plan.unassigned.length
        ? "Nobody has room. Ask people to opt in, or raise their limits in Participants."
        : "Every available photo already has someone."));
      return;
    }
    const picks = new Map(plan.assignments.map((a) => [a.photo_id, a.member_id]));
    const sources = new Map();
    clear(planBox, h("section.panel.col",
      h("h2.panel__title", "Preview", h("span.mono-sm", plan.load.map((l) => `${l.member_name} ${l.count}`).join(" · "))),
      h("p.small.muted", "Nothing changes until you publish. Change any line first if you like."),
      h("div.table-wrap", h("table.table",
        h("thead", h("tr", h("th", "Photo"), h("th", "Goes to"))),
        h("tbody", plan.assignments.map((a) => h("tr", h("td", a.photo_title),
          h("td", memberSelect(a.member_id, (v) => { picks.set(a.photo_id, v); sources.set(a.photo_id, "manual"); }, false))))))),
      plan.unassigned.length ? alertBox("warning", `${plan.unassigned.length} photo${plan.unassigned.length === 1 ? "" : "s"} left over`, "Everyone's limit is reached. Assign these by hand below or raise limits.") : null,
      h("div.row",
        h("button.btn.btn--primary", { type: "button", onclick: async () => {
          try {
            const items = [...picks].map(([photo_id, member_id]) => ({ photo_id, member_id, source: sources.get(photo_id) || "auto" }));
            const r = await api(`/api/projects/${p.id}/assignments/publish`, { method: "POST", body: { assignments: items } });
            toast(`${r.assigned} photo${r.assigned === 1 ? "" : "s"} assigned`, "Contributors see them now.", "success");
            reload();
          } catch (ex) { toast("Couldn't publish", ex.message, "danger"); }
        } }, "Publish"),
        h("button.btn.btn--ghost", { type: "button", onclick: () => clear(planBox) }, "Discard"))));
  }

  const open = photos.filter((x) => x.available && x.state === "unassigned").length;
  clear(el,
    h("section.panel.col",
      h("h2.panel__title", "Share out photos"),
      h("p.small.muted", `${open} available photo${open === 1 ? "" : "s"} without anyone. ${opted.length} of ${members.length} people are editing. Photos go to whoever has the fewest so far, within each person's limit, in list order. Equal numbers of photos don't mean equal work, so adjust as you see fit.`),
      h("div", h("button.btn.btn--primary", { type: "button", disabled: !open || !opted.length, onclick: preview }, "Preview even split")),
      !opted.length && members.length ? h("p.small.muted", "Nobody has opted in yet. Turn on Editing for people in Participants, or let them choose when they join.") : null),
    planBox,
    photos.length ? h("section.panel.panel--flush", h("div.table-wrap", h("table.table",
      h("thead", h("tr", h("th", "Photo"), h("th", "Status"), h("th", "Assigned to"))),
      h("tbody", photos.filter((x) => x.available || x.assignee).map((x) => h("tr",
        h("td", x.title), h("td", stateBadge(x.state)),
        h("td", memberSelect(x.assignee ? x.assignee.id : "", async (v) => {
          if (x.assignee && x.revisions && !(await confirmDialog(`Reassign from ${x.assignee.name}?`, "Their submitted edits are kept.", { confirm: "Reassign" }))) { reload(); return; }
          try { await api(`/api/projects/${p.id}/photos/${x.id}/assignment`, { method: "PUT", body: { member_id: v || null } }); toast("Saved", "", "success"); }
          catch (ex) { toast("Couldn't assign", ex.message, "danger"); }
          reload();
        })))))))) : null);
}

// ---------------------------------------------------------------- review and export

async function fillReview(el, p) {
  const { photos } = await api(`/api/projects/${p.id}/photos`);
  const order = ["submitted", "needs_changes", "approved", "final"];
  const withEdits = photos.filter((x) => x.revisions).sort((a, b) => order.indexOf(a.state) - order.indexOf(b.state));
  const exportsBox = h("div.col");
  const fmt = h("select.input", { "aria-label": "Export format", style: { width: "auto" } },
    h("option", { value: "jpeg" }, "JPEG (quality 95)"), h("option", { value: "png" }, "PNG (lossless, large)"));
  const finals = photos.filter((x) => x.state === "final").length;

  async function showExports() {
    const { exports } = await api(`/api/projects/${p.id}/exports`);
    clear(exportsBox, exports.slice(0, 3).map((x) => h("div.spread",
      h("span.mono-sm.muted", `${fmtTime(x.created_at)} · ${x.format.toUpperCase()}${x.count ? ` · ${x.count} photos` : ""}`),
      x.status === "succeeded" ? h("a.btn.btn--sm", { href: fileUrl(x.asset_id, true) }, icon("download"), "Download ZIP")
        : x.status === "failed" ? badge(x.error || "Failed", "danger") : h("span.row", h("span.live-dot"), "Preparing"))));
    if (exports.some((x) => x.status === "queued" || x.status === "running")) setTimeout(() => { if (current && current.tab === "review") showExports(); }, 1500);
  }

  clear(el,
    h("section.panel.col",
      h("h2.panel__title", "Export final versions"),
      h("p.small.muted", `${finals} photo${finals === 1 ? " has" : "s have"} a final version. The ZIP holds each one at the original's full size.`),
      h("div.row", fmt, h("button.btn.btn--primary", { type: "button", disabled: !finals, onclick: async () => {
        await api(`/api/projects/${p.id}/exports`, { method: "POST", body: { format: fmt.value } });
        showExports();
      } }, icon("download"), "Export finals")),
      exportsBox),
    withEdits.length ? h("div.gallery", withEdits.map((x) => photoTile(p, x)))
      : h("div.empty.panel", icon("layers", 20), h("span.title", "No edits yet"), h("span.small", "Submitted edits show up here for review.")));
  showExports();
}

route();
