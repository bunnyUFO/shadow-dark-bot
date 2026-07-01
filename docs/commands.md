# Bot Commands

Every command is a Discord **slash command**, available to anyone in the server.

For now, **all commands are open to all guild members**. There's no role-gating — anyone can add, take, borrow, return, or even delete entries. Built-in invariants (see the bottom of this page) prevent the worst classes of mistakes. Role-based permissions and an audit log feed are on the roadmap.

## Primary entry point: `/<group> browse`

Each of the four systems has a **`browse`** command that is the main, all-in-one way to interact with it. Browse lists what's there, lets you drill down into a specific item or entry, and exposes the common actions as buttons / dropdowns inside the same ephemeral view. The narrower slash commands (`/items add`, `/inventory add`, `/treasury add`, `/coffers add` and friends) are still around as shortcuts for power use, but **most day-to-day use should go through browse**:

- `/items browse` — list / inspect / edit the catalog.
- `/inventory browse` — list locations / inspect contents / add more or take items already at a location. (Use `/inventory add` to add a brand-new item to a location.)
- `/treasury browse` — list magical instances / inspect / borrow / return.
- `/coffers browse` — see the balance / buy catalog items.

If you're not sure which command to run, run `/<group> browse`.

## Visibility (everything ephemeral; share button on every response)

Every command replies **ephemerally** — only the invoker sees the response. Each response includes a `📢 Share with channel` button; clicking it posts a public copy (the current embed and/or message content) prefixed with "Shared by @you", then disables itself to prevent duplicate shares.

This applies uniformly to reads (`/items info`/`browse`, `/inventory browse`, `/treasury browse`, `/coffers browse`) and to writes (`/inventory add`, `/treasury add`/`remove`, `/coffers add`/`subtract`, `/items add`/`edit`/`remove`, all `/inventory location-*` commands). Editing lives inside `/items browse` (Edit button → modal), borrowing/returning inside `/treasury browse`, taking (and re-adding) inside `/inventory browse`, and buying inside `/coffers browse` — the views update in place; hit Share if you want to announce.

---

## `/items` — Item Catalog

The catalog is the single source of truth for what an item *is*. Inventory and treasury entries reference catalog rows. Item value is entered in gold/silver/copper pieces and stored internally as copper (1 gp = 10 sp = 100 cp).

Every item has a **type**: one of `common`, `weapon`, `armor`, `scroll`, `potion`, `loot`, `crafted`, or `magical`. Only `magical` items live in the treasury; all seven other types stack in inventory locations. The non-magical types are informational categories — useful for filtering and grouping in `/items browse`, but behave identically in inventory.

Items also have a **bundle size**: how many fit in one gear slot. Defaults to 1. Arrows might be `gear_slots:1, bundle_size:20`, meaning 1–20 arrows take 1 slot, 21–40 take 2, etc. (ceiling rule per the SD rulebook).

| Command | What it does | Example |
|---|---|---|
| `/items add name type description? gear_slots? bundle_size? gp? sp? cp?` | Create a new catalog entry. `type` picker selects one of the eight item types. `gear_slots` and `bundle_size` both default to 1. Errors on duplicate name. | `/items add name:"Arrows" type:common bundle_size:20 sp:1` |
| `/items info name` | Embed with description, gear slots (with bundle if > 1), type, and value. Color-coded by type. | `/items info name:"Wand of Sparks"` |
| `/items edit name type?` | Opens a form pre-filled with current values for name, description, gear slots, bundle size, and value (`"5gp 2sp"`-style string — blank means unset). Edit what you want, hit submit. Optional `type:` arg also changes the item's type; switching to/from `magical` is blocked if the item is currently referenced by inventory or treasury entries. | `/items edit name:"Arrows"` |
| `/items remove name` | Remove a catalog entry. Blocked if any inventory or treasury entry still references it. | `/items remove name:"Rope, 50ft"` |
| `/items browse type?` | The main way to explore the catalog. Lists items (grouped by type when unfiltered, flat when filtered) with an "Inspect an item…" dropdown. The drilled-down item view has an `Edit` button that opens the same modal as `/items edit`. | `/items browse type:weapon` |

**Tip:** `name` parameters autocomplete from the catalog as you type. The `type` param on edit is optional — leave it off to just tweak fields.

---

## `/inventory` — Shared Non-Magical Stashes

For items donated to the guild that anyone can take freely (rope, rations, torches, etc.). Multiple named locations are supported (e.g., "Main Stash", "Lakeside Cabin"). Each location has a **max capacity in gear slots**; the total slots used by items in the location cannot exceed it. Locations default to 0 max gear slots if not specified; items default to 1 gear slot.

