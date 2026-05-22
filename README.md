# shadow-dark-bot

A Discord bot for Shadow Dark TTRPG guilds. Tracks the guild's item catalog, shared non-magical stashes, and magical-item borrow/return.

## Status

Feature-complete for the first slice. Four command groups working end-to-end:

- **`/items`** — the catalog of every item the guild has discovered (add, info, edit, remove, list). Values in gp/sp/cp, gear slots, **bundle size** (e.g., 20 arrows per slot), and an **item type** picker with five options: `common`, `magical`, `crafted`, `scroll`, `potion`. Color-coded embeds by type. Items can be renamed via `/items edit new_name:…`.
- **`/inventory`** — shared stashes for everything except magical items (so common / crafted / scroll / potion all go here). Multiple named locations with per-location gear-slot capacity. Capacity math is bundle-aware: adding 5 arrows then 15 more uses 1 slot total, not 2. Add, take, list, location CRUD.
- **`/treasury`** — magical-item borrow/return tracking. Each physical instance has its own row; same catalog item can be checked out by multiple users at once. Borrow on your own behalf or pick a member; everything's logged in the `borrows` ledger.
- **`/coffers`** — shared guild funds in gp/sp/cp. Add, subtract, and `buy` a catalog item by name (auto-subtracts its value from the balance). Single shared balance, stored internally as integer copper.

The bot can be installed in multiple Discord servers; commands sync to every server it joins. **Data is currently shared across all servers** — see the roadmap for the multi-tenant slice that gives each server its own catalog/inventory/treasury/coffers.

See the [roadmap](docs/implementation/roadmap.md) for what's planned next.

## Project Structure

```
shadow-dark-bot/
│
├── README.md                     ← you are here
├── pyproject.toml                ← Python's "package.json": declares deps + project metadata
├── .env.example                  ← template for secrets (copy to .env, fill in DISCORD_TOKEN)
├── .env                          ← real secrets — gitignored, you create this locally
├── .gitignore                    ← files git should never track (.env, .venv, data/, …)
│
├── Dockerfile                    ← recipe for building a container image of the bot
├── docker-entrypoint.sh          ← runs at container start: chowns /app/data, then drops to bot user via gosu
├── docker-compose.yml            ← runs the container with volumes + env vars wired up
│
├── scripts/                      ← one-shot helpers for Proxmox deploy
│   ├── install-proxmox.sh        ← host-side LXC installer (curl-pipe-bash from README)
│   └── update-bot.sh             ← host-side `git pull && docker compose up -d --build` wrapper
│
├── alembic.ini                   ← config for Alembic, the database-migrations tool
├── migrations/                   ← every schema change ever, in version-controlled order
│   ├── env.py                    ← migration runner: loads our models + DB URL
│   ├── script.py.mako            ← template Alembic uses when generating new migrations
│   └── versions/
│       ├── 0001_initial.py             ← creates the six tables
│       ├── 0002_value_in_copper.py     ← value_gp (float) → value_cp (integer copper)
│       ├── 0003_inventory_capacity.py  ← gear_slots NOT NULL; adds max_gear_slots
│       ├── 0004_coffers.py             ← adds the singleton coffers table
│       ├── 0005_bundle_size.py         ← adds items.bundle_size (default 1) for stack-per-slot items
│       └── 0006_item_type.py           ← replaces is_magical bool with item_type enum (common/magical/crafted/scroll/potion)
│
├── data/                         ← runtime data — gitignored
│   └── shadowdark.db             ← the SQLite database file (auto-created on first run)
│
├── src/                          ← all Python source code lives under here
│   └── shadowdark_bot/           ← the actual Python "package" (the bot)
│       ├── __init__.py           ← marker file that makes this folder a Python package
│       ├── main.py               ← entry point: `python -m shadowdark_bot.main`
│       ├── config.py             ← reads .env into typed settings (DISCORD_TOKEN, DATABASE_URL)
│       ├── db.py                 ← SQLAlchemy engine + session helper for talking to SQLite
│       ├── models.py             ← the ORM models — Python classes mirroring DB tables
│       ├── currency.py           ← gp/sp/cp ↔ copper conversion + formatting helpers
│       ├── embeds.py             ← functions that build the pretty Discord embed cards
│       └── cogs/                 ← "cog" = discord.py term for a group of related commands
│           ├── __init__.py
│           ├── items_database.py     ← /items add, info, edit, remove, list
│           ├── guild_inventory.py    ← /inventory location-create/edit/delete, add, take, list
│           ├── magical_treasury.py   ← /treasury add, remove, list, borrow, return, who-has
│           └── guild_coffers.py      ← /coffers show, add, subtract, buy
│
└── docs/                         ← human-readable documentation (not loaded at runtime)
    ├── commands.md               ← every slash command, what it does, examples
    ├── setup-discord.md          ← how to create the Discord app and invite the bot
    ├── deploy-proxmox.md         ← LXC + Docker walkthrough for production
    └── implementation/
        ├── architecture.md       ← high-level: which file does what, why these libraries
        ├── data-model.md         ← the database schema, table by table, with invariants
        ├── permissions.md        ← who can run what (currently: everyone; roles are future work)
        └── roadmap.md            ← what's planned next (audit log, role-based perms, channel routing, etc.)
```

