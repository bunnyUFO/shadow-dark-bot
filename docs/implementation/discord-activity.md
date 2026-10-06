# Plan: Character sheet as a Discord Activity

**Status: planned — nothing here is built yet.** This doc is the plan of record for turning
`/character sheet` into an embedded web app (a Discord **Activity**) that opens inside Discord,
restricted to our own servers.

Hosting lives on the Bunnylink Proxmox server; the infra side of this plan is mirrored in the
`proxmox-bunnylink` repo (`plans/shadowdark-activity.md`).

## Goal

A full character sheet (Combat / Inventory / Roleplaying tabs, editing, spells, carry/give) as a
web page that:

1. Works on the **home network without Discord** — `http://<bot-container-ip>:8081`, pick your
   character (the "local" app; built first so the UI can be iterated on).
2. Works as a **public website** at `https://shadowdark.bunnyufo.net` with "Log in with Discord".
3. Opens **inside Discord** as an Activity — from the App Launcher or an "Open full sheet" button
   on `/character sheet`.
4. Is usable **only by our players, only in our servers** (public app), or only from the home
   network (local app).

### Two apps, one process

| | Local app | Public app |
|---|---|---|
| Setting | `LOCAL_WEB_ENABLED=true` | `WEB_ENABLED=true` (+ OAuth settings) |
| Port | 8081 | 8080 (behind Nginx Proxy Manager) |
| Login | Pick a character from a list — no Discord | Discord OAuth + guild allowlist |
| Reachable from | Private / loopback addresses only; requests carrying proxy headers (`X-Forwarded-For`, `X-Real-IP`, `Forwarded`) are refused | The internet, via NPM |
| Session tokens | Signed with a random key made at every start (picking again after a restart) | Signed with `SESSION_SECRET` |

Both serve the same frontend and API. Local tokens are signed with a different key **and** a
different salt, so a token from the no-login app can never be replayed against the public one.
**Never point NPM at port 8081.**

The existing Discord-native sheet keeps working; the web app is an addition, not a replacement.

## How Activities work (and why the sheet stays private)

- An Activity is our web page loaded in an iframe on each user's own Discord client, via
  Discord's proxy at `<app_id>.discordsays.com`. Nothing is screen-shared.
- Discord groups users into one **instance per channel**: the first launch creates it; anyone
  else launching in that channel joins it and gets the same `instanceId`.
- Sharing is opt-in per app. We ignore the instance for data — the backend loads the character
  for the **authenticated user**, so several players in one channel each see only their own sheet.
  Others can see *that* you're in the Activity (participant avatars), not your sheet.
- Activities can launch in server channels, threads, DMs and group DMs. Whether a guild-install-only
  app can launch in a DM is untested — verify before relying on it.
- Future opt-in idea: a GM view that uses the shared instance to show the party's sheets side by side.

## Restricting access to our servers

| Layer | Mechanism |
|---|---|
| Unverified Activity (default, never submit for verification) | Launches only for the app owner, dev team, and up to **50 App Testers**, and only in servers with **< 25 members** |
| App settings | "Public Bot" **off** (only the owner can install); **User Install off** (guild install only) |
| Backend allowlist | `ALLOWED_GUILD_IDS` env var. The `guild_id` the SDK reports is client-supplied, so the backend confirms membership with `guild.fetch_member(user_id)` via the running bot |
| Identity | User ID always comes from the verified Discord OAuth token, never from the client |

Consequences: every player must be invited as an App Tester and accept; a server that grows to
25+ members loses the Activity (the standalone website still works there).

## Architecture

```
Discord client (iframe) / browser
  → https://shadowdark.bunnyufo.net        (Route 53 → home IP)
  → router :443 → Nginx Proxy Manager (CT 103, 192.168.1.11, TLS)
  → CT 200 :8080 → one Python process:
        ├── discord.py bot           (existing)
        ├── FastAPI web API + static frontend   (new)
        └── shared services layer    (new) → SQLite (existing)
```

- **One process.** uvicorn runs inside the bot's event loop (started from `setup_hook`). Keeps a
  single SQLite writer, lets the API use the bot's guild cache for membership checks, and keeps
  deploy as one compose service.
- **Same origin.** The frontend and API are served from the same host so the Activity needs a
  single URL mapping and no CORS.

### Services layer (the big refactor)

`cogs/player_characters.py` (~2,400 lines) mixes rules with Discord replies — e.g. `_do_carry`
validates input and calls `interaction.response.send_message` in the same function; likewise
`_do_give`, `_do_remove`, `_do_add_more`, `_do_learn_spell`, `_do_forget_spell` and the edit modals'
`on_submit`. These move into `src/shadowdark_bot/services/characters.py`:

