// App shell: session start (local picker or Discord login), hash routing, views.
//
// Routes:
//   #/                      -> your sheet (Combat)
//   #/sheet/<tab>           -> your sheet
//   #/party                 -> everyone's characters
//   #/party/<user_id>/<tab> -> someone else's sheet (read-only, no stash)
//   /auth/callback?code=…   -> Discord redirect login lands here

import { api, ApiError, getToken, setToken } from "./api.js";
import { h, mount } from "./dom.js";
import { sheetBody, sheetHeader, subtitle, TABS } from "./sheet.js";

const app = document.getElementById("app");
const topnav = document.getElementById("topnav");
const STATE_KEY = "sd.oauth_state";

let config = null; // /api/auth/config
let me = null; // /api/me

// ---------- boot ----------

async function boot() {
  try {
    config = await api("/auth/config");
    if (location.pathname === "/auth/callback") {
      await finishDiscordLogin();
      return;
    }
    await route();
  } catch (err) {
    showError(err);
  }
}

window.addEventListener("hashchange", () => route().catch(showError));

async function route() {
  if (!getToken()) {
    renderNav();
    return config.mode === "local" ? renderPicker() : renderDiscordLogin();
  }
  try {
    me = me ?? (await api("/me"));
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      me = null;
      return route(); // token expired or app restarted: start over
    }
    throw err;
  }
  renderNav();

  const [, section = "sheet", a, b] = location.hash.replace(/^#/, "").split("/");
  if (section === "party" && a) return renderOtherSheet(decodeURIComponent(a), tabOr(b));
  if (section === "party") return renderParty();
  return renderMySheet(tabOr(a));
}

const tabOr = (t) => (TABS.some(([key]) => key === t) ? t : "combat");

// ---------- nav ----------

function renderNav() {
  if (!me) {
    mount(topnav);
    return;
  }
  const switchLabel = config.mode === "local" ? "Switch" : "Log out";
  mount(topnav,
    h("a", { href: "#/sheet/combat" }, "My sheet"),
    h("a", { href: "#/party" }, "Party"),
    h("button", { type: "button", class: "link", onclick: signOut }, switchLabel),
  );
}

function signOut() {
  setToken(null);
  me = null;
  location.hash = "#/";
  route().catch(showError);
}

// ---------- session start: local ----------

async function renderPicker() {
  const chars = await api("/auth/local/characters");
  mount(app,
    h("section", { class: "intro" },
      h("h1", {}, "Who's playing?"),
      h("p", { class: "muted" }, "Pick your character. (Home network only — no login needed.)"),
    ),
    chars.length === 0
      ? h("p", { class: "card muted" },
          "No characters yet. Create one in Discord with /character sheet.")
      : h("ul", { class: "picker" },
          chars.map((c) =>
            h("li", {},
              h("button", { type: "button", class: "pick", onclick: () => pickLocal(c.user_id) },
                h("span", { class: "pick-name" }, c.name),
                h("span", { class: "pick-sub" }, subtitle(c)),
              ),
            ),
          ),
        ),
  );
}

async function pickLocal(userId) {
  const session = await api("/auth/local", { method: "POST", body: { user_id: userId } });
  setToken(session.token);
  me = null;
  location.hash = "#/sheet/combat";
  await route();
}

// ---------- session start: Discord ----------

function renderDiscordLogin() {
  mount(app,
    h("section", { class: "intro" },
      h("h1", {}, "Shadowdark character sheet"),
      h("p", { class: "muted" }, "For members of our Discord servers."),
      h("button", { type: "button", class: "primary", onclick: startDiscordLogin },
        "Log in with Discord"),
    ),
  );
}

function startDiscordLogin() {
  const state = crypto.randomUUID();
  try {
    sessionStorage.setItem(STATE_KEY, state);
  } catch {
    /* without storage the state check below will fail closed */
  }
  const params = new URLSearchParams({
    client_id: config.client_id,
    redirect_uri: config.redirect_uri,
    response_type: "code",
    scope: config.scopes.join(" "),
    state,
    prompt: "none",
  });
  location.assign(`https://discord.com/oauth2/authorize?${params}`);
}

async function finishDiscordLogin() {
  const params = new URLSearchParams(location.search);
  let expected = null;
  try {
    expected = sessionStorage.getItem(STATE_KEY);
    sessionStorage.removeItem(STATE_KEY);
  } catch {
    /* fall through: state mismatch */
  }
  // Drop the code from the address bar before anything else.
  history.replaceState(null, "", "/");
  if (params.get("error")) throw new Error("Discord login was cancelled.");
  if (!params.get("code") || !expected || params.get("state") !== expected) {
    throw new Error("That login link is stale or was tampered with. Try again.");
  }
  const session = await api("/auth/exchange", {
    method: "POST",
    body: { code: params.get("code"), flow: "web" },
  });
  setToken(session.token);
  me = null;
  location.hash = "#/sheet/combat";
  await route();
}

// ---------- sheets ----------

async function renderMySheet(tab) {
  if (!me.has_character) {
    mount(app,
      h("section", { class: "intro" },
        h("h1", {}, "No character yet"),
        h("p", { class: "muted" }, "Create one in Discord with /character sheet, then come back."),
      ),
    );
    return;
  }
  const c = await api("/character");
  mount(app, sheetHeader(c, tab, (t) => `#/sheet/${t}`), h("div", { class: "sheet" }, sheetBody(c, tab)));
}

async function renderOtherSheet(userId, tab) {
  if (userId === me.user_id) {
    location.hash = `#/sheet/${tab}`;
    return;
  }
  const c = await api(`/characters/${encodeURIComponent(userId)}`);
  mount(app,
    h("p", { class: "banner" },
      h("a", { href: "#/party" }, "← Party"),
      ` Viewing ${c.name}'s sheet (read-only)`,
    ),
    sheetHeader(c, tab, (t) => `#/party/${encodeURIComponent(userId)}/${t}`),
    h("div", { class: "sheet" }, sheetBody(c, tab)),
  );
}

async function renderParty() {
  const chars = await api("/characters");
  mount(app,
    h("section", { class: "intro" }, h("h1", {}, "Party")),
    h("ul", { class: "picker" },
      chars.map((c) =>
        h("li", {},
          h("a", {
            class: "pick",
            href: c.user_id === me.user_id ? "#/sheet/combat" : `#/party/${encodeURIComponent(c.user_id)}/combat`,
          },
            h("span", { class: "pick-name" }, c.user_id === me.user_id ? `${c.name} (you)` : c.name),
            h("span", { class: "pick-sub" }, subtitle(c)),
          ),
        ),
      ),
    ),
  );
}

// ---------- errors ----------

function showError(err) {
  console.error(err);
  mount(app,
    h("section", { class: "card error" },
      h("h2", {}, "Something went wrong"),
      h("p", {}, err?.message ?? String(err)),
      h("button", { type: "button", class: "primary", onclick: () => location.reload() }, "Try again"),
    ),
  );
}

boot();
