// Guild-wide sections: the spell reference, the item catalog, and guild storage
// (coffers, shared locations, magic item treasury). Read-only.

import { api } from "./api.js";
import { h, mount, slots } from "./dom.js";
import { coins, storeCard } from "./sheet.js";

// Filters survive navigating away and back within a visit.
const spellFilters = { q: "", cls: "", tier: "", alignment: "" };
const itemFilters = { q: "", type: "" };

export const ITEM_TYPES = [
  ["common", "Common"],
  ["weapon", "Weapon"],
  ["armor", "Armor"],
  ["scroll", "Scroll"],
  ["potion", "Potion"],
  ["loot", "Loot"],
  ["crafted", "Crafted"],
  ["magical", "Magical"],
];
const TYPE_LABEL = Object.fromEntries(ITEM_TYPES);
const ALIGNMENTS = [["L", "Lawful"], ["N", "Neutral"], ["C", "Chaotic"]];
const ALIGN_LABEL = Object.fromEntries(ALIGNMENTS);

export function typeBadge(type) {
  return h("span", { class: `badge type-${type}` }, TYPE_LABEL[type] ?? type);
}

// ---------- shared filter widgets ----------

function searchBox(value, placeholder, oninput) {
  return h("input", {
    type: "search",
    class: "search",
    value,
    placeholder,
    "aria-label": placeholder,
    autocomplete: "off",
    oninput: (e) => oninput(e.target.value),
  });
}

// A row of toggle chips; `options` is [[value, label], …] with "" meaning All.
function chipGroup(label, options, current, onpick) {
  const group = h("div", { class: "chip-group", role: "radiogroup", "aria-label": label });
  const draw = (selected) =>
    group.replaceChildren(
      ...options.map(([value, text]) =>
        h("button", {
          type: "button",
          class: value === selected ? "chip on" : "chip",
          role: "radio",
          "aria-checked": value === selected ? "true" : "false",
          onclick: () => {
            draw(value);
            onpick(value);
          },
        }, text),
      ),
    );
  draw(current);
  return group;
}

const countLine = (shown, total, noun) =>
  h("p", { class: "count muted" }, shown === total ? `${total} ${noun}` : `${shown} of ${total} ${noun}`);

// ---------- spells ----------

export async function renderSpells(app) {
  const spells = await api("/spells");
  const list = h("div", {});
  const redraw = () => drawSpells(list, spells);
  const tiers = [...new Set(spells.map((s) => s.tier))].sort((a, b) => a - b);
  mount(app,
    h("section", { class: "intro" }, h("h1", {}, "Spells")),
    h("div", { class: "filters" },
      searchBox(spellFilters.q, "Search spells", (v) => { spellFilters.q = v; redraw(); }),
      chipGroup("Class", [["", "All classes"], ["wizard", "Wizard"], ["priest", "Priest"]],
        spellFilters.cls, (v) => { spellFilters.cls = v; redraw(); }),
      chipGroup("Tier", [["", "All tiers"], ...tiers.map((t) => [String(t), `Tier ${t}`])],
        spellFilters.tier, (v) => { spellFilters.tier = v; redraw(); }),
      chipGroup("Alignment", [["", "Any alignment"], ...ALIGNMENTS],
        spellFilters.alignment, (v) => { spellFilters.alignment = v; redraw(); }),
    ),
    list,
  );
  redraw();
}

function drawSpells(list, spells) {
  const q = spellFilters.q.trim().toLowerCase();
  const shown = spells.filter((s) =>
    (!q || s.name.toLowerCase().includes(q))
    && (!spellFilters.cls || s.classes.includes(spellFilters.cls))
    && (!spellFilters.tier || String(s.tier) === spellFilters.tier)
    && (!spellFilters.alignment || s.alignment === spellFilters.alignment),
  );
  const byTier = new Map();
  for (const s of shown) {
    if (!byTier.has(s.tier)) byTier.set(s.tier, []);
    byTier.get(s.tier).push(s);
  }
  mount(list,
    countLine(shown.length, spells.length, "spells"),
    shown.length === 0
      ? h("p", { class: "card muted" }, "No spells match those filters.")
      : [...byTier.entries()].map(([tier, group]) =>
          h("section", { class: "card" },
            h("h2", {}, `Tier ${tier}`),
            h("ul", { class: "rows" }, group.map(spellRefRow)),
          ),
        ),
  );
}

function spellRefRow(s) {
  const classes = s.classes.map((c) => c[0].toUpperCase() + c.slice(1)).join(" / ");
  return h("li", {},
    h("details", { class: "row" },
      h("summary", {},
        h("span", { class: "row-name" }, s.name),
        s.alignment ? h("span", { class: "badge align" }, ALIGN_LABEL[s.alignment]) : null,
        h("span", { class: "row-meta" }, classes),
      ),
      h("div", { class: "row-body" },
        h("dl", { class: "facts" },
          fact("Duration", s.duration),
          fact("Range", s.range),
          fact("Class", classes),
          fact("Alignment", s.alignment ? `${ALIGN_LABEL[s.alignment]} only` : null),
        ),
        s.description ? h("p", { class: "prose" }, s.description) : null,
      ),
    ),
  );
}

// ---------- item catalog ----------

