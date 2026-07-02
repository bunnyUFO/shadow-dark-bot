# Roadmap

## Done

| Slice | What landed |
|---|---|
| **M0 — Skeleton** | discord.py bot bootstrap, single-guild auto-discovery, `/ping` smoke test |
| **M1 — Items catalog** | SQLite + Alembic, six-table schema, `/items` group, currency in integer copper |
| **M2 — Inventory** | `/inventory` group with multiple named locations and per-location gear-slot capacity |
| **M3 — Treasury** | `/treasury` group with one-row-per-instance borrow tracking and member-picker `borrower` parameter |
| **M4 — Coffers** | `/coffers show`, `add`, `subtract`, `buy <item>` — singleton balance in integer copper, normalized display |
| **M5 — Proxmox deploy** | `scripts/install-proxmox.sh` (one-line install), `scripts/update-bot.sh`, self-healing Docker entrypoint, full deploy walkthrough in `deploy-proxmox.md` |
| **M6 — Multi-guild command sync** | Removed the single-guild lock; commands now sync to every guild the bot is in on connect and on join. Data is still shared across guilds (see Multi-tenant data, below) |
| **M7 — Item polish** | `bundle_size` for stack-per-slot items (arrows = 20/slot) with bundle-aware capacity math; rename via `/items edit new_name`; `item_type` enum with eight values (`common`/`weapon`/`armor`/`scroll`/`potion`/`loot`/`crafted`/`magical`) replacing the `is_magical` bool, with type-aware color coding and routing |
| **M8 — Player characters** | `/character` group: one character per player with lean stats (ancestry/class/alignment/level/max HP/AC, six abilities + derived modifier table, gold, known languages, talents, optional spellcasting modifier), a hybrid catalog/freeform carried inventory with `max(10, STR)` carry capacity, and a **known-spells** list backed by a seeded Shadow Dark spell reference (all Tier 1–5 wizard/priest plus 48 alignment-gated wizard spells). Spells are managed through a dedicated class-limited `/character` Manage Spells flow (tier selector → Learn/Forget on spell detail, any tier, no custom spells), with a separate read-only `/spells browse` reference. Ephemeral owner-only interactive sheet (edit modals + Manage Inventory + Manage Spells), plus read-only `/character show`. Overrides the earlier "character sheets" non-goal. |

Below are the next slices in rough order of priority. Each should be its own focused change set — don't bundle.

## After that

| Feature | Sketch |
|---|---|
| **Audit log writes** | Schema (`audit_log` table) is in place; no code writes to it yet. Add a small `record(session, actor, action, target_kind, target_id, payload)` helper called from each write command. Same transaction as the change, so it can't desync. Useful as a backstop for griefing investigations even before a `/audit` command exists. |
| **Role-based permissions** | Add a `guild_settings` table holding admin/GM role IDs, a `@requires_role` decorator on destructive commands, and a `/setup-roles` slash command (uses Discord's native role-picker UI — no manual ID copying). Layers on without migrating existing tables. Add when accidents/griefing become real, not before. |
| **`/audit` command** | Show recent audit-log rows, filterable by action or actor. Needs audit-log writes wired up first. |
| **Channel routing** | Have the bot post `/inventory`/`/treasury` results to a configured `#guild-stash` or `#magical-treasury` channel and reply ephemerally to the invoker. Needs a `bot_settings` table + an admin `/setup` command with Discord's channel-picker. |
| **Per-character carry (guild-stash link)** | Partly landed in M8: characters now carry their own `character_items`. What's left is linking that to the *shared stash* — e.g. moving a stack from `/inventory` to a character (and back) instead of adding it freehand — so the guild can see who pulled what from the stash. |
| **Downtime activities** | Tracking crafting, training, etc. — separate cog, separate schema. |
| **Dice rolling** | A `/roll 1d20+3` command. Trivial to add (`d20` library or hand-rolled parser). |
| **Session log / shared notes** | `/note add`, `/note list` — bot-tracked session notes pinned in a channel. |
| **Multi-tenant data (per-guild isolation)** | Commands already sync to every guild (M6), but the underlying database is single-tenant — all servers see the same catalog/inventory/treasury/coffers. Real isolation needs a `guild_id` column on every domain table, queries scoped by `interaction.guild_id`, and a backfill migration that stamps existing rows with the original guild's ID. Worth doing the moment a second server actually wants its own data. |
| **Backups to off-LXC storage** | Restic or rclone the `/backups` directory to a NAS or Backblaze B2. |
| **Web view** | Read-only Flask/FastAPI page showing the catalog and inventory. Lowest priority — Discord is the UX. |

## Non-goals (probably forever)

- Combat tracking / initiative.
- Live VTT integration.
- Anything requiring scraping or violating Discord ToS.

> Note: "character sheets" was previously a non-goal. It shipped as a deliberately **lean** slice in M8 (`/character`) — core stats + carried inventory, not a full VTT sheet. Deep sheet features (spell lists, level-up automation, initiative) remain out of scope.

## How to decide what's next

After this slice is in daily use for a few sessions, the right next slice will be obvious from what's annoying. Don't pre-build. Coffers is the most likely first follow-up because the user explicitly asked for it.
