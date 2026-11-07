// The creature bestiary: a searchable, filterable list and a Shadowdark-style stat
// block per creature, plus the encounters the party has had with it. Everyone can
// read it; only a Dungeon Master can add, edit and delete. A stat nobody knows is
// simply not shown (the Edit form still lists every field). A Named creature can
// point at its Type and shows the Type's stats where its own are blank.

import { api } from "./api.js";
import { h, mount, signed } from "./dom.js";
import { openForm } from "./edit.js";
import { libraryLink, linkedEffects } from "./effects.js";
import { chipGroup, countLine } from "./library.js";
import { card, pageHead } from "./sheet.js";

const filters = { q: "", kind: "", tag: "" }; // survives navigating away and back within a visit
let filtersFor = null; // whose visit the filters belong to

const ALIGN_LABEL = { L: "Lawful", N: "Neutral", C: "Chaotic" };
const KIND_LABEL = { named: "Named", type: "Type" };
const DEFAULT_TAGS = ["ally", "enemy", "neutral", "creature", "townfolk"];

const has = (v) => v !== null && v !== undefined && v !== "";

const btn = (label, onClick, cls = "tool") => h("button", { type: "button", class: cls, onClick }, label);

const kindBadge = (kind) =>
  h("span", { class: `kind-badge kind-${kind}` }, KIND_LABEL[kind] ?? kind);

const tagChips = (tags) =>
  tags?.length ? h("ul", { class: "chips creature-tags" }, tags.map((t) => h("li", {}, t))) : null;

const metCount = (n) => (n === 0 ? "Not met yet" : n === 1 ? "Met once" : `Met ${n} times`);

// ---------- list ----------

export async function renderCreatures(app, me = null) {
  if (me && filtersFor !== me.user_id) {
    // A different character signed in: start from a clean list.
    Object.assign(filters, { q: "", kind: "", tag: "" });
    filtersFor = me.user_id;
  }
  const creatures = await api("/creatures");
  const tags = [...new Set(creatures.flatMap((c) => c.tags))].sort();
  if (filters.tag && !tags.includes(filters.tag)) filters.tag = "";
  const list = h("div", {});
  const draw = () => {
    const q = filters.q.trim().toLowerCase();
    const shown = creatures.filter((c) =>
      (!q || c.name.toLowerCase().includes(q)) &&
      (!filters.kind || c.kind === filters.kind) &&
      (!filters.tag || c.tags.includes(filters.tag)));
    mount(list,
      countLine(shown.length, creatures.length, "creatures"),
      shown.length === 0
        ? h("p", { class: "card muted" },
            creatures.length === 0 ? "Nothing in the bestiary yet." : "No creatures match.")
        : h("ul", { class: "creature-list" }, shown.map(creatureRow)),
    );
  };
  mount(app,
    pageHead("Creatures", ["What we've met out there · ", libraryLink()]),
    h("div", { class: "filters" },
      h("input", {
        type: "search", class: "search", value: filters.q, placeholder: "Search creatures",
        "aria-label": "Search creatures", autocomplete: "off",
        oninput: (e) => { filters.q = e.target.value; draw(); },
      }),
      chipGroup("Kind", [["", "All"], ["named", "Named"], ["type", "Types"]],
        filters.kind, (v) => { filters.kind = v; draw(); }),
      tags.length
        ? chipGroup("Tag", [["", "All tags"], ...tags.map((t) => [t, t])],
            filters.tag, (v) => { filters.tag = v; draw(); })
        : null,
      me?.is_dm ? btn("Add creature", () => openCreatureForm(null), "tool add-creature") : null,
    ),
    list,
  );
  draw();
}

function creatureRow(c) {
  const stats = [["LV", c.level], ["AC", c.ac], ["HP", c.hp]]
    .filter(([, v]) => has(v)).map(([label, v]) => `${label} ${v}`);
  return h("li", {},
    h("a", { class: "creature-row", href: `#/creatures/${c.id}` },
      h("span", { class: "creature-name" }, c.name, kindBadge(c.kind)),
      c.title ? h("span", { class: "creature-title muted" }, c.title) : null,
      stats.length ? h("span", { class: "creature-stats" }, stats.join(" · ")) : null,
      tagChips(c.tags),
      h("span", { class: "creature-seen" }, metCount(c.encounter_count)),
    ),
  );
}

