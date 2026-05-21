# Roadmap

## Done

| Slice | What landed |
|---|---|
| **M0 — Skeleton** | discord.py bot bootstrap, single-guild auto-discovery, `/ping` smoke test |
| **M1 — Items catalog** | SQLite + Alembic, six-table schema, `/items` group, currency in integer copper |
| **M2 — Inventory** | `/inventory` group with multiple named locations and per-location gear-slot capacity |
| **M3 — Treasury** | `/treasury` group with one-row-per-instance borrow tracking and member-picker `borrower` parameter |
| **M4 — Coffers** | `/coffers show`, `add`, `subtract`, `buy <item>` — singleton balance in integer copper, normalized display |

Below are the next slices in rough order of priority. Each should be its own focused change set — don't bundle.

## After that

| Feature | Sketch |
|---|---|
| **Audit log writes** | Schema (`audit_log` table) is in place; no code writes to it yet. Add a small `record(session, actor, action, target_kind, target_id, payload)` helper called from each write command. Same transaction as the change, so it can't desync. Useful as a backstop for griefing investigations even before a `/audit` command exists. |
| **Role-based permissions** | Add a `guild_settings` table holding admin/GM role IDs, a `@requires_role` decorator on destructive commands, and a `/setup-roles` slash command (uses Discord's native role-picker UI — no manual ID copying). Layers on without migrating existing tables. Add when accidents/griefing become real, not before. |
| **`/audit` command** | Show recent audit-log rows, filterable by action or actor. Needs audit-log writes wired up first. |
| **Channel routing** | Have the bot post `/inventory`/`/treasury` results to a configured `#guild-stash` or `#magical-treasury` channel and reply ephemerally to the invoker. Needs a `bot_settings` table + an admin `/setup` command with Discord's channel-picker. |
| **Per-character carry** | Optional: who's carrying what right now (vs. what's in the stash). Adds a `characters` table keyed by Discord user, and a `held_by_character` link. |
| **Downtime activities** | Tracking crafting, training, etc. — separate cog, separate schema. |
| **Dice rolling** | A `/roll 1d20+3` command. Trivial to add (`d20` library or hand-rolled parser). |
| **Session log / shared notes** | `/note add`, `/note list` — bot-tracked session notes pinned in a channel. |
| **Multi-guild** | The current single-guild assumption is enforced in `on_ready` / `on_guild_join`. Lifting it would require adding `guild_id` to every table and scoping queries by `interaction.guild_id`. Larger change; only worth it if the bot ever serves more than your group. |
| **Backups to off-LXC storage** | Restic or rclone the `/backups` directory to a NAS or Backblaze B2. |
| **Web view** | Read-only Flask/FastAPI page showing the catalog and inventory. Lowest priority — Discord is the UX. |

## Non-goals (probably forever)

- Character sheets (use a dedicated VTT/sheet tool).
- Combat tracking / initiative.
- Live VTT integration.
- Anything requiring scraping or violating Discord ToS.

## How to decide what's next

After this slice is in daily use for a few sessions, the right next slice will be obvious from what's annoying. Don't pre-build. Coffers is the most likely first follow-up because the user explicitly asked for it.
