import { alertBox, api, clear, h, topbar } from "./core.js";

const app = document.getElementById("app");

async function render() {
  let cfg = { mode: "dev" };
  try {
    await api("/api/auth/me");
    location.replace("/owner");
    return;
  } catch { /* not signed in */ }
  try { cfg = await api("/api/auth/config"); } catch { /* default */ }

  const email = h("input.input#email", { type: "email", autocomplete: "email", required: true, placeholder: "you@example.com" });
  const msg = h("div.col");
  const submit = h("button.btn.btn--primary.btn--lg.btn--block", { type: "submit" }, cfg.mode === "dev" ? "Sign in" : "Email me a sign-in link");

  const form = h("form.col", {
    onsubmit: async (e) => {
      e.preventDefault();
      submit.disabled = true;
      clear(msg);
      try {
        if (cfg.mode === "dev") {
          await api("/api/auth/dev-login", { method: "POST", body: { email: email.value } });
          location.href = "/owner";
          return;
        }
        await api("/api/auth/magic-link", { method: "POST", body: { email: email.value } });
        clear(msg, alertBox("success", "Check your email", "Open the link we sent to finish signing in. It works once and expires soon."));
      } catch (err) {
        clear(msg, alertBox("danger", "Couldn't sign in", err.message));
      } finally {
        submit.disabled = false;
      }
    },
  },
  h("label.field", h("span.label", "Email"), email),
  submit, msg);

  clear(app,
    topbar(),
    h("main.main.main--narrow",
      h("div.page-head", h("h1.display-lg", "Organiser sign in"),
        h("p.muted", "For photographers and organisers who run a project. Contributors don't need an account: they join with the project link and Room PIN.")),
      cfg.mode === "dev" ? alertBox("warning", "Development sign-in", "Any email signs in without a check. This mode is refused in production.") : null,
      h("section.panel.col", form)));
}

render();
