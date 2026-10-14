// Which library effects to offer first on your sheet: those linked to a spell
// you know or an item you are carrying. DOM-free so it can be tested under Node.
//
//   templates: [{id, links: [{kind, target_id}]}]   (GET /api/effects)
//   sheet:     the character JSON (spells[].spell.id, held.items[].item_id)
// Returns the Set of suggested template ids.
export function suggestedIds(templates, sheet) {
  const spells = new Set((sheet.spells ?? []).map((s) => s.spell?.id).filter((id) => id != null));
  const items = new Set((sheet.held?.items ?? []).map((i) => i.item_id).filter((id) => id != null));
  const out = new Set();
  for (const t of templates) {
    if (t.links.some((l) => (l.kind === "spell" && spells.has(l.target_id))
      || (l.kind === "item" && items.has(l.target_id)))) out.add(t.id);
  }
  return out;
}
