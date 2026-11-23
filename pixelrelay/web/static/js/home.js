import { api, clear, h, icon, topbar } from "./core.js";

const app = document.getElementById("app");

function parseRoomLink(raw) {
  const v = raw.trim();
  const m = v.match(/\/r\/([a-z0-9]{6,20})/i) || v.match(/^([a-z0-9]{6,20})$/i);
  return m ? m[1].toLowerCase() : null;
}

async function render() {
  const roomField = h("input.input.input--mono#room", { placeholder: "Paste the link you were sent", autocomplete: "off", inputmode: "url" });
  const err = h("div.field__error", { hidden: true });
  const myRooms = h("div.col");

  const form = h("form.col", {
    onsubmit: (e) => {
      e.preventDefault();
      const slug = parseRoomLink(roomField.value);
      if (!slug) { err.hidden = false; err.textContent = "x That doesn't look like a PixelRelay link."; roomField.setAttribute("aria-invalid", "true"); return; }
      location.href = `/r/${slug}`;
    },
  },
  h("label.field",
    h("span.label", "Project link"),
    roomField, err,
    h("span.field__help", "You'll enter the Room PIN on the next screen.")),
  h("button.btn.btn--primary.btn--lg.btn--block", { type: "submit" }, "Open project"));

  clear(app,
    topbar({ actions: [h("a.btn.btn--ghost.btn--sm", { href: "/login" }, "Organiser sign in")] }),
    h("main.main.main--narrow",
      h("section.hero",
        h("h1.display-xl", "PixelRelay"),
        h("p.hero__tag", "Every edit moves the picture forward."),
        h("p.muted", "Edit your photos from a group shoot in your own app. PixelRelay keeps every version and puts your changes back onto the full-resolution original."),
        h("hr.hairline")),
      h("section.panel.panel--bracket.col",
        h("h2.panel__title", "Join a project"), form),
      myRooms,
      h("section.col",
        h("h2.label", "How it works"),
        h("div.steps",
          step(1, "Open the link", "Your organiser sends a link and a 6-digit Room PIN."),
          step(2, "Pick your photos", "See only the photos assigned to you."),
          step(3, "Edit your way", "Download the original and edit in any app."),
          step(4, "Upload and merge", "Smart Merge puts your changes onto the original."))),
      h("footer.footer", "Collaborate. Create. Preserve.")));

  try {
    const { rooms } = await api("/api/invites/rooms");
    if (rooms.length) {
      clear(myRooms, h("h2.label", "Your projects on this device"),
        h("div.cards", rooms.map((r) => h("a.card", { href: r.url },
          h("div.card__body", h("span.card__title", r.name), h("span.small.muted", "Continue")), icon("next")))));
    }
  } catch { /* not important */ }
}

function step(n, title, body) {
  return h("div.step", h("span.step__n", String(n)), h("div.col", { style: { gap: "2px" } }, h("span.title", title), h("span.small.muted", body)));
}

render();