export async function renderItems(app) {
  const items = await api("/items?limit=1000");
  const list = h("div", {});
  const redraw = () => drawItems(list, items);
  const present = new Set(items.map((i) => i.item_type));
  mount(app,
    h("section", { class: "intro" },
      h("h1", {}, "Items"),
      h("p", { class: "muted" }, "Everything the guild has catalogued."),
    ),
    h("div", { class: "filters" },
      searchBox(itemFilters.q, "Search items", (v) => { itemFilters.q = v; redraw(); }),
      chipGroup("Type", [["", "All types"], ...ITEM_TYPES.filter(([t]) => present.has(t))],
        itemFilters.type, (v) => { itemFilters.type = v; redraw(); }),
    ),
    list,
  );
  redraw();
}

function drawItems(list, items) {
  const q = itemFilters.q.trim().toLowerCase();
  const shown = items.filter((i) =>
    (!q || i.name.toLowerCase().includes(q)) && (!itemFilters.type || i.item_type === itemFilters.type),
  );
  mount(list,
    countLine(shown.length, items.length, "items"),
    shown.length === 0
      ? h("p", { class: "card muted" },
          items.length ? "No items match those filters." : "The catalog is empty. Add items in Discord with /items add.")
      : h("section", { class: "card" }, h("ul", { class: "rows" }, shown.map(catalogRow))),
  );
}

function slotText(gearSlots, bundle) {
  return bundle > 1 ? `${slots(gearSlots)} sl / ${bundle}` : `${slots(gearSlots)} sl`;
}

function catalogRow(i) {
  return h("li", {},
    h("details", { class: "row" },
      h("summary", {},
        h("span", { class: "row-name" }, i.name),
        typeBadge(i.item_type),
        h("span", { class: "row-meta" }, slotText(i.gear_slots, i.bundle_size)),
      ),
      h("div", { class: "row-body" },
        h("dl", { class: "facts" },
          fact("Type", TYPE_LABEL[i.item_type] ?? i.item_type),
          fact("Slots", i.bundle_size > 1
            ? `${slots(i.gear_slots)} per ${i.bundle_size}`
            : `${slots(i.gear_slots)} each`),
          fact("Value", i.value_cp !== null ? coins(i.value_cp) : null),
        ),
        i.description ? h("p", { class: "prose" }, i.description) : null,
      ),
    ),
  );
}

// ---------- guild storage ----------

export async function renderGuild(app) {
  const g = await api("/guild");
  const available = g.treasury.filter((t) => t.status === "available").length;
  mount(app,
    h("section", { class: "intro" }, h("h1", {}, "Guild")),
    h("div", { class: "tiles" },
      h("div", { class: "tile" },
        h("span", { class: "tile-label" }, "Coffers"),
        h("span", { class: "tile-value" }, g.coffers.balance),
      ),
      h("div", { class: "tile" },
        h("span", { class: "tile-label" }, "Treasury"),
        h("span", { class: "tile-value" }, `${available} / ${g.treasury.length}`),
        h("span", { class: "tile-note" }, "magic items available"),
      ),
    ),
    h("section", { class: "card treasury" },
      h("div", { class: "store-head" },
        h("h2", {}, "Magic item treasury"),
        h("span", { class: "slots" }, `${available} available`),
      ),
      g.treasury.length === 0
        ? h("p", { class: "muted" }, "Empty. Add magical items in Discord with /treasury add.")
        : h("ul", { class: "rows" }, g.treasury.map(treasuryRow)),
    ),
    g.locations.length === 0
      ? h("p", { class: "card muted" }, "No guild storage locations yet. Create one in Discord with /inventory.")
      : g.locations.map((loc) => storeCard(loc.name, loc, "Empty.")),
  );
}

function treasuryRow(t) {
  const who = t.borrower ? (t.borrower.name ?? "a player") : null;
  return h("li", {},
    h("details", { class: "row" },
      h("summary", {},
        h("span", { class: "row-name" }, t.item.name, t.tag ? h("span", { class: "tag" }, ` (${t.tag})`) : null),
        t.borrower
          ? h("span", { class: "badge out" }, `With ${who}`)
          : h("span", { class: "badge in" }, "Available"),
      ),
      h("div", { class: "row-body" },
        h("dl", { class: "facts" },
          fact("Status", t.borrower ? `Borrowed by ${who}, ${since(t.borrower.borrowed_at)}` : "Available"),
          fact("Notes", t.borrower?.notes),
          fact("Slots", slotText(t.item.gear_slots, t.item.bundle_size)),
          fact("Value", t.item.value_cp !== null ? coins(t.item.value_cp) : null),
          fact("Instance", `#${t.id}`),
        ),
        t.item.description ? h("p", { class: "prose" }, t.item.description) : null,
      ),
    ),
  );
}

// ---------- helpers ----------

function fact(label, value) {
  if (value === null || value === undefined || value === "") return null;
  return [h("dt", {}, label), h("dd", {}, value)];
}

// Server times are UTC without a zone suffix.
function since(iso) {
  const t = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  const secs = Math.max(0, (Date.now() - t) / 1000);
  if (secs < 3600) return `${Math.max(1, Math.round(secs / 60))} min ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)} h ago`;
  const days = Math.round(secs / 86400);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}
