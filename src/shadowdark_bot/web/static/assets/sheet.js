// Renders a character sheet (the JSON from /api/character or
// /api/characters/{id}) with the same three tabs as the Discord sheet.

import { h, signed, slots } from "./dom.js";
import { usedAfterTap } from "./rounds.js";

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
// With `collapsible`, tapping the title row folds the body away (the tools stay
// beside it and don't toggle); the state is remembered per card key.
export function card(key, title, { aside, tools, collapsible = false } = {}, ...body) {
  if (!collapsible) {
    return h("section", { class: "card", "data-section": key },
      h("div", { class: "card-head" },
        h("h2", {}, title),
        h("span", { class: "card-aside" }, aside ?? null),
        h("span", { class: "card-tools" }, tools ?? null),
      ),
      ...body,
    );
  }
  const open = !isCollapsed(key);
  const bodyEl = h("div", { class: "card-body", id: `card-body-${key}`, hidden: open ? null : true }, ...body);
  const toggle = h("button", {
    type: "button", class: "card-toggle", "aria-expanded": String(open), "aria-controls": bodyEl.id,
  },
    h("span", { class: "chevron", "aria-hidden": "true" }),
    h("h2", {}, title),
    h("span", { class: "card-aside" }, aside ?? null),
  );
  const section = h("section", { class: open ? "card collapsible" : "card collapsible collapsed", "data-section": key },
    h("div", { class: "card-head" }, toggle, h("span", { class: "card-tools" }, tools ?? null)),
    bodyEl,
  );
  toggle.addEventListener("click", () => {
    const nowOpen = bodyEl.hidden; // currently hidden -> opening
    bodyEl.hidden = !nowOpen;
    toggle.setAttribute("aria-expanded", String(nowOpen));
    section.classList.toggle("collapsed", !nowOpen);
    setCollapsed(key, !nowOpen);
  });
  return section;
}

const COLLAPSE_KEY = "sd.collapsed.";
function isCollapsed(key) {
  try {
    return localStorage.getItem(COLLAPSE_KEY + key) === "1";
  } catch {
    return false;
  }
}
function setCollapsed(key, on) {
  try {
    if (on) localStorage.setItem(COLLAPSE_KEY + key, "1");
    else localStorage.removeItem(COLLAPSE_KEY + key);
  } catch { /* storage unavailable: just don't remember */ }
}

// ---------- combat ----------

function statTile(label, value, note, boosted = false) {
  return h("div", { class: boosted ? "tile boosted" : "tile" },
    h("span", { class: "tile-label" }, label),
    h("span", { class: "tile-value" }, value),
    note ? h("span", { class: "tile-note" }, note) : null,
  );
}

// "XP 7 / 30" with a thin bar; a hint (not an automatic level-up) once full.
function xpMeter(c) {
  const need = c.xp_needed;
  const pct = Math.max(0, Math.min(100, Math.round((c.xp / need) * 100)));
  return h("div", { class: "xp" },
    h("div", { class: "xp-line" },
      h("span", { class: "xp-text" }, `XP ${c.xp} / ${need}`),
      c.xp >= need ? h("span", { class: "xp-up" }, "Level up!") : null,
    ),
    h("div", {
      class: "xp-bar", role: "progressbar", "aria-label": "XP to next level",
      "aria-valuemin": "0", "aria-valuemax": String(need), "aria-valuenow": String(Math.min(c.xp, need)),
    }, h("span", { style: `width:${pct}%` })),
  );
}

// "Mage Armor =14, Haste +2": which active effects changed a stat.
function sourceText(stat) {
  return stat.sources.map((s) => `${s.effect} ${s.change}`).join(", ");
}

const BONUS_LABELS = { ac: "AC", hp: "HP", str: "STR", dex: "DEX", con: "CON", int: "INT", wis: "WIS", cha: "CHA" };

