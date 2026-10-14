// The effects library (#/effects, #/effects/<id>): reusable effects with a
// duration and stat changes, each linked to the items, spells and creatures
// that cause it. Anyone may add, edit, delete and link. Also the small chip
// list other pages use to show the effects linked to one item/spell/creature.

import { api } from "./api.js";
import { h, mount } from "./dom.js";
import { bonusesFrom, btn, effectFields, openForm } from "./edit.js";
import { card, pageHead } from "./sheet.js";

const filters = { q: "" }; // survives navigating away and back within a visit

const BONUS_LABELS = { ac: "AC", hp: "HP", str: "STR", dex: "DEX", con: "CON", int: "INT", wis: "WIS", cha: "CHA" };
const KINDS = [["item", "Item"], ["spell", "Spell"], ["creature", "Creature"]];
const KIND_LABEL = Object.fromEntries(KINDS);

// Where a linked source lives in the app.
const sourceHref = (l) => (l.kind === "creature" ? `#/creatures/${l.target_id}` : l.kind === "item" ? "#/items" : "#/spells");

export const duration = (e) =>
  e.max_rounds === null ? "Until removed" : `${e.max_rounds} round${e.max_rounds === 1 ? "" : "s"}`;

export function bonusChips(bonuses) {
  const entries = Object.entries(bonuses ?? {});
  if (!entries.length) return null;
  return h("ul", { class: "chips bonus-chips" },
    entries.map(([k, v]) => h("li", {}, `${BONUS_LABELS[k] ?? k} ${v}`)));
}

// "Effects library" link for the heads of the pages effects hang off.
export const libraryLink = () => h("a", { class: "head-link", href: "#/effects" }, "Effects library →");

// Chips linking to #/effects/<id> for a row's expanded body; null when none.
export function linkedEffects(list) {
  if (!list?.length) return null;
  return h("div", { class: "linked-effects" },
    h("span", { class: "muted" }, "Effects"),
    h("ul", { class: "chips" }, list.map((e) =>
      h("li", {}, h("a", { href: `#/effects/${e.id}` }, e.name)))));
}

// kind:target_id -> [effect, ...], from the whole library (one request).
export async function effectsBySource() {
  const map = new Map();
  try {
    for (const e of await api("/effects")) {
      for (const l of e.links) {
        const key = `${l.kind}:${l.target_id}`;
        if (!map.has(key)) map.set(key, []);
        map.get(key).push(e);
      }
    }
  } catch { /* the library is a nicety: pages still work without it */ }
  return map;
}

// ---------- list ----------

export async function renderEffects(app, id) {
  if (id) return renderEffect(app, id);
  const all = await api("/effects");
  const list = h("div", {});
  const draw = () => {
    const q = filters.q.trim().toLowerCase();
    const shown = all.filter((e) => !q || e.name.toLowerCase().includes(q));
    mount(list,
      h("p", { class: "count muted" },
        shown.length === all.length ? `${all.length} effects` : `${shown.length} of ${all.length} effects`),
      shown.length === 0
        ? h("p", { class: "card muted" },
            all.length === 0 ? "No effects yet. Add the first one." : "No effects match that search.")
        : h("ul", { class: "creature-list" }, shown.map(effectRow)),
    );
  };
  mount(app,
    pageHead("Effects", "Reusable buffs, curses and spells in effect"),
    h("div", { class: "filters" },
      h("input", {
        type: "search", class: "search", value: filters.q, placeholder: "Search effects",
        "aria-label": "Search effects", autocomplete: "off",
        oninput: (e) => { filters.q = e.target.value; draw(); },
      }),
      btn("Add effect", () => openEffectForm(null, (saved) => { location.hash = `#/effects/${saved.id}`; })),
    ),
    list,
  );
  draw();
}

function effectRow(e) {
  return h("li", {},
    h("a", { class: "creature-row", href: `#/effects/${e.id}` },
      h("span", { class: "creature-name" }, e.name),
      h("span", { class: "creature-stats" }, duration(e)),
      bonusChips(e.bonuses),
      e.links.length
        ? h("span", { class: "creature-seen" }, e.links.map((l) => l.target_name).join(", "))
        : null,
    ),
  );
}

