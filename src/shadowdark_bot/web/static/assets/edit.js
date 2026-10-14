// Editing layer for your own sheet: one modal form helper plus the wiring that
// feeds sheetBody's tool/action slots. Every write returns the full sheet JSON,
// which is handed to redraw().

import { api } from "./api.js";
import { h, slots } from "./dom.js";
import { coins } from "./sheet.js";

export const cleanDetail = (s) => String(s ?? "").replace(/\*\*/g, "");

function selectOptions(options = [], value = "") {
  return options.map(([v, l]) =>
    h("option", { value: v, selected: String(v) === String(value ?? "") || null }, l));
}

function control(f, listId) {
  const common = { name: f.name, id: `f-${f.name}`, required: f.required || null };
  if (f.type === "select") {
    return h("select", { ...common, size: f.size ?? null }, selectOptions(f.options, f.value));
  }
  if (f.type === "textarea") {
    const t = h("textarea", { ...common, rows: 4 });
    t.value = f.value ?? "";
    return t;
  }
  const el = h("input", {
    ...common,
    type: f.type === "number" || f.type === "date" ? f.type : "text",
    min: f.min ?? null, max: f.max ?? null, step: f.step ?? null,
    list: listId,
    autocomplete: "off",
  });
  el.value = f.value ?? "";
  return el;
}

export function openForm({ title, message, fields = [], submitLabel = "Save", danger = false, onSubmit }) {
  const dialog = h("dialog", { class: "sheet-form" });
  const error = h("p", { class: "form-error", role: "alert" });
  const controls = [];
  const rowFor = new Map();
  const rows = fields.map((f, i) => {
    let listId = null;
    let list = null;
    if (f.suggestions?.length) {
      listId = `dl-${f.name}`;
      list = h("datalist", { id: listId }, f.suggestions.map((s) => h("option", { value: s })));
    }
    const el = control(f, listId);
    controls.push([f, el]);
    // A select with `filter` gets a search box above it that narrows the options
    // (a tappable list that works on phones, unlike <datalist>).
    const search = f.type === "select" && f.filter
      ? h("input", {
          type: "search", placeholder: f.filter, "aria-label": f.filter, autocomplete: "off",
          oninput: (e) => {
            const q = e.target.value.trim().toLowerCase();
            const keep = el.value;
            el.replaceChildren(...selectOptions(
              (f.options ?? []).filter(([, l]) => !q || String(l).toLowerCase().includes(q)), keep));
          },
        })
      : null;
    const row = h("div", { class: "field" },
      h("label", { for: `f-${f.name}` }, f.label),
      search, el, list,
      f.hint ? h("small", { class: "muted" }, f.hint) : null);
    rowFor.set(f, row);
    return row;
  });
  // `showWhen: [otherField, value]` hides a field (and drops it from validation
  // and the submitted values) unless the other field has that value.
  const applyVisibility = () => {
    for (const [f, el] of controls) {
      if (!f.showWhen) continue;
      const other = controls.find(([g]) => g.name === f.showWhen[0])?.[1];
      const hidden = other?.value !== f.showWhen[1];
      rowFor.get(f).hidden = hidden;
      el.disabled = hidden;
    }
  };
  for (const [, el] of controls) el.addEventListener("change", applyVisibility);
  applyVisibility();
  const submit = onSubmit
    ? h("button", { type: "submit", class: danger ? "primary danger" : "primary" }, submitLabel)
    : null;
  const cancel = h("button", { type: "button", class: "tool", onClick: () => dialog.close() }, onSubmit ? "Cancel" : "Close");
  const form = h("form", { method: "dialog" },
    h("h2", {}, title),
    message ? h("p", { class: "form-message" }, message) : null,
    rows, error,
    h("div", { class: "form-actions" }, cancel, submit));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!onSubmit) return;
    const values = {};
    for (const [f, el] of controls) {
      if (el.disabled) continue;
      values[f.name] = f.type === "number" ? (el.value === "" ? null : Number(el.value)) : el.value;
    }
    submit.disabled = true;
    error.textContent = "";
    try {
      await onSubmit(values);
      dialog.close();
      dialog.remove();
    } catch (err) {
      error.textContent = cleanDetail(err.message);
      submit.disabled = false;
    }
  });
  dialog.appendChild(form);
  dialog.addEventListener("close", () => dialog.remove());
  document.body.appendChild(dialog);
  dialog.showModal();
  (controls[0]?.[1] ?? cancel).focus();
  return dialog;
}