function combatTab(c, opts) {
  const tools = (key) => opts.tools?.(key, c);
  const eff = c.effective ?? {};
  const acStat = eff.ac;
  const hpStat = eff.hp;
  const ac = c.armor_class === null
    ? statTile("AC", "—")
    : acStat?.boosted
      ? statTile("AC", acStat.total, `${acStat.base} base · ${sourceText(acStat)}`, true)
      : statTile("AC", c.armor_class, `${c.armor_class_base} + DEX`);
  const hp = hpStat?.boosted
    ? statTile("HP", hpStat.total, `${hpStat.base} base · ${sourceText(hpStat)}`, true)
    : statTile("HP", c.max_hp ?? "—");
  const cast = c.spellcasting;
  const profTools = tools("proficiencies");
  return [
    card("vitals", "Hit points & armour", { tools: tools("vitals") },
      h("div", { class: "tiles" },
        hp,
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
      xpMeter(c),
    ),
    card("abilities", "Abilities", { tools: tools("abilities") },
      h("div", { class: "abilities" },
        c.abilities.map((a) => {
          const st = eff[a.key];
          const boosted = st?.boosted;
          return h("div", { class: boosted ? "ability boosted" : "ability", title: boosted ? sourceText(st) : null },
            h("span", { class: "ability-label" }, a.label),
            h("span", { class: "ability-mod" }, signed(boosted ? st.total : a.modifier)),
            h("span", { class: "ability-score" }, a.score),
            boosted
              ? h("span", { class: "ability-note" }, `${signed(st.base)} base · ${sourceText(st)}`)
              : null,
          );
        }),
      ),
    ),
    c.proficiencies || profTools
      ? card("proficiencies", "Proficiencies", { tools: profTools },
          c.proficiencies ? h("p", { class: "prose" }, c.proficiencies) : h("p", { class: "muted" }, "—"))
      : null,
    spellsCard(c, opts),
    effectsCard(c, opts),
  ];
}

// ---------- effects ----------

// Hidden entirely on read-only views when nothing is running. Round boxes tick
// instantly (optimistic) and revert if the write fails.
function effectsCard(c, opts) {
  const tools = opts.tools?.("effects", c);
  const list = c.effects ?? [];
  if (!list.length && !tools) return null;
  return card("effects", "Effects", { tools, collapsible: true },
    list.length === 0
      ? h("p", { class: "muted" }, "Nothing running. Add a spell, potion or blessing to track it.")
      : h("ul", { class: "rows effects" }, list.map((e) => effectRow(e, c, opts))),
    h("p", { class: "card-foot" }, h("a", { href: "#/effects" }, "Effects library \u2192")),
  );
}

function bonusChips(e) {
  const entries = Object.entries(e.bonuses ?? {});
  if (!entries.length) return null;
  return h("ul", { class: "chips bonus-chips" },
    entries.map(([k, v]) => h("li", {}, `${BONUS_LABELS[k] ?? k} ${v}`)));
}

function effectRow(e, c, opts) {
  const timed = e.max_rounds !== null;
  const interactive = timed && !!opts.tickEffect;
  let used = e.rounds_used; // filled boxes = rounds left = max - used
  const label = h("span", { class: "row-meta effect-left" });
  const boxes = timed
    ? Array.from({ length: e.max_rounds }, (_, k) => {
        const n = k + 1;
        return h(interactive ? "button" : "span", {
          class: "round-box",
          type: interactive ? "button" : null,
          role: interactive ? "checkbox" : null,
          "aria-label": `Round ${n}`,
          onClick: interactive ? () => tick(n) : null,
        });
      })
    : [];
  const li = h("li", { class: "effect" });
  const paint = (u) => {
    used = u;
    const left = timed ? e.max_rounds - u : null;
    li.classList.toggle("ended", timed && left === 0);
    label.textContent = !timed ? "Until removed" : left === 0 ? "Ended" : `${left} round${left === 1 ? "" : "s"} left`;
    boxes.forEach((b, k) => {
      const on = k < e.max_rounds - u;
      b.classList.toggle("on", on);
      if (interactive) b.setAttribute("aria-checked", String(on));
    });
  };
  // A filled box spends one round from the right, whichever filled box was
  // tapped; an empty box refills from the left up to and including itself.
  const tick = (n) => {
    const prev = used;
    paint(usedAfterTap(e.max_rounds, used, n));
    Promise.resolve(opts.tickEffect(e, used)).catch(() => paint(prev));
  };
  paint(used);
  li.append(...[
    h("div", { class: "effect-head" },
      h("span", { class: "row-name" }, e.name),
      label,
    ),
    bonusChips(e),
    timed ? h("div", { class: "rounds", role: "group", "aria-label": `${e.name} rounds` }, boxes) : null,
    opts.effectActions ? h("div", { class: "row-actions" }, opts.effectActions(e, c)) : null,
  ].filter(Boolean));
  return li;
}

function spellsCard(c, opts) {
  if (!c.spells.length && !c.spellcasting) return null;
  const byTier = new Map();
  for (const s of c.spells) {
    const key = s.tier ?? 0;
    if (!byTier.has(key)) byTier.set(key, []);
    byTier.get(key).push(s);
  }
  const lostCount = c.spells.filter((s) => s.lost).length;
  const aside = opts.spellToggle ? "Tick = available" : lostCount ? `${lostCount} lost` : null;
  return card("spells", "Spells", { aside, tools: opts.tools?.("spells", c), collapsible: true },
    c.spells.length === 0
      ? h("p", { class: "muted" }, "No spells known yet.")
      : [...byTier.entries()].map(([tier, spells]) => [
          h("h3", { class: "tier" }, tier ? `Tier ${tier}` : "Other"),
          h("ul", { class: "rows" }, spells.map((s) => spellRow(s, opts.spellActions?.(s, c), opts.spellToggle?.(s, c)))),
        ]),
  );
}

function spellRow(s, actions, toggle) {
  const ref = s.spell;
  return h("li", { class: [s.lost && "lost", toggle && "has-toggle"].filter(Boolean).join(" ") || null },
    toggle ?? null,
    h("details", { class: "row" },
      h("summary", {}, h("span", { class: "row-name" }, s.name),
        h("span", { class: "lost-badge" }, "Lost")),
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
  return card(ctx.cardKey ?? ctx.key ?? `store-${store.id ?? label}`, label,
    { aside, tools: ctx.opts?.tools?.(ctx.key, ctx.c), collapsible: ctx.collapsible ?? false },
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
        // Inside the expanded row so three buttons never squeeze the name on a phone.
        h("div", { class: "row-actions" }, actions ?? null),
      ),
    ),
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
