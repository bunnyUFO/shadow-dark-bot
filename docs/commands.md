# Bot Commands

Every command is a Discord **slash command**, available to anyone in the server.

For now, **all commands are open to all guild members**. There's no role-gating — anyone can add, take, borrow, return, or even delete entries. Built-in invariants (see the bottom of this page) prevent the worst classes of mistakes. Role-based permissions and an audit log feed are on the roadmap.

---

## `/items` — Item Catalog

The catalog is the single source of truth for what an item *is*. Inventory and treasury entries reference catalog rows. Item value is entered in gold/silver/copper pieces and stored internally as copper (1 gp = 10 sp = 100 cp).

Every item has a **type**: one of `common`, `magical`, `crafted`, `scroll`, or `potion`. Only `magical` items live in the treasury; the other four all stack in inventory locations. Crafted/scroll/potion are informational categories — useful for filtering and grouping, but behave like common.

Items also have a **bundle size**: how many fit in one gear slot. Defaults to 1. Arrows might be `gear_slots:1, bundle_size:20`, meaning 1–20 arrows take 1 slot, 21–40 take 2, etc. (ceiling rule per the SD rulebook).

| Command | What it does | Example |
|---|---|---|
| `/items add name type description? gear_slots? bundle_size? gp? sp? cp?` | Create a new catalog entry. `type` picker selects one of the five item types. Errors on duplicate name. | `/items add name:"Arrows" type:common gear_slots:1 bundle_size:20 sp:1` |
| `/items info name` | Embed with description, gear slots (with bundle if > 1), type, and value. Color-coded by type. | `/items info name:"Wand of Sparks"` |
| `/items edit name [new_name?] [fields…]` | Update any field, including renaming via `new_name`. Providing any of `gp`/`sp`/`cp` replaces the whole value. Switching `type` to/from `magical` is blocked if the item is currently referenced by inventory or treasury entries. | `/items edit name:"Arrows" new_name:"Arrows, cold iron" bundle_size:20` |
| `/items remove name` | Remove a catalog entry. Blocked if any inventory or treasury entry still references it. | `/items remove name:"Rope, 50ft"` |
| `/items list type?` | List the catalog, grouped by type (Common / Magical / Crafted / Scrolls / Potions). Use `type:<value>` to filter to one group. | `/items list type:magical` |

**Tip:** `name` parameters autocomplete from the catalog as you type. The `type` and `new_name` parameters are both optional on edit — change just the one field you want.

---

## `/inventory` — Shared Non-Magical Stashes

For items donated to the guild that anyone can take freely (rope, rations, torches, etc.). Multiple named locations are supported (e.g., "Main Stash", "Lakeside Cabin"). Each location has a **max capacity in gear slots**; the total slots used by items in the location cannot exceed it. Locations and items both default to 0 gear slots if not specified.

| Command | What it does | Example |
|---|---|---|
| `/inventory location-create name max_gear_slots? description?` | Make a new shared storage location. Capacity defaults to 0 — adjust with `/inventory location-edit` later. | `/inventory location-create name:"Main Stash" max_gear_slots:20` |
| `/inventory location-edit name max_gear_slots? description?` | Update capacity or description. Refuses if shrinking capacity below current usage. | `/inventory location-edit name:"Main Stash" max_gear_slots:30` |
| `/inventory location-delete name` | Remove a location. Must be empty. | `/inventory location-delete name:"Old Camp"` |
| `/inventory add location item quantity=1 notes?` | Add to a stack at a location. Item must exist in the catalog and be any type **except magical**. Same item at the same location always merges into one stack; the capacity check uses the bundle-aware delta, so adding to a partially-filled bundle may consume 0 extra slots. | `/inventory add location:"Main Stash" item:"Arrows" quantity:15` |
| `/inventory take location item quantity=1` | Take from a stack. Decrements the quantity; the stack is deleted when it reaches zero. | `/inventory take location:"Main Stash" item:"Rope, 50ft"` |
| `/inventory list location?` | No `location` arg: list every inventory location with `used/max` slot utilization. With `location`: detailed contents + capacity header. | `/inventory list location:"Main Stash"` |

