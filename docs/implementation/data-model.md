# Data Model

SQLite via SQLAlchemy 2.x. Five tables for items/inventory/treasury, a `coffers` singleton for shared funds, plus one cross-cutting `audit_log` (schema-only for now; writes not implemented).

## ER overview

```
items ──────────┬─< inventory_entries >── locations
                │                              ▲
                └─< treasury_entries ──────────┘
                       │
                       └─< borrows
```

- `items` is the **catalog** — the abstract thing.
- `locations` is a named storage place (works for both kinds).
- `inventory_entries` are **stacks** of non-magical items at a location.
- `treasury_entries` are **individual magical instances** at a location.
- `borrows` is the borrow history for treasury entries; the currently-open row (one per entry) doubles as the "who has it now" record.
- `audit_log` records every write action.

## Tables

### `items` (catalog)
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT UNIQUE NOT NULL | Used in commands and autocomplete; can be renamed via `/items edit new_name:…` |
| `description` | TEXT NULL | |
| `gear_slots` | REAL NOT NULL DEFAULT 0 | Slot cost per *bundle*. Shadow Dark uses fractional slots in some homebrews. Default 0 means weightless. |
| `bundle_size` | INTEGER NOT NULL DEFAULT 1 | `CHECK (bundle_size >= 1)`. How many items fit in one gear-slot allocation. Arrows: 20. Stack slot cost is `ceil(quantity / bundle_size) * gear_slots`. |
| `value_cp` | INTEGER NULL | Total value in copper pieces (1 gp = 100 cp). NULL = no value tracked. |
| `item_type` | TEXT NOT NULL DEFAULT `'common'` | `CHECK (item_type IN ('common', 'magical', 'crafted', 'scroll', 'potion'))`. Only `magical` items go in treasury; the other four all live in inventory. |
| `created_by` | TEXT | Discord user ID of creator |
| `created_at` | TIMESTAMP | |

### `locations`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT UNIQUE NOT NULL | "Main Stash", "Magical Vault" |
| `kind` | TEXT NOT NULL | `'inventory'` or `'treasury'` |
| `description` | TEXT NULL | |
| `max_gear_slots` | REAL NOT NULL DEFAULT 0 | Inventory capacity. Sum of `entry.quantity × item.gear_slots` cannot exceed this. Default 0 = no capacity yet (set via `/inventory location-edit`). |
| `created_at` | TIMESTAMP | |

### `inventory_entries` (non-magical stacks)
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `location_id` | INTEGER FK locations | |
| `item_id` | INTEGER FK items | |
| `quantity` | INTEGER NOT NULL | `CHECK (quantity > 0)` — zero rows are deleted |
| `notes` | TEXT NULL | |
| `added_by` | TEXT | Discord user ID |
| `added_at` | TIMESTAMP | |
| | | `UNIQUE (location_id, item_id)` — one stack per item per location |

### `treasury_entries` (magical instances)
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | The `entry_id` users pass to `/treasury borrow` |
| `location_id` | INTEGER FK locations | Must be a `kind='treasury'` location |
| `item_id` | INTEGER FK items | Item must have `item_type='magical'` |
| `tag` | TEXT NULL | Optional, distinguishes duplicates ("chipped", "lefthand") |
| `status` | TEXT NOT NULL | `'available'` or `'borrowed'` |
| `added_by` | TEXT | |
| `added_at` | TIMESTAMP | |

### `borrows`
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `treasury_entry_id` | INTEGER FK treasury_entries | |
| `borrower_id` | TEXT NOT NULL | Discord user ID |
| `borrowed_at` | TIMESTAMP NOT NULL | |
| `returned_at` | TIMESTAMP NULL | Open borrow while NULL |
| `notes` | TEXT NULL | |

### `coffers` (singleton — shared guild balance)
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | Always 1 (singleton; auto-created on first `/coffers` command) |
| `balance_cp` | INTEGER NOT NULL DEFAULT 0 | Total balance in copper pieces. Renders normalized (1gp 5sp) via `currency.format_cp`. |

### `audit_log` (schema reserved; not yet written to)
| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `actor_id` | TEXT NOT NULL | Discord user ID |
| `action` | TEXT NOT NULL | e.g. `'item.create'`, `'inventory.take'`, `'treasury.borrow'` |
| `target_kind` | TEXT NULL | `'item'`, `'location'`, `'inventory_entry'`, `'treasury_entry'` |
| `target_id` | INTEGER NULL | |
| `payload_json` | TEXT NULL | Free-form JSON snapshot of the change |
| `created_at` | TIMESTAMP NOT NULL | |

> Note: this table exists in the schema but **no code currently writes to it**. Wiring it up is roadmapped alongside role-based permissions or an `/audit` command. The `borrows` table already captures the highest-value audit information (who has what magical item, since when).

## Invariants (enforced in domain code, not just SQL)