let catalogPromise = null;
function catalogItems() {
  catalogPromise ??= api("/items?limit=1000")
    .then((r) => (Array.isArray(r) ? r : r.items ?? []))
    .catch(() => {
      catalogPromise = null; // try again next time
      return [];
    });
  return catalogPromise;
}

export const btn = (label, onClick) => h("button", { type: "button", class: "tool", onClick }, label);

export function editOpts(c, redraw) {
  const write = async (path, method, body) => redraw(await api(path, { method, body }));

  const tools = (key) => {
    if (key === "gold") {
      return btn("Edit", () => openForm({
        title: "Edit gold",
        fields: [{ name: "gold", label: "Gold", type: "text", value: c.gold, hint: "Like 12gp 5sp 3cp", required: true }],
        onSubmit: (v) => write("/character/gold", "PATCH", { gold: v.gold }),
      }));
    }
    if (key === "held" || key === "stash") {
      return btn("Add", () => addItemDialog(key, write));
    }
    if (key === "effects") {
      return btn("Add", () => effectDialog(null, write));
    }
    if (key === "spells") {
      const rest = c.spells.some((s) => s.lost)
        ? btn("Rest", () => write("/character/spells/rest", "POST").catch(showFailure("Rest")))
        : null;
      return [rest, c.spellcasting ? btn("Learn", learnSpell) : null];
    }
    if (key === "identity" || key === "background") {
      return btn("Edit", () => openForm({
        title: "Edit character",
        fields: [
          { name: "name", label: "Name", type: "text", value: c.name, required: true },
          { name: "char_class", label: "Class", type: "text", value: c.char_class },
          { name: "title", label: "Title", type: "text", value: c.title },
          { name: "ancestry", label: "Ancestry", type: "text", value: c.ancestry },
          { name: "alignment", label: "Alignment", type: "select", value: (c.alignment ?? "").toLowerCase(),
            options: [["", "None"], ["lawful", "Lawful"], ["neutral", "Neutral"], ["chaotic", "Chaotic"]] },
          { name: "background", label: "Background", type: "textarea", value: c.background },
        ],
        onSubmit: (v) => write("/character/identity", "PATCH", {
          name: v.name, char_class: v.char_class || null, title: v.title || null,
          ancestry: v.ancestry || null, alignment: v.alignment || null, background: v.background || null,
        }),
      }));
    }
    return moreTools(key);
  };

  const learnSpell = async () => {
        try {
          const cls = c.spellcasting.ability === "wis" ? "priest" : "wizard";
          const res = await api(`/spells?class=${cls}`);
          const list = Array.isArray(res) ? res : res.spells ?? res.items ?? [];
          const known = new Set(c.spells.map((s) => s.spell?.id));
          const avail = list.filter((s) => !known.has(s.id));
          if (!avail.length) return openForm({ title: "Learn spell", message: "You know every spell on your list." });
          openForm({
            title: "Learn spell",
            fields: [{ name: "spell_id", label: "Spell", type: "select",
              options: avail.map((s) => [s.id, `${s.name} (tier ${s.tier})`]) }],
            submitLabel: "Learn",
            onSubmit: (v) => write("/character/spells", "POST", { spell_id: Number(v.spell_id) }),
          });
        } catch (err) {
          openForm({ title: "Learn spell", message: cleanDetail(err.message) });
        }
  };

  const addXp = () => openForm({
    title: "Add XP",
    message: `You have ${c.xp} of ${c.xp_needed} XP. Use a negative number to remove XP.`,
    fields: [{ name: "add", label: "XP to add", type: "number", step: 1, required: true }],
    submitLabel: "Add",
    onSubmit: (v) => write("/character/xp", "POST", { add: v.add ?? 0 }),
  });

  const moreTools = (key) => {
    if (key === "vitals" || key === "abilities") {
      const edit = btn("Edit", () => openForm({
        title: "Edit stats",
        fields: [
          ...c.abilities.map((a) => ({ name: "score_" + a.key, label: a.label, type: "number",
            value: a.score, min: 1, max: 30, required: true })),
          { name: "level", label: "Level", type: "number", value: c.level, min: 1, required: true },
          { name: "xp", label: "XP", type: "number", value: c.xp, min: 0, required: true,
            hint: `${c.xp_needed} needed for the next level` },
          { name: "max_hp", label: "Max HP", type: "number", value: c.max_hp, min: 0 },
          { name: "armor_class", label: "Base AC (without DEX)", type: "number", value: c.armor_class_base },
          { name: "spell_ability", label: "Spellcasting stat", type: "select",
            value: c.spellcasting?.ability ?? "none", options: [["none", "None"], ["int", "INT"], ["wis", "WIS"]] },
          { name: "spell_check_bonus", label: "Spellcasting talent bonus", type: "number",
            value: c.spellcasting?.talent_bonus ?? 0 },
        ],
        onSubmit: (v) => write("/character/stats", "PATCH", {
          scores: c.abilities.map((a) => v["score_" + a.key]),
          level: v.level, xp: v.xp, max_hp: v.max_hp, armor_class: v.armor_class,
          spell_ability: v.spell_ability, spell_check_bonus: v.spell_check_bonus ?? 0,
        }),
      }));
      return key === "vitals" ? [btn("+XP", addXp), edit] : edit;
    }
    if (key === "languages" || key === "talents" || key === "additional") {
      return btn("Edit", () => openForm({
        title: "Edit skills",
        fields: [
          { name: "languages", label: "Languages", type: "text", value: c.languages, hint: "Comma separated, like Common, Elvish" },
          { name: "talents", label: "Talents", type: "textarea", value: c.talents },
          { name: "additional_info", label: "Additional info", type: "textarea", value: c.additional_info },
        ],
        onSubmit: (v) => write("/character/skills", "PATCH", {
          languages: v.languages || null, talents: v.talents || null, additional_info: v.additional_info || null,
        }),
      }));
    }
    if (key === "proficiencies") {
      return btn("Edit", () => openForm({
        title: "Edit proficiencies",
        fields: [{ name: "proficiencies", label: "Proficiencies", type: "textarea", value: c.proficiencies }],
        onSubmit: (v) => write("/character/proficiencies", "PATCH", { proficiencies: v.proficiencies || null }),
      }));
    }
    return null;
  };

  const itemActions = (item) => [
    btn("Edit", () => editItemDialog(item, write)),
    btn("Give", async () => {
      try {
        const res = await api(`/character/items/${item.id}/give-targets`);
        const targets = Array.isArray(res) ? res : res.targets ?? [];
        if (!targets.length) return openForm({ title: "Give", message: "Nowhere to give this to." });
        openForm({
          title: `Give ${item.name}`,
          fields: [
            { name: "target", label: "To", type: "select",
              options: targets.map((t) => [t.location_id ?? t.id,
                t.kind === "guild" ? `Guild: ${t.name}` : t.name]) },
            { name: "quantity", label: "Quantity", type: "number", value: item.quantity, min: 1, max: item.quantity },
          ],
          submitLabel: "Give",
          onSubmit: (v) => write(`/character/items/${item.id}/give`, "POST",
            { target_location_id: Number(v.target), quantity: v.quantity ?? item.quantity }),
        });
      } catch (err) {
        openForm({ title: "Give", message: cleanDetail(err.message) });
      }
    }),
    btn("Drop", () => openForm({
      title: `Drop ${item.name}`,
      fields: [{ name: "quantity", label: "Quantity", type: "number", value: item.quantity, min: 1, max: item.quantity }],
      submitLabel: "Drop",
      danger: true,
      onSubmit: (v) => write(`/character/items/${item.id}?quantity=${v.quantity ?? item.quantity}`, "DELETE"),
    })),
  ];

  const spellActions = (s) => btn("Forget", () => openForm({
    title: "Forget spell",
    message: `Forget ${s.name}?`,
    submitLabel: "Forget",
    danger: true,
    onSubmit: () => write(`/character/spells/${s.id}`, "DELETE"),
  }));

  // A checkbox-style toggle: ticked = available, unticked = lost until rest.
  // Updates the row at once, redraws from the reply, reverts on failure.
  const spellToggle = (s) => {
    const box = h("input", { type: "checkbox", checked: !s.lost || null });
    const label = h("label", { class: "avail", title: "Available" }, box,
      h("span", { class: "visually-hidden" }, `${s.name} available`));
    box.addEventListener("change", async () => {
      const li = label.closest("li");
      li?.classList.toggle("lost", !box.checked);
      try {
        await write(`/character/spells/${s.id}`, "PATCH", { lost: !box.checked });
      } catch (err) {
        box.checked = !s.lost;
        li?.classList.toggle("lost", s.lost);
        showFailure("Spell")(err);
      }
    });
    return label;
  };

  const tickEffect = async (e, used) => {
    try {
      await write(`/character/effects/${e.id}`, "PATCH", { rounds_used: used });
    } catch (err) {
      showFailure("Effect")(err);
      throw err; // lets the row revert its boxes
    }
  };

  const effectActions = (e) => [
    btn("Edit", () => effectDialog(e, write)),
    e.max_rounds !== null && e.rounds_used > 0
      ? btn("Reset", () => write(`/character/effects/${e.id}`, "PATCH", { rounds_used: 0 })
          .catch(showFailure("Effect")))
      : null,
    btn("Remove", () => write(`/character/effects/${e.id}`, "DELETE").catch(showFailure("Effect"))),
  ];

  return { tools, itemActions, spellActions, spellToggle, tickEffect, effectActions };
}