- Functions take a session + plain arguments, return results or raise `CharacterError(message)`.
- The cog catches `CharacterError` and renders the existing `**Failed to …**` ephemeral reply.
- The API maps `CharacterError` to a 4xx JSON error.
- `rules.py`, `storage.py` and the parse helpers (`_parse_scores`, `_parse_spellcasting`, …) are reused as-is.
- Unit tests cover the services without discord.py — matching what `architecture.md` already claims.

### Web API

Code: `src/shadowdark_bot/web/` — `app.py` (routes), `auth.py` (OAuth exchange + session
tokens), `schemas.py` (JSON shapes), `server.py` (uvicorn in the bot's loop + membership check).

| Method & path | Status | Purpose |
|---|---|---|
| `GET /api/health` | ✅ | Liveness, no login |
| `GET /api/auth/config` | ✅ | `client_id`, `redirect_uri`, scopes for the frontend to start a login |
| `POST /api/auth/exchange` | ✅ | `{code, flow: "web" \| "activity"}` → exchanges the code with Discord, checks the guild allowlist, returns our session token (+ the Discord access token for the SDK on `activity`) |
| `GET /api/me` | ✅ | Session user + whether they have a character |
| `GET /api/character` | ✅ | Full sheet for the session user (404 if none) |
| `GET /api/characters/{user_id}` | ✅ | Read-only view, held items only (like `/character show`) |
| `GET /api/spells?class=&tier=` · `GET /api/items?q=&type=&limit=` | ✅ | Spell reference and item catalog (Spells / Items sections, pickers) |
| `GET /api/guild` | ✅ | Coffers, shared storage locations with contents, magic item treasury (status + borrower's character name) |
| `GET /api/characters` | ✅ | Party list |
| `PATCH /api/character/{identity,stats,gold,skills,proficiencies}` | Phase 5 | Mirrors the edit modals |
| `POST /api/character/items` · `DELETE …/items/{id}` · `POST …/items/{id}/give` | Phase 5 | Carry / remove / give |
| `POST /api/character/spells` · `DELETE …/spells/{id}` | Phase 5 | Learn / forget |

Errors are `{"detail": "..."}`: 401 not logged in, 403 not in an allowed guild, 404 no character,
400 a rule failed (the same reason text the bot shows). Interactive docs (`/docs`) are disabled.

**Allowlist.** At login the bot checks whether the user is in any `ALLOWED_GUILD_IDS` guild
(member cache, then a REST lookup — no members intent needed). The result is baked into the
session; tokens expire after `SESSION_TTL_HOURS` (default 12), so removing someone from the server
locks them out by the next login.

Session token is sent as `Authorization: Bearer …` — **not a cookie**, because third-party-iframe
cookies are frequently blocked.

### Frontend

Plain ES modules + CSS in `src/shadowdark_bot/web/static/`, served by the app itself — **no build
step**, so the Docker image needs no Node and the 512 MB container is fine. (Originally sketched as
Vite + TypeScript; revisit only if the UI outgrows this.) Startup detects whether it is
inside Discord (`frame_id` query param): if so, it uses `@discord/embedded-app-sdk`
(`ready()` → `authorize()` → `/api/auth/exchange` → `authenticate()`); otherwise it uses the
normal OAuth redirect. Everything after login is shared.

**Rules so the Activity step stays small:**

- Relative URLs only (`/api/...`) — inside Discord every request goes through the proxy.
- Bundle fonts, icons and scripts; no CDNs (external hosts need URL mappings).
- No popups, new tabs or top-level redirects once inside Discord.
- Mobile-first layout; the Activity panel is narrow and many players are on phones.
- Never send `X-Frame-Options: DENY` or a restrictive `frame-ancestors`.

## Hosting (Bunnylink)

Follows the existing homelab pattern (see `proxmox-bunnylink/reverse-proxy.md`):

| Step | Detail |
|---|---|
| Static IP for CT 200 | Proposed `192.168.1.34`, set in `/etc/pve/lxc/200.conf` (keep `hwaddr`) — it is currently a plain DHCP lease (`.151`) |
| DNS | Route 53 `shadowdark.bunnyufo.net` → **CNAME `home.bunnyufo.net`**, so a home-IP change is one record |
| Certificate | NPM Let's Encrypt via Route 53 DNS-01; good moment to switch to the planned wildcard `*.bunnyufo.net` |
| NPM proxy host | `shadowdark.bunnyufo.net` → `http://192.168.1.34:8080`, Force SSL, Websockets on, no frame-blocking headers |
| Compose | Publish `8080:8080`; uvicorn `--proxy-headers --forwarded-allow-ips=192.168.1.11` |
| Firewall (optional) | Proxmox firewall on CT 200: allow 8080 only from `192.168.1.11` |
| Resources | Runtime fits 512 MB; an in-image Node build may OOM → bump CT 200 to 1 GB **or** build the frontend in GitHub Actions |
| Installer | Teach `scripts/install-proxmox.sh` the static IP / port so a reinstall reproduces it |

### Discord Developer Portal

- Activities → enable (auto-creates the **Launch** entry-point command).
- URL Mappings → `/` → `shadowdark.bunnyufo.net`.
- OAuth2 → add redirect `https://shadowdark.bunnyufo.net/auth/callback` (standalone login);
  copy the client secret into `.env` as `DISCORD_CLIENT_SECRET`.
- App Testers → invite each player.
- Keep Public Bot off and User Install off.

New `.env` keys (see `.env.example`): `WEB_ENABLED`, `WEB_PORT`, `PUBLIC_BASE_URL`,
`DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `SESSION_SECRET` (≥ 32 chars), `SESSION_TTL_HOURS`,
`ALLOWED_GUILD_IDS`, `FORWARDED_ALLOW_IPS`. The web app is off by default; with
`WEB_ENABLED=true` the bot refuses to start until the required ones are set.

## Phases

Order changed (2026-10-03): build and iterate on the **local** app first, then do public hosting.

1. ✅ **Services refactor** — `services/characters.py` extracted; the cog calls it; `tests/test_character_services.py` covers it without discord.py. No behavior change.
2. ✅ **Web API + login backend** — FastAPI in the bot's event loop, `/api/auth/exchange` for both login flows, read endpoints, guild allowlist, `tests/test_web_api.py`.
3. ✅ **Local app + read-only frontend** — `LOCAL_WEB_ENABLED` on port 8081 (pick a character, home network only); no-build frontend in `web/static/`. Sections: **Sheet** (Combat / Inventory / Roleplaying tabs), **Party**, **Guild** (coffers, storage locations, magic item treasury), **Items** (catalog with search + type filter), **Spells** (reference with class / tier / alignment filters). Bottom tab bar on phones, top bar on desktop. Discord redirect login page ready for later. Compose publishes 8081.
4. **Iterate on the UI** from feedback on the local app.
5. **Editing** — write endpoints + edit forms (identity, stats, gold, skills, items, spells, give).
6. **Public hosting** — static IP, DNS, cert, NPM host → port 8080, OAuth settings. Sheet live at `shadowdark.bunnyufo.net`.
7. **Activity** — enable in the portal, URL mapping, SDK login path (vendor the SDK as one ES module; no build step), App Testers invited.
8. **Bot integration** — "Open full sheet" button using `interaction.response.launch_activity()`
   (requires bumping `discord.py` to **≥ 2.6**; `pyproject.toml` currently allows 2.4).

## Gotchas

- **Entry-point command vs. command sync.** The bot only syncs guild-scoped commands
  (`main.py`, `_sync_to_guild`), so it never touches the global **Launch** command. A future global
  `tree.sync()` that doesn't include it can fail or remove it, and the launch button disappears.
- **Stale Discord views.** Editing in the web app doesn't refresh an already-open ephemeral sheet
  message; re-opening it shows the new data. Acceptable.
- **Data is still shared across guilds** (see roadmap: multi-tenant). The guild allowlist limits
  *who* can use the app; it doesn't isolate data per server.
- **Home exposure.** Only NPM is internet-facing; keep the app's endpoints behind Discord login
  and CT 200's port reachable only from NPM.

## References

- [How Activities Work](https://docs.discord.com/developers/activities/how-activities-work)
- [Embedded App SDK reference](https://docs.discord.com/developers/developer-tools/embedded-app-sdk)
- [Local development, URL mappings and tunnels](https://docs.discord.com/developers/activities/development-guides/local-development)
- [Multiplayer experience (instances)](https://docs.discord.com/developers/activities/development-guides/multiplayer-experience)
- [Verified vs. unverified Activities](https://support-dev.discord.com/hc/en-us/articles/26576097154199-What-are-Verified-and-Unverified-Activities)
- [Application commands — entry point](https://docs.discord.com/developers/interactions/application-commands)
- [discord.py `InteractionResponse.launch_activity`](https://discordpy.readthedocs.io/en/latest/interactions/api.html)