// ---------- detail ----------

async function renderEffect(app, id) {
  const draw = (e) => {
    const y = window.scrollY;
    mount(app,
      h("p", { class: "banner" }, h("a", { href: "#/effects" }, "← Effects")),
      card("effect", e.name, {
        tools: [
          btn("Edit", () => openEffectForm(e, draw)),
          btn("Delete", () => openForm({
            title: "Delete effect",
            message: `Delete ${e.name} from the library? Copies already on sheets stay.`,
            submitLabel: "Delete",
            danger: true,
            onSubmit: async () => {
              await api(`/effects/${e.id}`, { method: "DELETE" });
              location.hash = "#/effects";
            },
          })),
        ],
      },
        h("dl", { class: "facts" },
          h("dt", {}, "Duration"), h("dd", {}, duration(e)),
        ),
        bonusChips(e.bonuses) ?? h("p", { class: "muted" }, "No stat changes."),
        e.description ? h("p", { class: "prose" }, e.description) : null,
      ),
      linksCard(e, draw),
    );
    window.scrollTo(0, y);
  };
  draw(await api(`/effects/${encodeURIComponent(id)}`));
}

function linksCard(e, redraw) {
  const unlink = async (l) => redraw(await api(`/effects/${e.id}/links/${l.kind}/${l.target_id}`, { method: "DELETE" }));
  return card("links", "Comes from", {
    tools: btn("Link", () => linkDialog(e, redraw)),
  },
    e.links.length === 0
      ? h("p", { class: "muted" }, "Not linked to any item, spell or creature yet.")
      : h("ul", { class: "rows link-rows" }, e.links.map((l) =>
          h("li", {},
            h("a", { class: "link-target", href: sourceHref(l) },
              h("span", { class: "badge" }, KIND_LABEL[l.kind]), " ", l.target_name),
            h("span", { class: "row-actions" },
              btn("Unlink", () => unlink(l).catch((err) => openForm({ title: "Unlink", message: err.message })))),
          ))),
  );
}

// "Link to item/spell/creature": pick the kind, then search the list.
async function linkDialog(e, redraw) {
  const [items, spells, creatures] = await Promise.all([
    api("/items?limit=1000"), api("/spells"), api("/creatures"),
  ]);
  const linked = new Set(e.links.map((l) => `${l.kind}:${l.target_id}`));
  const options = (kind, rows) =>
    rows.filter((r) => !linked.has(`${kind}:${r.id}`)).map((r) => [r.id, r.name]);
  const sources = { item: options("item", items), spell: options("spell", spells), creature: options("creature", creatures) };
  const kinds = KINDS.filter(([k]) => sources[k].length);
  if (!kinds.length) return openForm({ title: "Link", message: "Everything is already linked." });
  openForm({
    title: `Link ${e.name}`,
    fields: [
      { name: "kind", label: "Link to", type: "select", value: kinds[0][0], options: kinds },
      ...kinds.map(([k, label]) => ({
        name: `target_${k}`, label: label, type: "select", required: true, size: 6,
        filter: `Search ${label.toLowerCase()}s`, options: sources[k], showWhen: ["kind", k],
      })),
    ],
    submitLabel: "Link",
    onSubmit: async (v) => redraw(await api(`/effects/${e.id}/links`, {
      method: "POST", body: { kind: v.kind, target_id: Number(v[`target_${v.kind}`]) },
    })),
  });
}

// ---------- add / edit ----------

function openEffectForm(e, done) {
  openForm({
    title: e ? `Edit ${e.name}` : "Add effect",
    fields: [
      ...effectFields(e).slice(0, 2),
      { name: "description", label: "Description", type: "textarea", value: e?.description },
      ...effectFields(e).slice(2),
    ],
    submitLabel: e ? "Save" : "Add",
    onSubmit: async (v) => {
      const body = {
        name: v.name, max_rounds: v.max_rounds, description: v.description || null, bonuses: bonusesFrom(v),
      };
      done(await api(e ? `/effects/${e.id}` : "/effects", { method: e ? "PATCH" : "POST", body }));
    },
  });
}
