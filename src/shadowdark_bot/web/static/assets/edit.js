// Editing layer for your own sheet: one modal form helper plus the wiring that
// feeds sheetBody's tool/action slots. Every write returns the full sheet JSON,
// which is handed to redraw().

import { api } from "./api.js";
import { h } from "./dom.js";

export const cleanDetail = (s) => String(s ?? "").replace(/\*\*/g, "");

function control(f, listId) {
  const common = { name: f.name, id: `f-${f.name}`, required: f.required || null };
  if (f.type === "select") {
    return h("select", common, (f.options ?? []).map(([v, l]) =>
      h("option", { value: v, selected: String(v) === String(f.value ?? "") || null }, l)));
  }
  if (f.type === "textarea") {
    const t = h("textarea", { ...common, rows: 4 });
    t.value = f.value ?? "";
    return t;
  }
  const el = h("input", {
    ...common,
    type: f.type === "number" ? "number" : "text",
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
  const rows = fields.map((f, i) => {
    let listId = null;
    let list = null;
    if (f.suggestions?.length) {
      listId = `dl-${f.name}`;
      list = h("datalist", { id: listId }, f.suggestions.map((s) => h("option", { value: s })));
    }
    const el = control(f, listId);
    controls.push([f, el]);
    return h("div", { class: "field" },
      h("label", { for: `f-${f.name}` }, f.label),
      el, list,
      f.hint ? h("small", { class: "muted" }, f.hint) : null);
  });
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
function catalogNames() {
  catalogPromise ??= api("/items?limit=1000")
    .then((r) => (Array.isArray(r) ? r : r.items ?? []).map((i) => i.name))
    .catch(() => []);
  return catalogPromise;
}

const btn = (label, onClick) => h("button", { type: "button", class: "tool", onClick }, label);

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
      return btn("Add", async () => {
        const names = await catalogNames();
        openForm({
          title: "Add item",
          fields: [
            { name: "name", label: "Item", type: "text", suggestions: names, required: true },
            { name: "quantity", label: "Quantity", type: "number", value: 1, min: 1 },
            { name: "to", label: "Put in", type: "select", value: key, options: [["held", "Carrying"], ["stash", "Stash"]] },
          ],
          submitLabel: "Add",
          onSubmit: (v) => write("/character/items", "POST", { name: v.name, quantity: v.quantity ?? 1, to: v.to }),
        });
      });
    }
    if (key === "spells" && c.spellcasting) {
      return btn("Learn", async () => {
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
      });
    }
    return null;
  };

  const itemActions = (item) => [
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

  return { tools, itemActions, spellActions };
}