| Command | What it does | Example |
|---|---|---|
| `/inventory location-create name max_gear_slots? description?` | Make a new shared storage location. Capacity defaults to 0 — adjust with `/inventory location-edit` later. | `/inventory location-create name:"Main Stash" max_gear_slots:20` |
| `/inventory location-edit name max_gear_slots? description?` | Update capacity or description. Refuses if shrinking capacity below current usage. | `/inventory location-edit name:"Main Stash" max_gear_slots:30` |
| `/inventory location-delete name` | Remove a location. Must be empty. | `/inventory location-delete name:"Old Camp"` |
| `/inventory add location item quantity=1 notes?` | Add to a stack at a location. Item must exist in the catalog and be any type **except magical**. Same item at the same location always merges into one stack; the capacity check uses the bundle-aware delta, so adding to a partially-filled bundle may consume 0 extra slots. | `/inventory add location:"Main Stash" item:"Arrows" quantity:15` |
| `/inventory browse location?` | The main way to inspect and act on inventory. No `location` arg: lists every inventory location with `used/max` slot utilization. Three-level drill-down: pick a location → see its contents (with an "Inspect an item…" dropdown) → pick an item → see its catalog info with a footer showing how many are at that location, plus `+ Add more` and `− Take` quick-action buttons that open small quantity modals. To add an item that isn't already at the location, use `/inventory add` — it has location/item autocomplete. With `location:`: jumps straight to that location's detail view. | `/inventory browse location:"Main Stash"` |

**Common flow** — donating loot after a session:
```
/items add name:"Lantern" type:common gear_slots:1 gp:5
/inventory location-create name:"Main Stash" max_gear_slots:20
/inventory add location:"Main Stash" item:"Lantern" quantity:2 notes:"from the goblin cave"
/inventory browse location:"Main Stash"
```

**Bundle stacking** — partial-bundle adds are free until the bundle overflows:
```
/items add name:"Arrows" type:common gear_slots:1 bundle_size:20
/inventory add location:"Main Stash" item:"Arrows" quantity:5   → stack 5,  1 slot used
/inventory add location:"Main Stash" item:"Arrows" quantity:15  → stack 20, still 1 slot
/inventory add location:"Main Stash" item:"Arrows" quantity:1   → stack 21, 2 slots
```

---

## `/coffers` — Shared Guild Coffers

A single balance shared by the guild, tracked internally in copper pieces (1 gp = 10 sp = 100 cp). Displays auto-normalize ("105 cp" shown as "1gp 5cp"). The balance row is auto-created on first use.

| Command | What it does | Example |
|---|---|---|
| `/coffers add gp? sp? cp? reason?` | Add funds. At least one of gp/sp/cp must be a positive value. Reply shows the new balance. | `/coffers add gp:50 sp:5 reason:"dungeon loot"` |
| `/coffers subtract gp? sp? cp? reason?` | Subtract funds. Refuses if the result would be negative (reports the shortfall). Reply shows the new balance. | `/coffers subtract gp:2 reason:"paying innkeeper"` |
| `/coffers browse` | Show the current balance with a dropdown of buyable catalog items (those with a value set). Picking one opens a small modal for quantity (default 1) and deducts on submit. The view refreshes to show the new balance, then a confirmation lands as a followup with a Share button. The only way to buy from coffers. | `/coffers browse` |

**Tip:** The `/coffers browse` dropdown only lists catalog items that have a value set, so you can't accidentally pick something with no price. Every `add`/`subtract`/`browse`-buy reply includes the resulting balance — there's no standalone "show" command; open `/coffers browse` to peek.

**Common flow** — splitting loot after a session:
```
/coffers add gp:120 reason:"dungeon haul"
/coffers browse
→ pick "Healing Potion" from the dropdown
→ enter quantity, submit
```

---

## `/treasury` — Magical Item Borrow Tracking

For guild-owned magical items. They are **never given away** — they're borrowed and returned. Each physical item is its own row, so the guild can own two "Wand of Sparks" instances and track each independently. There's a single, auto-managed treasury (no location concept exposed); items are added to it and borrowed/returned from it.

| Command | What it does | Example |
|---|---|---|
| `/treasury add item tag?` | Register a magical item instance. The catalog entry must have `type:magical`. Use `tag` to distinguish duplicates ("chipped", etc.). Duplicates allowed. | `/treasury add item:"Wand of Sparks" tag:"chipped"` |
| `/treasury remove entry_id` | Remove an instance. Must be currently available (not borrowed). Use the autocomplete to pick by name; the integer ID is sent automatically. | `/treasury remove entry_id:#7` |
| `/treasury browse status?` | The single entry point for everything else — list, inspect, borrow, return. Lists all treasury items grouped by Available / Borrowed (header shows totals — informational, no cap). `status:available` or `status:borrowed` filters to one group. The reply is ephemeral with an "Inspect an entry…" dropdown to drill into a detail view; from there you can `Borrow for me`, pick a member from the `Borrow for someone…` dropdown, or `Return` (when borrowed). The detail view also has a `← Back to treasury` button. | `/treasury browse status:available` |