### Where to look when you want to…

| Goal | File(s) |
|---|---|
| **Add a new slash command** | A cog under `src/shadowdark_bot/cogs/`. Use `items_database.py` as a template. |
| **Change how an embed looks** | `src/shadowdark_bot/embeds.py` |
| **Add a database column** | Edit `src/shadowdark_bot/models.py`, then create a new migration in `migrations/versions/` |
| **Change a setting** | `.env` (your local copy; never commit secrets) |
| **Update install dependencies** | `pyproject.toml`, then re-run `pip install -e .` |
| **Read what a command does** | `docs/commands.md` |
| **Run the bot locally** | See "Quick start (local dev)" below |
| **Deploy to Proxmox** | See "Deploy on Proxmox" below + `docs/deploy-proxmox.md` for full details |
| **Update a deployed bot** | `bash <(curl -fsSL …/scripts/update-bot.sh)` on the Proxmox host |

### Background concepts (if you're newer to Python/Discord)

- **Slash command** — the `/items add` style commands you see in Discord. They're registered with Discord when the bot connects; the bot receives the user's input, runs code, replies with text or an "embed" (a pretty card).
- **`discord.py`** — the Python library that handles the Discord connection. We use version 2.x.
- **Cog** — a `discord.py` convention for grouping related commands into a class. Each command group (`/items`, `/inventory`) gets its own cog file.
- **SQLAlchemy** — Python's most popular library for talking to databases. We use the ORM (Object-Relational Mapper) style: each table is a Python class (`Item`, `Location`, …) and rows behave like Python objects.
- **Alembic** — companion to SQLAlchemy that handles schema migrations. Each change to the database is a numbered file in `migrations/versions/`; they apply in order at bot startup.
- **SQLite** — a file-based database. The entire database is one `.db` file in `data/`. No separate server to run.
- **Virtual environment (`.venv/`)** — an isolated Python install just for this project, so its dependencies don't conflict with anything else on your system.
- **`pip install -e .`** — installs the project "in editable mode": code edits take effect immediately on the next run, no reinstall needed.

## Quick start (local dev on Windows)

1. Install Python 3.12 if you don't have it.
2. Create a Discord application and get a bot token — see [docs/setup-discord.md](docs/setup-discord.md).
3. In Git Bash from the repo root:
   ```bash
   py -3.12 -m venv .venv
   source .venv/Scripts/activate
   pip install -e .
   cp .env.example .env
   # edit .env to set DISCORD_TOKEN
   python -m shadowdark_bot.main
   ```
4. In Discord, run `/ping` — you should get back `pong`.

## Deploy on Proxmox (one-line install)

On your Proxmox host as `root`:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/bunnyUFO/shadow-dark-bot/main/scripts/install-proxmox.sh)
```

Creates an unprivileged Debian 12 LXC, installs Docker inside, clones this repo, writes `.env` with the bot token (prompted securely), and starts the bot. Default specs: 1 vCPU / 512 MB RAM / 4 GB disk / DHCP on `vmbr0`; everything overridable via env vars. Container auto-starts on Proxmox boot.

Update later with:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/bunnyUFO/shadow-dark-bot/main/scripts/update-bot.sh)
```

Full walkthrough (overrides, backups, restore, troubleshooting): [docs/deploy-proxmox.md](docs/deploy-proxmox.md).

## Docs

User-facing:
- [Commands reference](docs/commands.md)
- [Discord application setup](docs/setup-discord.md)
- [Deploy on Proxmox](docs/deploy-proxmox.md)

Implementation:
- [Architecture](docs/implementation/architecture.md)
- [Data model](docs/implementation/data-model.md)
- [Permissions & audit log](docs/implementation/permissions.md)
- [Roadmap](docs/implementation/roadmap.md)
