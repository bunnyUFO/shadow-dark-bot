// Renders a character sheet (the JSON from /api/character or
// /api/characters/{id}) with the same three tabs as the Discord sheet.

import { h, signed, slots } from "./dom.js";

export const TABS = [
  ["combat", "Combat"],
  ["inventory", "Inventory"],
  ["roleplaying", "Roleplaying"],
];

export function subtitle(c) {
  const lead = [c.alignment, c.ancestry, c.char_class].filter(Boolean).join(" ");
  const parts = [`Level ${c.level}`, lead].filter(Boolean).join(" ");
  return c.title ? `${parts} — ${c.title}` : parts;
}

export function sheetHeader(c, tab, hrefFor) {
  return [
    h("section", { class: "sheet-head" },
      h("h1", { class: "char-name" }, c.name),
      h("p", { class: "char-sub" }, subtitle(c)),
    ),
    h("nav", { class: "tabs", role: "tablist" },
      TABS.map(([key, label]) =>
        h("a", {
          class: key === tab ? "tab active" : "tab",
          href: hrefFor(key),
          role: "tab",
          "aria-selected": key === tab ? "true" : "false",
        }, label),
      ),
    ),
  ];
}

export function sheetBody(c, tab) {
  if (tab === "inventory") return inventoryTab(c);
  if (tab === "roleplaying") return roleplayingTab(c);
  return combatTab(c);
}

// ---------- combat ----------

function statTile(label, value, note) {
  return h("div", { class: "tile" },
    h("span", { class: "tile-label" }, label),
    h("span", { class: "tile-value" }, value),
    note ? h("span", { class: "tile-note" }, note) : null,
  );
}

function combatTab(c) {
  const ac = c.armor_class === null
    ? statTile("AC", "—")
    : statTile("AC", c.armor_class, `${c.armor_class_base} + DEX`);
  const cast = c.spellcasting;
  return [
    h("div", { class: "tiles" },
      statTile("HP", c.max_hp ?? "—"),
      ac,
      statTile("Level", c.level),
      cast
        ? statTile(
            "Spellcasting",
            signed(cast.total),
            `${cast.ability.toUpperCase()} ${signed(cast.ability_modifier)}`
              + (cast.talent_bonus ? `, talent ${signed(cast.talent_bonus)}` : ""),
          )
        : null,
    ),
    h("section", { class: "card" },
      h("h2", {}, "Abilities"),
      h("div", { class: "abilities" },
        c.abilities.map((a) =>
          h("div", { class: "ability" },
            h("span", { class: "ability-label" }, a.label),
            h("span", { class: "ability-mod" }, signed(a.modifier)),
            h("span", { class: "ability-score" }, a.score),
          ),
        ),
      ),
    ),
    c.proficiencies
      ? h("section", { class: "card" },
          h("h2", {}, "Proficiencies"),
          h("p", { class: "prose" }, c.proficiencies))
      : null,
    spellsCard(c),
  ];
}

function spellsCard(c) {
  if (!c.spells.length && !c.spellcasting) return null;
  const byTier = new Map();
  for (const s of c.spells) {
    const key = s.tier ?? 0;
    if (!byTier.has(key)) byTier.set(key, []);
    byTier.get(key).push(s);
  }
  return h("section", { class: "card" },
    h("h2", {}, "Spells"),
    c.spells.length === 0
      ? h("p", { class: "muted" }, "No spells known yet.")
      : [...byTier.entries()].map(([tier, spells]) => [
          h("h3", { class: "tier" }, tier ? `Tier ${tier}` : "Other"),
          h("ul", { class: "rows" }, spells.map(spellRow)),
        ]),
  );
}

function spellRow(s) {
  const ref = s.spell;
  return h("li", {},
    h("details", { class: "row" },
      h("summary", {}, h("span", { class: "row-name" }, s.name)),
      h("div", { class: "row-body" },
        ref
          ? [
              h("dl", { class: "facts" },
                fact("Duration", ref.duration),
                fact("Range", ref.range),
                fact("Class", ref.classes.map(cap).join(" / ")),
              ),
              ref.description ? h("p", { class: "prose" }, ref.description) : null,
            ]
          : h("p", { class: "muted" }, "No reference text."),
      ),
    ),
  );
}

