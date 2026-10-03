# Architecture

## Goals

- Solve real bookkeeping pain for the guild: catalog items, track shared stashes, enforce magical-item borrow discipline.
- Stay small and easy to maintain — this is a hobby project for one guild.
- Persistent, auditable, recoverable from a single file.

## Layered design

```
┌──────────────────────────────────────────────┐
│ Discord slash commands (cogs/*.py)           │  ← presentation + arg parsing
├──────────────────────────────────────────────┤
│ Domain services (services/*.py)              │  ← business invariants live here
├──────────────────────────────────────────────┤
│ SQLAlchemy session + SQLite (db.py)          │  ← persistence
└──────────────────────────────────────────────┘
```

This is the target shape. `/character` follows it: the cog parses interaction input, opens a session, calls `services/characters.py`, and formats the reply; the service enforces the invariants and raises `CharacterError` with a user-facing reason. Services never import discord.py, so they're unit-tested directly and will be shared with the planned web API (see [discord-activity.md](discord-activity.md)). The guild cogs (`/items`, `/inventory`, `/treasury`, `/coffers`) still keep their invariants inline — see below.

## Tech stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Mature Discord library, easy to read |
| Discord library | discord.py 2.x | Native slash-command and autocomplete support |
| ORM | SQLAlchemy 2.x (sync) | Battle-tested, type-friendly, plays well with Alembic |
| Migrations | Alembic | Autogenerate from model changes, applies at container start |
| DB | SQLite | Single file, no server, trivial backups; we'll never outscale it |
| Config | pydantic-settings | Typed `.env` loading with validation |
| Tests | pytest with in-memory SQLite | Fast, isolated, no Discord client needed |
| Lint/format | ruff | One tool, fast |

## Why not…

- **Postgres** — overkill. Our entire dataset will fit in single-digit MB. SQLite gives us file-level backups and zero ops.
- **Async SQLAlchemy** — discord.py is async, but our DB calls are short and rare; sync sessions are simpler and the gateway loop won't notice.
- **TypeScript** — fine alternative, but Python is easier for one-person maintenance of a small bot.
- **Global slash commands** — they propagate slowly (~1 hour). Instead the bot syncs guild-scoped commands to every guild it's in on connect (and on `on_guild_join` for new joins), which deploys instantly.

## Runtime topology

```
Discord ─── gateway ─── shadowdark_bot (Python, single process)
                         ├── local web app on :8081 (optional, LOCAL_WEB_ENABLED=true)
                         ├── public web app on :8080 (optional, WEB_ENABLED=true)
                         └── /data/shadowdark.db (SQLite)
```

Everything in one Docker container in one LXC on Proxmox. No external services, no queue, no cache. The optional web app (character sheet / Discord Activity) is served by uvicorn on the bot's own event loop rather than a second process, so there is still one SQLite writer — see [discord-activity.md](discord-activity.md).

## Code layout

```
src/shadowdark_bot/
  main.py           # bot bootstrap: run migrations, load cogs, sync commands, run
  config.py         # pydantic-settings: DISCORD_TOKEN, DATABASE_URL, WEB_* / OAuth settings
  db.py             # engine, session_scope context manager, SQLite WAL/FK pragmas
  models.py         # SQLAlchemy ORM classes (Item, Location, InventoryEntry, TreasuryEntry, Borrow, AuditLog, Coffer)
  currency.py       # gp/sp/cp ↔ copper helpers (parse_to_cp, format_cp)
  embeds.py         # build_item_embed, build_location_*_embed, build_treasury_*_embed, build_coffer_*_embed, time helpers
  rules.py          # pure Shadow Dark rules: modifiers, carry capacity, bundle-aware slot cost
  storage.py        # character held/stash location helpers shared by the cogs
  services/
    characters.py   # character-sheet operations: identity/stats edits, carry/give/remove, spells, delete
  web/
    app.py          # FastAPI routes: login exchange, sheet + reference endpoints
    auth.py         # Discord OAuth code exchange, signed session tokens
    schemas.py      # JSON response shapes built from the ORM rows
    server.py       # runs the local (no-login) and public apps on the bot's event loop
    static/         # frontend: index.html + assets/*.js|css, no build step
  cogs/
    items_database.py    # /items add, info, edit, remove, browse
    guild_inventory.py   # /inventory location-create/edit/delete, add, browse
    magical_treasury.py  # /treasury add, remove, browse
    guild_coffers.py     # /coffers add, subtract, browse
    player_characters.py # /character sheet, carry, show — Discord UI over services/characters.py
    spell_reference.py   # /spells info, browse
```

The guild cogs still implement both the slash-command surface and the invariants (capacity checks, magical-vs-non-magical sorting, borrow-state guards). They move into `services/` only when something besides Discord needs them — `/character` moved first because the web app needs it.

## Key cross-cutting concerns

**Audit log table.** The `audit_log` schema exists for future use. **No writes happen yet** — wiring it in is deferred until role-based permissions or an `/audit` command lands. The `borrows` table already serves as the audit trail for the most write-heavy area (treasury borrow/return).

**Autocomplete.** discord.py 2.x supports per-parameter autocomplete callbacks. Item names, location names, and treasury entry IDs all use these — typing `/inventory add item:rop` suggests "Rope, 50ft"; `/treasury remove entry_id:` shows only available instances with their tags. Implemented with simple `LIKE` queries against SQLite (fast for our scale, no FTS needed).

**Errors as ephemeral replies.** Invariant violations become user-visible ephemeral messages with a `**Failed to <action>.**` header followed by the reason. Never silent failures or stack traces.

**Transactions.** Each command opens one `session_scope()` context manager that commits on success and rolls back on exception. Multi-row writes (borrow: set status + insert ledger row; return: set status + close ledger row) happen atomically.

**SQLite pragmas.** `db.py` enables `foreign_keys=ON` and `journal_mode=WAL` on every connection. FK enforcement catches orphan-reference bugs early; WAL allows safe reads during writes.

## Scope assumptions and what this design does NOT do

- **Commands sync to every guild; data is shared across them.** `main.py` calls `tree.sync(guild=…)` for every guild on connect and on join, so the bot works in multiple servers. But there are no `guild_id` columns anywhere — every row implicitly belongs to "the database." If the bot is in your server and a friend's, both servers see and edit the same catalog/inventory/treasury/coffers. Real multi-tenancy (per-guild data isolation) is tracked in the roadmap as a future slice.
- **No role-based permissions for now.** Every guild member can run every command. Invariants in cog code prevent inconsistent state. The `borrows` table records every treasury checkout; `audit_log` schema exists for future use. See `permissions.md` for when to add roles.
- **Discord identity only.** No external user accounts; Discord user IDs are the only identity.
- **Single treasury location**, auto-created on first use. The schema supports multiple but the cog doesn't expose them — keeps the UX simple.
- **Single coffer balance**, auto-created on first `/coffers` command. Stored as integer copper to avoid float drift.
- **No treasury capacity enforcement.** Inventory locations have a `max_gear_slots` cap; treasury locations have the column too but it's ignored.
- **No transaction history for coffers yet.** Add/subtract/buy show the reason in chat but don't persist it. Will land alongside audit-log writes.
- No web dashboard, no HTTP API.
- No character sheets, no dice rolling.
