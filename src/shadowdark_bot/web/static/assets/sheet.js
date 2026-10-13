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

export function sheetHeader(c, tab, hrefFor, tools = null) {
  return [
    h("section", { class: "sheet-head" },
      h("h1", { class: "char-name" }, c.name),
      h("span", { class: "card-tools" }, tools),
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

// opts (all optional, used by the editing layer):
//   opts.spellActions?.(spell, c)         -> nodes for a spell row's action slot
//   opts.tools?.(sectionKey, c)          -> nodes for a card's tools slot
//   opts.itemActions?.(item, storeKey, c) -> nodes for an item row's action slot
export function sheetBody(c, tab, opts = {}) {
  if (tab === "inventory") return inventoryTab(c, opts);
  if (tab === "roleplaying") return roleplayingTab(c, opts);
  return combatTab(c, opts);
}

// Page heading shared by Party, Guild, Items and Spells.
export function pageHead(title, meta) {
  return h("header", { class: "page-head" },
    h("h1", {}, title),
    meta ? h("p", { class: "page-meta" }, meta) : null,
  );
}

// A sheet card: <section data-section=key> with a head row of title, aside
// text and a tools slot (empty and hidden until something fills it).
export function card(key, title, { aside, tools } = {}, ...body) {
  return h("section", { class: "card", "data-section": key },
    h("div", { class: "card-head" },
      h("h2", {}, title),
      h("span", { class: "card-aside" }, aside ?? null),
      h("span", { class: "card-tools" }, tools ?? null),
    ),
    ...body,
  );
}

// ---------- combat ----------

function statTile(label, value, note) {
  return h("div", { class: "tile" },
    h("span", { class: "tile-label" }, label),
    h("span", { class: "tile-value" }, value),
    note ? h("span", { class: "tile-note" }, note) : null,
  );
}

function combatTab(c, opts) {
  const tools = (key) => opts.tools?.(key, c);
  const ac = c.armor_class === null
    ? statTile("AC", "—")
    : statTile("AC", c.armor_class, `${c.armor_class_base} + DEX`);
  const cast = c.spellcasting;
  const profTools = tools("proficiencies");
  return [
    card("vitals", "Hit points & armour", { tools: tools("vitals") },
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
    ),
    card("abilities", "Abilities", { tools: tools("abilities") },
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
    c.proficiencies || profTools
      ? card("proficiencies", "Proficiencies", { tools: profTools },
          c.proficiencies ? h("p", { class: "prose" }, c.proficiencies) : h("p", { class: "muted" }, "—"))
      : null,
    spellsCard(c, opts),
  ];
}

function spellsCard(c, opts) {
  if (!c.spells.length && !c.spellcasting) return null;
  const byTier = new Map();
  for (const s of c.spells) {
    const key = s.tier ?? 0;
    if (!byTier.has(key)) byTier.set(key, []);
    byTier.get(key).push(s);
  }
  return card("spells", "Spells", { tools: opts.tools?.("spells", c) },
    c.spells.length === 0
      ? h("p", { class: "muted" }, "No spells known yet.")
      : [...byTier.entries()].map(([tier, spells]) => [
          h("h3", { class: "tier" }, tier ? `Tier ${tier}` : "Other"),
          h("ul", { class: "rows" }, spells.map((s) => spellRow(s, opts.spellActions?.(s, c)))),
        ]),
  );
}

function spellRow(s, actions) {
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
    h("span", { class: "row-actions" }, actions ?? null),
  );
}

// ---------- inventory ----------

function inventoryTab(c, opts) {
  return [
    card("gold", "Gold", { tools: opts.tools?.("gold", c) },
      h("div", { class: "coin-row" },
        h("span", {}, "Coins"),
        h("strong", {}, c.gold),
      ),
    ),
    storeCard("Carrying", c.held, "Carrying nothing.", { key: "held", c, opts }),
    c.stash ? storeCard("Stash", c.stash, "Empty.", { key: "stash", c, opts }) : null,
  ];
}

// ctx = { key, c, opts } on the character sheet; absent on guild locations.
export function storeCard(label, store, empty, ctx = {}) {
  const pct = store.max_slots > 0 ? Math.min(100, (store.used_slots / store.max_slots) * 100) : 0;
  const full = store.used_slots >= store.max_slots;
  const aside = h("span", { class: full ? "slots full" : "slots" },
    `${slots(store.used_slots)} / ${slots(store.max_slots)} slots`);
  return card(ctx.key ?? `store-${store.id ?? label}`, label,
    { aside, tools: ctx.opts?.tools?.(ctx.key, ctx.c) },
    store.description && !ctx.key ? h("p", { class: "store-desc muted" }, store.description) : null,
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
      : h("ul", { class: "rows" },
          store.items.map((i) => itemRow(i, ctx.opts?.itemActions?.(i, ctx.key, ctx.c)))),
  );
}

function itemRow(i, actions) {
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
    h("span", { class: "row-actions" }, actions ?? null),
  );
}

// ---------- roleplaying ----------

function roleplayingTab(c, opts) {
  const tools = (key) => opts.tools?.(key, c);
  const languages = (c.languages ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  const prose = (text) => (text ? h("p", { class: "prose" }, text) : h("p", { class: "muted" }, "—"));
  return [
    card("background", "Background", { tools: tools("background") }, prose(c.background)),
    card("languages", "Languages", { tools: tools("languages") },
      languages.length
        ? h("ul", { class: "chips" }, languages.map((l) => h("li", {}, l)))
        : h("p", { class: "muted" }, "—")),
    card("talents", "Talents", { tools: tools("talents") }, prose(c.talents)),
    c.additional_info
      ? card("additional", "Additional info", { tools: tools("additional") }, prose(c.additional_info))
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