1. **Magical sorting**: `inventory_entries.item_id` must reference an item with `item_type != 'magical'`. `treasury_entries.item_id` must reference `item_type = 'magical'`. Attempting the wrong combination produces an ephemeral error. (`crafted`, `scroll`, and `potion` all route to inventory just like `common`.)
2. **One open borrow per instance**: a `treasury_entries` row with `status='borrowed'` has exactly one `borrows` row where `returned_at IS NULL`. `/treasury borrow` against a borrowed entry errors. `/treasury return` requires that open row.
3. **Quantity floor**: `/inventory take` cannot take more than the current stack; doing so errors. Decrementing to zero deletes the row.
4. **Location kind matches**: an `inventory` command refuses a `treasury` location, and vice versa.
5. **No orphaned catalog deletes**: `/items remove` is blocked if any `inventory_entries` or `treasury_entries` rows reference the item. Removing all references first is required.
6. **Empty-location delete**: `/inventory location-delete` and `/treasury` equivalents require an empty location.
7. **Inventory capacity (bundle-aware)**: each stack's slot cost is `ceil(entry.quantity / item.bundle_size) * item.gear_slots`. On `/inventory add`, the delta between the existing stack's cost and the post-add stack's cost is checked against remaining capacity — so adding more items to a partially-filled bundle may consume 0 extra slots.
8. **No shrinking below use**: `/inventory location-edit max_gear_slots:N` is blocked if `N` is less than the current sum of used slots at that location.
9. **No-collision rename**: `/items edit new_name:…` is blocked if another row already has that name.
10. **Type change with references**: `/items edit type:…` is blocked when switching *into* magical with inventory stacks present, or *out of* magical with treasury instances present. Switching between any non-magical types is always allowed.

## Why one row per magical instance (instead of quantity)?

Magical items in Shadow Dark are often unique or near-unique. Treating each as a separate row lets us:
- Track a specific borrow against a specific instance.
- Allow optional `tag` text to distinguish duplicates ("the chipped wand vs. the new one").
- Avoid an awkward "borrow 1 of 3" model where it's unclear which copy is out.

## Migrations

Alembic. All migrations apply automatically at bot startup (before connecting to Discord). The current chain:

| Revision | Purpose |
|---|---|
| `0001_initial` | Creates all six tables with the original schema |
| `0002_value_in_copper` | Switches `items.value_gp REAL` → `items.value_cp INTEGER` (integer copper; avoids float drift) |
| `0003_inventory_capacity` | `items.gear_slots` becomes NOT NULL default 0; adds `locations.max_gear_slots` NOT NULL default 0 |
| `0004_coffers` | Adds the `coffers` singleton table (balance in integer copper) |
| `0005_bundle_size` | Adds `items.bundle_size` INTEGER NOT NULL DEFAULT 1 with `CHECK (bundle_size >= 1)`. Inventory capacity math becomes bundle-aware (ceiling per stack). |
| `0006_item_type` | Replaces `items.is_magical BOOLEAN` with `items.item_type TEXT` ∈ {`common`, `magical`, `crafted`, `scroll`, `potion`}. Backfill: `is_magical=1 → 'magical'`, else `'common'`. |

Future slices (e.g., role-based permissions, audit-log writes) will add new revisions. Each one uses `op.batch_alter_table` so SQLite can recreate tables transparently when needed.

## Treasury location singleton

The treasury cog auto-creates a single `locations` row with `name='Treasury'`, `kind='treasury'` on the first `/treasury add`. No user-facing location commands for treasury — keeps the UX focused on items and borrows. The `_get_or_create_treasury_location` helper is idempotent and lives in `cogs/magical_treasury.py`.

## Coffer singleton

The coffers cog auto-creates a single `coffers` row with `balance_cp=0` on the first `/coffers` command. The table is intentionally singleton — we never use `id` for anything other than primary key uniqueness. The `_get_or_create_coffer` helper is idempotent and lives in `cogs/guild_coffers.py`. All balance arithmetic happens in integer copper; the `currency.format_cp` helper renders normalized output (`1gp 5sp`).

## Sample queries

**Available magical items in a location:**
```sql
SELECT te.id, i.name, te.tag
FROM treasury_entries te JOIN items i ON i.id = te.item_id
WHERE te.location_id = ? AND te.status = 'available'
ORDER BY i.name;
```

**Who currently has each instance of a magical item:**
```sql
SELECT te.id, te.tag, b.borrower_id, b.borrowed_at
FROM treasury_entries te
JOIN items i ON i.id = te.item_id
LEFT JOIN borrows b ON b.treasury_entry_id = te.id AND b.returned_at IS NULL
WHERE i.name = ?;
```

**Total quantity of a non-magical item across all locations:**
```sql
SELECT COALESCE(SUM(quantity), 0)
FROM inventory_entries ie JOIN items i ON i.id = ie.item_id
WHERE i.name = ?;
```