// ---------- inventory ----------

function inventoryTab(c) {
  return [
    h("div", { class: "tiles" }, statTile("Gold", c.gold)),
    storeCard("Held", c.held, "Carrying nothing."),
    c.stash ? storeCard("Stash", c.stash, "Empty.") : null,
  ];
}

export function storeCard(label, store, empty) {
  const pct = store.max_slots > 0 ? Math.min(100, (store.used_slots / store.max_slots) * 100) : 0;
  const full = store.used_slots >= store.max_slots;
  return h("section", { class: "card" },
    h("div", { class: "store-head" },
      h("h2", {}, label),
      h("span", { class: full ? "slots full" : "slots" },
        `${slots(store.used_slots)} / ${slots(store.max_slots)} slots`),
    ),
    store.description ? h("p", { class: "store-desc muted" }, store.description) : null,
    h("div", {
      class: "meter",
      role: "meter",
      "aria-valuemin": "0",
      "aria-valuemax": String(store.max_slots),
      "aria-valuenow": String(store.used_slots),
      "aria-label": `${label} gear slots used`,
    }, h("div", { class: full ? "meter-fill full" : "meter-fill", style: `width:${pct}%` })),
    store.items.length === 0
      ? h("p", { class: "muted" }, empty)
      : h("ul", { class: "rows" }, store.items.map(itemRow)),
  );
}

function itemRow(i) {
  const per = `${slots(i.gear_slots_each)} each` + (i.bundle_size > 1 ? `, ${i.bundle_size} per slot` : "");
  return h("li", {},
    h("details", { class: "row" },
      h("summary", {},
        h("span", { class: "qty" }, `${i.quantity}×`),
        h("span", { class: "row-name" }, i.name),
        h("span", { class: "row-meta" }, `${slots(i.slot_cost)} sl`),
      ),
      h("div", { class: "row-body" },
        h("dl", { class: "facts" },
          fact("Type", i.item_type ? cap(i.item_type) : "Freeform"),
          fact("Slots", per),
          fact("Value", i.value_cp !== null ? coins(i.value_cp) : null),
        ),
        i.description ? h("p", { class: "prose" }, i.description) : null,
        i.notes ? h("p", { class: "prose muted" }, i.notes) : null,
      ),
    ),
  );
}

// ---------- roleplaying ----------

function roleplayingTab(c) {
  const languages = (c.languages ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  return [
    h("section", { class: "card" },
      h("h2", {}, "Background"),
      c.background ? h("p", { class: "prose" }, c.background) : h("p", { class: "muted" }, "—"),
    ),
    h("section", { class: "card" },
      h("h2", {}, "Languages"),
      languages.length
        ? h("ul", { class: "chips" }, languages.map((l) => h("li", {}, l)))
        : h("p", { class: "muted" }, "—"),
    ),
    h("section", { class: "card" },
      h("h2", {}, "Talents"),
      c.talents ? h("p", { class: "prose" }, c.talents) : h("p", { class: "muted" }, "—"),
    ),
    c.additional_info
      ? h("section", { class: "card" },
          h("h2", {}, "Additional info"),
          h("p", { class: "prose" }, c.additional_info))
      : null,
  ];
}

// ---------- helpers ----------

function fact(label, value) {
  if (value === null || value === undefined || value === "") return null;
  return [h("dt", {}, label), h("dd", {}, value)];
}

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// Copper -> "5gp 2sp 3cp" (matches the bot's format_cp).
export function coins(cp) {
  const gp = Math.floor(cp / 100);
  const sp = Math.floor((cp % 100) / 10);
  const c = cp % 10;
  const out = [gp && `${gp}gp`, sp && `${sp}sp`, c && `${c}cp`].filter(Boolean).join(" ");
  return out || "0cp";
}
