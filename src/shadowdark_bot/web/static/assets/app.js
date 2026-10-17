// App shell: session start (local picker or Discord login), hash routing, views.
//
// Routes:
//   #/                      -> your sheet (Combat)
//   #/sheet/<tab>           -> your sheet
//   #/party                 -> everyone's characters
//   #/party/<user_id>/<tab> -> someone else's sheet (read-only, no stash;
//                              the full, editable sheet for a Dungeon Master)
//   #/guild                 -> coffers, guild storage, magic item treasury
//   #/items                 -> the item catalog
//   #/spells                -> the spell reference
//   #/creatures[/<id>]      -> the bestiary and a creature's stat block
//   #/effects[/<id>]        -> the effects library (reached from Spells, Items,
//                              Creatures and the sheet's Effects card)
//   /auth/callback?code=…   -> Discord redirect login lands here
//   ?frame_id=…             -> launched as a Discord Activity: log in via the SDK

import { activityCode, inActivity } from "./activity.js";
import { api, ApiError, getToken, setToken } from "./api.js";
import { h, mount } from "./dom.js";
import { renderCreature, renderCreatures } from "./creatures.js";
import { editOpts } from "./edit.js";
import { renderEffects } from "./effects.js";
import { renderGuild, renderItems, renderSpells } from "./library.js";
import { pageHead, sheetBody, sheetHeader, subtitle, TABS } from "./sheet.js";

const app = document.getElementById("app");
const topnav = document.getElementById("topnav");
const bottomnav = document.getElementById("bottomnav");

// Top-level sections: [route, label]. Desktop shows them in the top bar,
// phones in a bottom tab bar.
const SECTIONS = [
  ["sheet", "Sheet", "\u2694"],
  ["party", "Party", "\u2605"],
  ["guild", "Guild", "\u2302"],
  ["items", "Items", "\u2699"],
  ["spells", "Spells", "\u2726"],
  ["creatures", "Creatures", "\u2620"],
];
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
    if (config.mode === "local") return renderPicker();
    return inActivity ? startActivityLogin() : renderDiscordLogin();
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
  const [, section = "sheet", a, b] = location.hash.replace(/^#/, "").split("/");
  renderNav(section);
  window.scrollTo(0, 0);
  if (section === "party" && a) return renderOtherSheet(decodeURIComponent(a), tabOr(b));
  if (section === "party") return renderParty();
  if (section === "guild") return renderGuild(app, me);
  if (section === "items") return renderItems(app, { canBuy: me.has_character });
  if (section === "spells") return renderSpells(app);
  if (section === "effects") return renderEffects(app, a ? decodeURIComponent(a) : null, me);
  if (section === "creatures" && a) return renderCreature(app, decodeURIComponent(a));
  if (section === "creatures") return renderCreatures(app);
  return renderMySheet(tabOr(a));
}

const tabOr = (t) => (TABS.some(([key]) => key === t) ? t : "combat");

// ---------- nav ----------

function renderNav(active) {
  document.body.classList.toggle("signed-in", Boolean(me));
  if (!me) {
    mount(topnav);
    mount(bottomnav);
    return;
  }
  const links = (withIcons) =>
    SECTIONS.map(([key, label, glyph]) =>
      h("a", {
        href: key === "sheet" ? "#/sheet/combat" : `#/${key}`,
        class: key === active ? "active" : null,
        "aria-current": key === active ? "page" : null,
      }, withIcons ? h("span", { class: "ico", "aria-hidden": "true" }, glyph) : null, label),
    );
  const switchLabel = config.mode === "local" ? "Switch" : "Log out";
  mount(topnav,
    h("span", { class: "sections" }, links()),
    // Inside Discord you're always yourself: logging out would just log you back in.
    inActivity ? null : h("button", { type: "button", class: "link", onclick: signOut }, switchLabel),
  );
  mount(bottomnav, links(true));
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

// Inside Discord: no redirect, the SDK hands us a code. Starts on its own; a
// failure (e.g. the user declined Discord's consent dialog) offers a retry.
async function startActivityLogin() {
  mount(app, h("p", { class: "loading" }, "Signing in through Discord…"));
  try {
    const code = await activityCode(config);
    const session = await api("/auth/exchange", {
      method: "POST",
      body: { code, flow: "activity" },
    });
    setToken(session.token);
    me = null;
    await route();
  } catch (err) {
    console.error(err);
    mount(app,
      h("section", { class: "intro" },
        h("h1", {}, "Shadowdark character sheet"),
        h("p", { class: "muted" }, err?.message ?? String(err)),
        h("button", { type: "button", class: "primary", onclick: () => startActivityLogin() },
          "Try again"),
      ),
    );
  }
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
  const draw = (c) => {
    const y = window.scrollY;
    const ed = editOpts(c, draw);
    mount(app, sheetHeader(c, tab, (t) => `#/sheet/${t}`, ed.tools("identity")),
      h("div", { class: "sheet" }, sheetBody(c, tab, ed)));
    window.scrollTo(0, y);
  };
  draw(await api("/character"));
}

async function renderOtherSheet(userId, tab) {
  if (userId === me.user_id) {
    location.hash = `#/sheet/${tab}`;
    return;
  }
  const path = `/characters/${encodeURIComponent(userId)}`;
  const hrefFor = (t) => `#/party/${encodeURIComponent(userId)}/${t}`;
  const back = () => h("a", { href: "#/party" }, "← Party");
  if (!me.is_dm) {
    const c = await api(path);
    mount(app,
      h("p", { class: "banner" }, back(), ` Viewing ${c.name}'s sheet (read-only)`),
      sheetHeader(c, tab, hrefFor),
      h("div", { class: "sheet" }, sheetBody(c, tab)),
    );
    return;
  }
  // A Dungeon Master gets the full sheet with every edit tool; its writes go
  // to /api/characters/<user_id>/… instead of your own /api/character/….
  const draw = (c) => {
    const y = window.scrollY;
    const ed = editOpts(c, draw, path);
    mount(app,
      h("p", { class: "banner dm" }, back(), ` Editing ${c.name}'s sheet as Dungeon Master`),
      sheetHeader(c, tab, hrefFor, ed.tools("identity")),
      h("div", { class: "sheet" }, sheetBody(c, tab, ed)),
    );
    window.scrollTo(0, y);
  };
  draw(await api(path));
}

async function renderParty() {
  const chars = await api("/characters");
  mount(app,
    pageHead("Party", me.is_dm
      ? "Everyone's characters. As Dungeon Master you can edit any of them."
      : "Everyone's characters"),
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
