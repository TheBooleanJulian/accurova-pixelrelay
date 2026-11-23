// Supabase magic-link landing: the access token arrives in the URL fragment
// (never sent to servers or logs). Exchange it once for our own session cookie.
import { alertBox, api, clear, h, topbar } from "./core.js";

const app = document.getElementById("app");

async function run() {
  const params = new URLSearchParams(location.hash.slice(1));
  history.replaceState(null, "", location.pathname); // drop the token from the address bar
  const token = params.get("access_token");
  const body = h("div.col", h("p.muted", "Signing you in..."));
  clear(app, topbar(), h("main.main.main--narrow", body));
  if (!token) {
    const reason = params.get("error_description") || "The sign-in link is missing its code or has expired.";
    clear(body, alertBox("danger", "Couldn't sign in", reason), h("a.btn", { href: "/login" }, "Try again"));
    return;
  }
  try {
    await api("/api/auth/supabase-session", { method: "POST", body: { access_token: token } });
    location.replace("/owner");
  } catch (err) {
    clear(body, alertBox("danger", "Couldn't sign in", err.message), h("a.btn", { href: "/login" }, "Try again"));
  }
}

run();