// ---------- add / edit an effect ----------

export const BONUS_FIELDS = [
  ["ac", "AC"], ["hp", "Max HP"], ["str", "STR modifier"], ["dex", "DEX modifier"],
  ["con", "CON modifier"], ["int", "INT modifier"], ["wis", "WIS modifier"], ["cha", "CHA modifier"],
];

// The shared form fields for an effect (name, duration, stat changes) and the
// reverse step, used by the sheet's effects and the effects library.
export function effectFields(effect) {
  return [
    { name: "name", label: "Name", type: "text", value: effect?.name, required: true },
    { name: "max_rounds", label: "Max rounds", type: "number", min: 1, max: 100,
      value: effect?.max_rounds, hint: "Leave blank for no duration" },
    ...BONUS_FIELDS.map(([key, label]) => ({
      name: `b_${key}`, label, type: "text", value: effect?.bonuses?.[key] ?? "",
      hint: key === "ac" ? "+2 adds, -1 subtracts, =14 sets" : null,
    })),
  ];
}

export function bonusesFrom(v) {
  const bonuses = {};
  for (const [key] of BONUS_FIELDS) bonuses[key] = v[`b_${key}`] ?? "";
  return bonuses;
}

function effectDialog(effect, write) {
  openForm({
    title: effect ? `Edit ${effect.name}` : "Add effect",
    fields: effectFields(effect),
    submitLabel: effect ? "Save" : "Add",
    onSubmit: (v) => {
      const body = { name: v.name, max_rounds: v.max_rounds, bonuses: bonusesFrom(v) };
      return effect
        ? write(`/character/effects/${effect.id}`, "PATCH", body)
        : write("/character/effects", "POST", body);
    },
  });
}

