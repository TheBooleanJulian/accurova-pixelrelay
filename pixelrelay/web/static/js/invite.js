// Personal invitation: /invite#<token>. The token is read from the fragment,
// removed from the address bar, and redeemed once.
import { alertBox, api, clear, h, topbar } from "./core.js";

const app = document.getElementById("app");

async function run() {
  const token = location.hash.slice(1);
  history.replaceState(null, "", location.pathname);
  const body = h("div.col", h("p.muted", "Opening your invitation..."));
  clear(app, topbar(), h("main.main.main--narrow", body));
  if (!token) {
    clear(body, alertBox("danger", "Invitation link incomplete", "Copy the whole link from your message and open it again."));
    return;
  }
  try {
    const { room_url } = await api("/api/invites/redeem", { method: "POST", body: { token } });
    location.replace(room_url);
  } catch (err) {
    clear(body, alertBox("danger", "Couldn't open the invitation", err.message),
      h("p.muted", "If you have the project link and Room PIN, you can join with those instead."));
  }
}

run();