**Common flow** — borrowing for an adventure:
```
/treasury browse status:available
→ pick an entry from the dropdown
→ click "Borrow for me"
... session ends ...
/treasury browse
→ pick the same entry
→ click "Return"
```

**Common flow** — borrowing on behalf of someone else:
```
/treasury browse status:available
→ pick an entry
→ open the "Borrow for someone…" dropdown and select @Tessa
```
**Note:** because everything is ephemeral, the borrower isn't auto-pinged. Hit the Share button on the detail view to announce it in the channel, or ping them yourself.

---

## `/character` — Your Player Character

Unlike the four guild-shared systems above, a **character belongs to one player** (keyed by Discord user). Each player has **one** character with lean Shadow Dark stats and a carried inventory. You edit **your own** character through an ephemeral, interactive sheet; you can **view** anyone else's read-only.

Stats covered: name, class, level, **max HP**, AC, the six ability scores (with an auto-computed modifier table — Shadow Dark's `floor((score−10)/2)` clamped to ±4), gold (entered gp/sp/cp, stored as copper), a free-text **Talents** section, an optional **spellcasting** modifier (governing stat INT or WIS plus a manual spell-check bonus for talent-granted bonuses), and a **known spells** list.

Inventory is **hybrid**: a carried item either links to the shared `/items` catalog (reusing its gear-slot / bundle data) or is a **freeform** typed name with its own per-item slot cost. Carry capacity is the Shadow Dark limit: **max(10, STR)** gear slots, using the same bundle-aware ceiling math as guild inventory.

| Command | What it does | Example |
|---|---|---|
| `/character sheet` | Open **your** sheet as an ephemeral, interactive embed. If you don't have a character yet, a **Create character** button opens the details form. The sheet's buttons — **Edit Details** (name/class/level/max HP/AC), **Edit Abilities & Gold** (six scores + gold), **Edit Talents & Casting** (talents, spellcasting stat, spell bonus), **Manage Inventory**, and **Manage Spells** — open modals or swap the view in place. | `/character sheet` |
| `/character carry item quantity? gear_slots?` | Add an item you're carrying. Autocomplete suggests catalog items (a match links it); any other text becomes a freeform item. `gear_slots` sets the per-item slot cost for a new freeform item (ignored for catalog items). Blocked if it would exceed your carry capacity. | `/character carry item:"Arrows" quantity:20` |
| `/character spell-add spell` | Learn a spell. Autocomplete suggests the built-in Shadow Dark reference (Tier 1–2 wizard/priest); a match links its full text (duration/range/description). Any other text is recorded as a freeform spell. | `/character spell-add spell:"Magic Missile"` |
| `/character spell-remove spell` | Forget a spell you know (autocomplete over your known spells). | `/character spell-remove spell:"Sleep"` |
| `/character show member` | View another player's sheet read-only. Toggle between **Stats** and **Inventory**; no edit buttons. Includes the Share button. | `/character show member:@Tessa` |
| `/character delete` | Delete your character (and everything it carries) after a confirm button. | `/character delete` |

**Manage Inventory** (from `/character sheet`) lists your stacks with a dropdown; drilling into one exposes **+ Add more**, **− Take**, and **Remove**, plus an **Add item** modal for quick freeform entries. Everything re-checks capacity and updates in place.

**Manage Spells** works the same way: a dropdown of your known spells (grouped by tier on the sheet), a detail view showing the reference text with a **Forget** button, and an **Add spell** modal. The built-in reference covers the Player Quickstart's Tier 1–2 wizard and priest spells; higher tiers are supported as freeform entries. Spells-known limits are **not** enforced — the list is a tracker, not a rules engine.

---

## Built-in invariants (not permissions, but they will block you)

Even though commands are open, the bot enforces invariants that prevent broken state:

- Adding a magical item to inventory (or a non-magical item to treasury) errors.
- Borrowing an already-borrowed treasury instance errors.
- Returning an instance that isn't currently borrowed errors.
- Removing a treasury instance that's currently borrowed errors — return it first.
- Taking more than a stack contains errors.
- Adding to inventory beyond a location's gear-slot capacity errors (capacity uses the bundle-aware ceiling formula).
- Shrinking a location's capacity below currently-used slots errors.
- Deleting a catalog entry that's still referenced by any inventory or treasury row errors.
- Deleting a non-empty location errors.
- Changing a catalog item's `type` into or out of `magical` is blocked while it's referenced by inventory or treasury entries. Switching between any of the non-magical types (common/weapon/armor/scroll/potion/loot/crafted) is always allowed.
- Renaming an item via the `/items edit` modal's Name field is rejected if another item already has that name.
- You can only edit **your own** character; the interactive sheet's controls reject anyone who isn't the owner. `/character show` is always read-only.
- Carrying items past your capacity — **max(10, STR)** gear slots — errors (bundle-aware ceiling formula, same as guild inventory).
- Each player has at most one character; the sheet upserts rather than creating duplicates.

Failed operations reply ephemerally (visible only to you) — no silent failures, no stack traces.