// ---------- Guild page actions ----------
// Guild writes return the refreshed /api/guild body, handed to redraw().
// Only offered when the viewer has a character (callers check me.has_character).

export function guildEditOpts(me, redraw) {
  const write = async (path, body) => redraw(await api(path, { method: "POST", body }));

  const takeAction = (loc, item) => btn("Take", () => openForm({
    title: `Take ${item.name}`,
    message: `${item.quantity} in ${loc.name}.`,
    fields: [
      { name: "quantity", label: "Quantity", type: "number", value: 1, min: 1, max: item.quantity },
      { name: "to", label: "Put in", type: "select", value: "held", options: [["held", "Carrying"], ["stash", "Stash"]] },
    ],
    submitLabel: "Take",
    onSubmit: (v) => write(`/guild/locations/${loc.location_id}/items/${item.id}/take`,
      { quantity: v.quantity ?? 1, to: v.to }),
  }));

  // Available -> Borrow; mine -> Return; someone else's -> nothing (the row shows who).
  const treasuryAction = (t) => {
    if (t.status === "available") {
      return btn("Borrow", () => write(`/guild/treasury/${t.id}/borrow`).catch(showFailure("Borrow")));
    }
    if (t.borrower?.user_id === me.user_id) {
      return btn("Return", () => write(`/guild/treasury/${t.id}/return`).catch(showFailure("Return")));
    }
    return null;
  };

  return { takeAction, treasuryAction };
}

export const showFailure = (title) => (err) => openForm({ title, message: cleanDetail(err.message) });

// ---------- add / edit an item (a block of its own) ----------

