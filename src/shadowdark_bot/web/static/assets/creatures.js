// The creature bestiary: a searchable list and a Shadowdark-style stat block per
// creature, plus the encounters the party has had with it. Anyone may add, edit
// and delete. Every stat can be unknown, which shows as "?".

import { api } from "./api.js";
import { h, mount, signed } from "./dom.js";
import { openForm } from "./edit.js";
import { libraryLink, linkedEffects } from "./effects.js";
import { card, pageHead } from "./sheet.js";

const filters = { q: "" }; // survives navigating away and back within a visit

const ALIGN_LABEL = { L: "Lawful", N: "Neutral", C: "Chaotic" };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const unknown = (v) => (v === null || v === undefined || v === "" ? "?" : String(v));
const mod = (v) => (v === null || v === undefined ? "?" : signed(v));

// "2026-07-04" -> "4 Jul 2026" (parsed by hand: no time-zone shift).
function niceDate(iso) {
  const [y, m, d] = String(iso).split("-").map(Number);
  return y && m && d ? `${d} ${MONTHS[m - 1]} ${y}` : String(iso ?? "");
}
const todayIso = () => {
  const n = new Date();
  const p = (x) => String(x).padStart(2, "0");
  return `${n.getFullYear()}-${p(n.getMonth() + 1)}-${p(n.getDate())}`;
};

const btn = (label, onClick, cls = "tool") => h("button", { type: "button", class: cls, onClick }, label);

// ---------- list ----------

export async function renderCreatures(app) {
  const creatures = await api("/creatures");
  const list = h("div", {});
  const draw = () => {
    const q = filters.q.trim().toLowerCase();
    const shown = creatures.filter((c) => !q || c.name.toLowerCase().includes(q));
    mount(list,
      h("p", { class: "count muted" },
        shown.length === creatures.length
          ? `${creatures.length} creatures`
          : `${shown.length} of ${creatures.length} creatures`),
      shown.length === 0
        ? h("p", { class: "card muted" },
            creatures.length === 0 ? "Nothing in the bestiary yet." : "No creatures match that search.")
        : h("ul", { class: "creature-list" }, shown.map(creatureRow)),
    );
  };
  mount(app,
    pageHead("Creatures", ["What we've met out there \u00b7 ", libraryLink()]),
    h("div", { class: "filters" },
      h("input", {
        type: "search", class: "search", value: filters.q, placeholder: "Search creatures",
        "aria-label": "Search creatures", autocomplete: "off",
        oninput: (e) => { filters.q = e.target.value; draw(); },
      }),
      btn("Add creature", () => openCreatureForm(null), "tool add-creature"),
    ),
    list,
  );
  draw();
}

function creatureRow(c) {
  return h("li", {},
    h("a", { class: "creature-row", href: `#/creatures/${c.id}` },
      h("span", { class: "creature-name" }, c.name),
      h("span", { class: "creature-stats" },
        `LV ${unknown(c.level)} · AC ${unknown(c.ac)} · HP ${unknown(c.hp)}`),
      h("span", { class: "creature-seen" },
        c.last_seen ? `Last seen ${niceDate(c.last_seen)}` : "Not yet encountered"),
    ),
  );
}

// ---------- detail ----------

export async function renderCreature(app, id) {
  const draw = (c, effects) => {
    const y = window.scrollY;
    mount(app,
      h("p", { class: "banner" }, h("a", { href: "#/creatures" }, "← Creatures")),
      statBlock(c, (saved) => draw(saved, effects)),
      effects.length ? card("effects", "Effects", {}, linkedEffects(effects)) : null,
      c.special_abilities ? textCard("abilities", "Special abilities", c.special_abilities) : null,
      c.description ? textCard("description", "Description", c.description) : null,
      c.notes ? textCard("notes", "Notes", c.notes) : null,
      encountersCard(c, (saved) => draw(saved, effects)),
    );
    window.scrollTo(0, y);
  };
  const [creature, effects] = await Promise.all([
    api(`/creatures/${encodeURIComponent(id)}`),
    api(`/effects?kind=creature&target_id=${encodeURIComponent(id)}`).catch(() => []),
  ]);
  draw(creature, effects);
}

const textCard = (key, title, text) => card(key, title, {}, h("p", { class: "prose" }, text));

