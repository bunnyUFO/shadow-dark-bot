# Permissions & Audit Log

## Current state: no role-based permissions

For now, the bot is **fully open** to all guild members. There is no GM role, no admin role, no permission decorator. Anyone in the Discord server can run any command — add, edit, delete, take, borrow, return.

This is a deliberate simplification for the first slice:
- One Discord server, presumed to be a trusted friend group.
- Built-in **invariants** (see `data-model.md`) prevent most accidental breakage — you can't delete a referenced catalog item, double-borrow a magical instance, exceed a location's capacity, or leave inventory in an inconsistent state.
- The **`borrows` table** records every treasury checkout (who, when, what notes) — that's the highest-value audit trail and is fully wired up.

If griefing or accidents become a problem in practice, see the roadmap for the role-based permissions design — it can be layered on without a schema change.

## What still gets enforced

| Concern | Mechanism |
|---|---|
| Wrong category (magical item in non-magical stash) | Invariant check in domain code; ephemeral error reply |
| Double-borrow | Invariant check before opening a new `borrows` row |
| Over-take from a stack | Invariant check before decrement |
| Orphaning a referenced catalog row | Blocked at `/items delete` |
| Deleting a non-empty location | Blocked at `/inventory location-delete` and `/treasury` equivalents |
| Guild membership | Slash commands are guild-scoped, so non-members of the server can't see them at all |

## Audit log

**Current state**: the `audit_log` table exists in the schema but **no code writes to it yet**. The decision to defer was deliberate — the `borrows` ledger covers the highest-value question (who has each magical item, when did they borrow it, when did they return it), and the rest of the operations are low-volume / low-stakes for a trusted group.

When we wire it up later (alongside role-based permissions or a `/audit` command), every write command will insert an `audit_log` row inside the same transaction as the change, so it can never desync. Planned action codes:

| Code | Source command |
|---|---|
| `item.create` | `/items add` |
| `item.edit` | `/items edit` |
| `item.delete` | `/items remove` |
| `location.create` | `/inventory location-create` |
| `location.edit` | `/inventory location-edit` |
| `location.delete` | `/inventory location-delete` |
| `inventory.add` | `/inventory add` |
| `inventory.take` | `/inventory take` |
| `treasury.add` | `/treasury add` |
| `treasury.remove` | `/treasury remove` |
| `treasury.borrow` | `/treasury borrow` |
| `treasury.return` | `/treasury return` |

`payload_json` will store a small JSON snapshot — for `inventory.take` something like `{"item": "Rope, 50ft", "location": "Main Stash", "quantity": 1, "remaining": 2}`. A future `/audit` command can render these without joining other tables.

## What would NOT be logged (even when wired up)

- Read commands (`info`, `list`, `who-has`).
- Autocomplete suggestions.
- Discord-level events (member joins, etc.) — out of scope.

## When role-based permissions become worth it

Add them when one of these is true:
1. Someone has accidentally (or maliciously) deleted shared data and the audit log can't undo it in practice.
2. The guild grows beyond people who all trust each other directly.
3. You want to delegate specific actions (e.g., "treasurer" role for coffers when those land).

The roadmap entry sketches the change: add a `guild_settings` row holding a list of admin/GM role IDs, add a small `@requires_role` decorator on the destructive commands, and add a `/setup-roles` command (with Discord's native role-picker UI) to configure them. Schema-compatible with the current design — no migrations of existing tables.