async function addItemDialog(role, write) {
  const items = await catalogItems();
  const catalogOptions = items.map((i) => [
    i.name,
    `${i.name} · ${slots(i.gear_slots)} sl${i.bundle_size > 1 ? ` per ${i.bundle_size}` : ""}`,
  ]);
  const hasCatalog = catalogOptions.length > 0;
  openForm({
    title: "Add item",
    fields: [
      { name: "mode", label: "Kind of item", type: "select", value: hasCatalog ? "catalog" : "custom",
        options: hasCatalog ? [["catalog", "From catalog"], ["custom", "Custom"]] : [["custom", "Custom"]] },
      { name: "catalog_name", label: "Catalog item", type: "select", required: true, size: 6,
        filter: "Search the catalog", options: catalogOptions, showWhen: ["mode", "catalog"] },
      { name: "name", label: "Name", type: "text", required: true, showWhen: ["mode", "custom"] },
      { name: "slots_each", label: "Gear slots each", type: "number", value: 1, min: 0, step: "any",
        showWhen: ["mode", "custom"] },
      { name: "notes", label: "Notes", type: "textarea", showWhen: ["mode", "custom"] },
      { name: "quantity", label: "Quantity", type: "number", value: 1, min: 1, required: true },
      { name: "to", label: "Put in", type: "select", value: role, options: [["held", "Carrying"], ["stash", "Stash"]] },
    ],
    submitLabel: "Add",
    onSubmit: (v) => write("/character/items", "POST", v.mode === "catalog"
      ? { name: v.catalog_name, quantity: v.quantity ?? 1, to: v.to }
      : { name: v.name, quantity: v.quantity ?? 1, to: v.to, slots_each: v.slots_each ?? 1, notes: v.notes || null }),
  });
}

function editItemDialog(item, write) {
  const catalog = item.source === "catalog";
  const notes = catalog ? item.notes : item.description;
  openForm({
    title: `Edit ${item.name}`,
    message: catalog ? "Name and size come from the catalog." : null,
    fields: [
      ...(catalog ? [] : [
        { name: "name", label: "Name", type: "text", value: item.name, required: true },
        { name: "slots_each", label: "Gear slots each", type: "number", value: item.gear_slots_each,
          min: 0, step: "any", required: true },
      ]),
      { name: "quantity", label: "Quantity", type: "number", value: item.quantity, min: 1, required: true },
      { name: "notes", label: "Notes", type: "textarea", value: notes ?? "" },
    ],
    onSubmit: (v) => write(`/character/items/${item.id}`, "PATCH", catalog
      ? { quantity: v.quantity, notes: v.notes }
      : { name: v.name, slots_each: v.slots_each, quantity: v.quantity, notes: v.notes }),
  });
}

// ---------- buy from the catalog (a block of its own) ----------

const price = (cp) => coins(cp);

// Opens the buy dialog for a catalog item; `onBought(message)` gets a short
// confirmation once the purchase went through.
export async function buyDialog(item, onBought) {
  let guild;
  try {
    guild = await api("/guild");
  } catch {
    guild = { coffers: { balance_cp: 0, balance: "0cp" }, locations: [] };
  }
  const me = await api("/character");
  const places = [
    [`held:${me.held.location_id}`, "Carrying"],
    [`stash:${me.stash.location_id}`, "Stash"],
    ...(item.item_type === "magical" ? [] : guild.locations.map((l) => [`guild:${l.location_id}`, `Guild: ${l.name}`])),
  ];
  const total = h("strong", {});
  const dialog = openForm({
    title: `Buy ${item.name}`,
    message: `${price(item.value_cp)} each. You have ${me.gold || "0cp"}; the coffers hold ${guild.coffers.balance || "0cp"}.`,
    fields: [
      { name: "quantity", label: "Quantity", type: "number", value: 1, min: 1, required: true },
      { name: "pay_from", label: "Pay with", type: "select", value: "character",
        options: [["character", "My gold"], ["guild", "Guild coffers"]] },
      { name: "dest", label: "Put in", type: "select", value: places[0][0], options: places },
    ],
    submitLabel: "Buy",
    onSubmit: async (v) => {
      const qty = v.quantity ?? 1;
      const [, locId] = v.dest.split(":");
      await api(`/items/${item.id}/buy`, { method: "POST", body: {
        quantity: qty, pay_from: v.pay_from, destination_location_id: Number(locId) } });
      const where = places.find(([val]) => val === v.dest)[1];
      onBought(`Bought ${qty > 1 ? `${qty}× ` : ""}${item.name} for ${price(item.value_cp * qty)}`
        + ` — paid from ${v.pay_from === "guild" ? "guild coffers" : "your gold"}, put in ${where}.`);
    },
  });
  // live total under the quantity field
  const qtyInput = dialog.querySelector("#f-quantity");
  const form = dialog.querySelector("form");
  const line = h("p", { class: "form-total" }, "Total: ", total);
  form.insertBefore(line, form.querySelector(".form-actions"));
  const update = () => { total.textContent = price(item.value_cp * Math.max(1, Number(qtyInput.value) || 1)); };
  qtyInput.addEventListener("input", update);
  update();
}