function statBlock(c, redraw) {
  const stat = (label, value) =>
    h("div", { class: "tile" },
      h("span", { class: "tile-label" }, label),
      h("span", { class: "tile-value" }, value));
  const abilities = [["S", c.str_mod], ["D", c.dex_mod], ["C", c.con_mod],
    ["I", c.int_mod], ["W", c.wis_mod], ["Ch", c.cha_mod]];
  const sub = [c.level === null ? "Level ?" : `Level ${c.level}`, c.alignment ? ALIGN_LABEL[c.alignment] : "Alignment ?"];
  return card("stats", c.name, {
    tools: [
      btn("Edit", () => openCreatureForm(c, redraw)),
      btn("Delete", () => openForm({
        title: "Delete creature",
        message: `Delete ${c.name} and its encounters? This can't be undone.`,
        submitLabel: "Delete",
        danger: true,
        onSubmit: async () => {
          await api(`/creatures/${c.id}`, { method: "DELETE" });
          location.hash = "#/creatures";
        },
      })),
    ],
  },
    h("p", { class: "creature-sub" }, sub.join(" · ")),
    h("div", { class: "tiles" }, stat("AC", unknown(c.ac)), stat("HP", unknown(c.hp)), stat("LV", unknown(c.level))),
    h("dl", { class: "facts stat-facts" },
      h("dt", {}, "ATK"), h("dd", {}, unknown(c.attacks)),
      h("dt", {}, "MV"), h("dd", {}, unknown(c.movement)),
      h("dt", {}, "AL"), h("dd", {}, c.alignment ?? "?"),
    ),
    h("div", { class: "mods" }, abilities.map(([label, v]) =>
      h("div", { class: "mod" },
        h("span", { class: "tile-label" }, label),
        h("span", { class: "mod-value" }, mod(v))))),
  );
}

// ---------- creature form ----------

const NUM = [
  ["level", "Level", 0, 30], ["ac", "AC", 1, 30], ["hp", "HP", 0, null],
  ["str_mod", "STR modifier", -5, 10], ["dex_mod", "DEX modifier", -5, 10],
  ["con_mod", "CON modifier", -5, 10], ["int_mod", "INT modifier", -5, 10],
  ["wis_mod", "WIS modifier", -5, 10], ["cha_mod", "CHA modifier", -5, 10],
];
const HINT = "Leave blank if unknown";

function openCreatureForm(c, redraw) {
  const num = ([name, label, min, max]) => ({
    name, label, type: "number", value: c?.[name] ?? "", min, max,
    hint: max === null ? HINT : `${HINT} (${min} to ${max})`,
  });
  const [level, ac, hp, ...mods] = NUM.map(num);
  openForm({
    title: c ? `Edit ${c.name}` : "Add creature",
    fields: [
      { name: "name", label: "Name", type: "text", value: c?.name, required: true,
        hint: "A description is fine, like Pale thing in the well" },
      level, ac, hp,
      { name: "attacks", label: "Attacks", type: "text", value: c?.attacks, hint: `${HINT}. Like 2 claw +3 (1d6)` },
      { name: "movement", label: "Movement", type: "text", value: c?.movement, hint: `${HINT}. Like near (climb)` },
      ...mods,
      { name: "alignment", label: "Alignment", type: "select", value: c?.alignment ?? "",
        options: [["", "Unknown"], ["L", "Lawful"], ["N", "Neutral"], ["C", "Chaotic"]] },
      { name: "special_abilities", label: "Special abilities", type: "textarea", value: c?.special_abilities, hint: HINT },
      { name: "description", label: "Description", type: "textarea", value: c?.description, hint: HINT },
      { name: "notes", label: "Notes", type: "textarea", value: c?.notes, hint: "Where we met it, what happened" },
    ],
    submitLabel: c ? "Save" : "Add",
    onSubmit: async (v) => {
      const saved = await api(c ? `/creatures/${c.id}` : "/creatures", {
        method: c ? "PATCH" : "POST", body: v,
      });
      if (c) redraw(saved);
      else location.hash = `#/creatures/${saved.id}`;
    },
  });
}

// ---------- encounters ----------

function encountersCard(c, redraw) {
  const write = async (path, method, body) => redraw(await api(`/creatures/${c.id}/encounters${path}`, { method, body }));
  const form = (e) => openForm({
    title: e ? "Edit encounter" : "Add encounter",
    fields: [
      { name: "date", label: "Date", type: "date", value: e?.date ?? todayIso(), required: true },
      { name: "session", label: "Session", type: "text", value: e?.session, hint: "Like Session 12" },
      { name: "place", label: "Place", type: "text", value: e?.place },
      { name: "outcome", label: "What happened", type: "textarea", value: e?.outcome },
    ],
    submitLabel: e ? "Save" : "Add",
    onSubmit: (v) => write(e ? `/${e.id}` : "", e ? "PATCH" : "POST", v),
  });
  const del = (e) => openForm({
    title: "Delete encounter",
    message: `Delete the encounter on ${niceDate(e.date)}?`,
    submitLabel: "Delete",
    danger: true,
    onSubmit: () => write(`/${e.id}`, "DELETE"),
  });
  return card("encounters", "Encounters", {
    aside: c.encounters.length ? String(c.encounters.length) : null,
    tools: btn("Add", () => form(null)),
  },
    c.encounters.length === 0
      ? h("p", { class: "muted" }, "Not met yet.")
      : h("ul", { class: "encounters" }, c.encounters.map((e) =>
          h("li", {},
            h("div", { class: "enc-main" },
              h("strong", {}, niceDate(e.date)),
              e.session || e.place
                ? h("span", { class: "muted" }, [e.session, e.place].filter(Boolean).join(" · "))
                : null,
              e.outcome ? h("p", { class: "prose" }, e.outcome) : null),
            h("div", { class: "row-actions" }, btn("Edit", () => form(e)), btn("Delete", () => del(e))),
          ))),
  );
}