**Common flow** — donating loot after a session:
```
/items add name:"Lantern" type:common gear_slots:1 gp:5
/inventory location-create name:"Main Stash" max_gear_slots:20
/inventory add location:"Main Stash" item:"Lantern" quantity:2 notes:"from the goblin cave"
/inventory list location:"Main Stash"
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
| `/coffers show` | Show the current balance. | `/coffers show` |
| `/coffers add gp? sp? cp? reason?` | Add funds. At least one of gp/sp/cp must be a positive value. | `/coffers add gp:50 sp:5 reason:"dungeon loot"` |
| `/coffers subtract gp? sp? cp? reason?` | Subtract funds. Refuses if the result would be negative (reports the shortfall). | `/coffers subtract gp:2 reason:"paying innkeeper"` |
| `/coffers buy item` | Subtract a catalog item's value from coffers. Item must exist and have a value set. Refuses if you can't afford it. Doesn't add anything to inventory — that's a separate command. | `/coffers buy item:"Wand of Sparks"` |

**Tip:** `/coffers buy` autocomplete only shows catalog items that have a value set, so you can't accidentally pick something with no price.

**Common flow** — splitting loot after a session:
```
/coffers add gp:120 reason:"dungeon haul"
/coffers buy item:"Healing Potion"
/coffers show
```

---

## `/treasury` — Magical Item Borrow Tracking

For guild-owned magical items. They are **never given away** — they're borrowed and returned. Each physical item is its own row, so the guild can own two "Wand of Sparks" instances and track each independently. There's a single, auto-managed treasury (no location concept exposed); items are added to it and borrowed/returned from it.

| Command | What it does | Example |
|---|---|---|
| `/treasury add item tag?` | Register a magical item instance. The catalog entry must have `type:magical`. Use `tag` to distinguish duplicates ("chipped", etc.). Duplicates allowed. | `/treasury add item:"Wand of Sparks" tag:"chipped"` |
| `/treasury remove entry_id` | Remove an instance. Must be currently available (not borrowed). Use the autocomplete to pick by name; the integer ID is sent automatically. | `/treasury remove entry_id:#7` |
| `/treasury list status?` | List all treasury items, grouped by Available / Borrowed. Header shows totals (count + gear slots, informational only). `status:available` or `status:borrowed` filters to one group. | `/treasury list status:available` |
| `/treasury borrow entry_id borrower? notes?` | Check out an instance. `borrower` defaults to you, or pick another member from the dropdown to borrow on their behalf — they'll get pinged. Errors if already borrowed. | `/treasury borrow entry_id:#7 borrower:@Tessa notes:"for downtime crafting"` |
| `/treasury return entry_id notes?` | Return an item. Anyone can return on the borrower's behalf. | `/treasury return entry_id:#7` |
| `/treasury who-has item` | For a magical catalog item, show every instance and its current borrower. | `/treasury who-has item:"Wand of Sparks"` |

**Tip:** The `entry_id` parameter uses autocomplete that shows item name + tag + (for return) current borrower. You don't have to type or remember numbers — Discord sends the underlying integer for you.

**Common flow** — borrowing for an adventure:
```
/treasury list status:available
/treasury borrow entry_id:#7 notes:"taking on the dungeon delve"
... session ends ...
/treasury return entry_id:#7
```

**Common flow** — borrowing on behalf of someone else:
```
/treasury borrow entry_id:#7 borrower:@Tessa notes:"she's offline, prepping crafting"
→ @Tessa gets pinged in the channel
```

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
- Changing a catalog item's `type` into or out of `magical` is blocked while it's referenced by inventory or treasury entries. Switching between any of the non-magical types (common/crafted/scroll/potion) is always allowed.
- Renaming an item via `/items edit new_name:…` is rejected if another item already has that name.

Failed operations reply ephemerally (visible only to you) — no silent failures, no stack traces.