// ---------- detail ----------

export async function renderCreature(app, id, me = null) {
  const canEdit = me?.is_dm === true;
  const draw = (c, effects) => {
    const y = window.scrollY;
    const own = has(c.special_abilities) ? c.special_abilities : null;
    const fromType = !own && has(c.inherited?.special_abilities) ? c.inherited.special_abilities : null;
    mount(app,
      h("p", { class: "banner" }, h("a", { href: "#/creatures" }, "← Creatures")),
      statBlock(c, (saved) => draw(saved, effects), canEdit),
      effects.length ? card("effects", "Effects", {}, linkedEffects(effects)) : null,
      own ? textCard("abilities", "Special abilities", own) : null,
      fromType
        ? textCard("abilities", `Special abilities (from ${c.type?.name ?? "its type"})`, fromType)
        : null,
      c.description ? textCard("description", "Description", c.description) : null,
      c.notes ? textCard("notes", "Notes", c.notes) : null,
      encountersCard(c, (saved) => draw(saved, effects), canEdit),
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

function statBlock(c, redraw, canEdit) {
  // A stat's own value, else the Type's (marked), else nothing.
  const pick = (field) => {
    if (has(c[field])) return { value: c[field], inherited: false };
    if (has(c.inherited?.[field])) return { value: c.inherited[field], inherited: true };
    return null;
  };
  const mark = (stat, text) => h("span", {
    class: stat.inherited ? "inherited" : null,
    title: stat.inherited ? `From ${c.type?.name ?? "its type"}` : null,
  }, text);
  const tile = (label, field) => {
    const stat = pick(field);
    return stat && h("div", { class: "tile" },
      h("span", { class: "tile-label" }, label),
      h("span", { class: "tile-value" }, mark(stat, stat.value)));
  };
  const fact = (label, field) => {
    const stat = pick(field);
    return stat ? [h("dt", {}, label), h("dd", {}, mark(stat, stat.value))] : [];
  };
  const tiles = [tile("AC", "ac"), tile("HP", "hp"), tile("LV", "level")].filter(Boolean);
  const facts = [...fact("ATK", "attacks"), ...fact("MV", "movement"), ...fact("AL", "alignment")];
  const mods = [["S", "str_mod"], ["D", "dex_mod"], ["C", "con_mod"],
    ["I", "int_mod"], ["W", "wis_mod"], ["Ch", "cha_mod"]]
    .map(([label, field]) => [label, pick(field)]).filter(([, stat]) => stat);
  const align = pick("alignment");
  const sub = [align && ALIGN_LABEL[align.value]].filter(Boolean); // level is in the LV tile
  const known = tiles.length || facts.length || mods.length || sub.length;
  return card("stats", c.name, {
    tools: !canEdit ? null : [
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
    h("p", { class: "creature-meta" },
      kindBadge(c.kind),
      c.title ? h("span", { class: "creature-title muted" }, c.title) : null,
      c.type
        ? h("span", { class: "creature-type muted" }, "Type: ",
            h("a", { href: `#/creatures/${c.type.id}` }, c.type.name))
        : null),
    tagChips(c.tags),
    sub.length ? h("p", { class: "creature-sub" }, sub.join(" · ")) : null,
    tiles.length ? h("div", { class: "tiles" }, tiles) : null,
    facts.length ? h("dl", { class: "facts stat-facts" }, facts) : null,
    mods.length
      ? h("div", { class: "mods" }, mods.map(([label, stat]) =>
          h("div", { class: "mod" },
            h("span", { class: "tile-label" }, label),
            h("span", { class: "mod-value" }, mark(stat, signed(stat.value))))))
      : null,
    known ? null : h("p", { class: "muted nothing-known" }, "Nothing known yet."),
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

async function openCreatureForm(c, redraw) {
  // The tag suggestions and the Types to pick from; a failure just means fewer choices.
  const [suggestions, all] = await Promise.all([
    api("/creatures/tags").catch(() => DEFAULT_TAGS),
    api("/creatures").catch(() => []),
  ]);
  const types = all.filter((t) => t.kind === "type" && t.id !== c?.id);
  // What this creature would show from its Type, as a hint in the empty field.
  const fromType = (name) =>
    has(c?.inherited?.[name]) ? `${c.inherited[name]} (from ${c.type?.name})` : null;
  const num = ([name, label, min, max]) => ({
    name, label, type: "number", value: c?.[name] ?? "", min, max,
    hint: max === null ? HINT : `${HINT} (${min} to ${max})`,
    placeholder: fromType(name),
  });
  const [level, ac, hp, ...mods] = NUM.map(num);
  openForm({
    title: c ? `Edit ${c.name}` : "Add creature",
    fields: [
      { name: "name", label: "Name", type: "text", value: c?.name, required: true,
        hint: "A description is fine, like Pale thing in the well" },
      { name: "kind", label: "Kind", type: "select", value: c?.kind ?? "type",
        options: [["type", "Type"], ["named", "Named"]],
        hint: "A Type is a kind of creature. Named is one particular creature." },
      { name: "type_id", label: "Type", type: "select", value: c?.type?.id ?? "",
        options: [["", "None"], ...types.map((t) => [String(t.id), t.name])],
        showWhen: ["kind", "named"],
        hint: "Pick the creature type this one belongs to; its stats and abilities show as defaults." },
      { name: "title", label: "Class / title", type: "text", value: c?.title, hint: "Like Boss of the old mine" },
      { name: "tags", label: "Tags", type: "tags", value: c?.tags ?? [], suggestions },
      level, ac, hp,
      { name: "attacks", label: "Attacks", type: "text", value: c?.attacks, placeholder: fromType("attacks"),
        hint: `${HINT}. Like 2 claw +3 (1d6)` },
      { name: "movement", label: "Movement", type: "text", value: c?.movement, placeholder: fromType("movement"),
        hint: `${HINT}. Like near (climb)` },
      ...mods,
      { name: "alignment", label: "Alignment", type: "select", value: c?.alignment ?? "",
        options: [["", c?.inherited?.alignment ? `Unset (${ALIGN_LABEL[c.inherited.alignment]} from ${c.type?.name})` : "Unknown"],
          ["L", "Lawful"], ["N", "Neutral"], ["C", "Chaotic"]] },
      { name: "special_abilities", label: "Special abilities", type: "textarea", value: c?.special_abilities,
        placeholder: fromType("special_abilities"), hint: HINT },
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

function encountersCard(c, redraw, canEdit) {
  const write = async (path, method, body) => redraw(await api(`/creatures/${c.id}/encounters${path}`, { method, body }));
  const form = (e) => openForm({
    title: e ? "Edit encounter" : "Add encounter",
    fields: [
      { name: "details", label: "What happened", type: "textarea", value: e?.details, required: true,
        hint: "When, where and how it went, in your own words" },
    ],
    submitLabel: e ? "Save" : "Add",
    onSubmit: (v) => write(e ? `/${e.id}` : "", e ? "PATCH" : "POST", v),
  });
  const del = (e) => openForm({
    title: "Delete encounter",
    message: "Delete this encounter?",
    submitLabel: "Delete",
    danger: true,
    onSubmit: () => write(`/${e.id}`, "DELETE"),
  });
  return card("encounters", "Encounters", {
    aside: c.encounters.length ? String(c.encounters.length) : null,
    tools: canEdit ? btn("Add", () => form(null)) : null,
  },
    c.encounters.length === 0
      ? h("p", { class: "muted" }, "Not met yet.")
      : h("ul", { class: "encounters" }, c.encounters.map((e) =>
          h("li", {},
            h("div", { class: "enc-main" }, h("p", { class: "prose" }, e.details)),
            canEdit
              ? h("div", { class: "row-actions" }, btn("Edit", () => form(e)), btn("Delete", () => del(e)))
              : null,
          ))),
  );
}
