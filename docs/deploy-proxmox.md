# Deploy on Proxmox

Target: a Debian 12 unprivileged LXC running Docker Compose, with the SQLite database in a host-bind-mounted volume so you can back it up trivially.

## Resource sizing

| | Value | Note |
|---|---|---|
| CPU | 1 vCPU | discord.py is largely idle waiting on the gateway |
| RAM | 512 MB | comfortable headroom for SQLite + Python |
| Disk | 4 GB | OS + image + tiny DB |
| Network | DHCP or static | static recommended for stable backups/SSH |

---

## Quick install (recommended)

One command, run on the **Proxmox host** as `root`. Creates the LXC, installs Docker inside, clones the repo, writes `.env`, and starts the bot.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/bunnyUFO/shadow-dark-bot/main/scripts/install-proxmox.sh)
```

You'll be prompted for the Discord bot token (silent input — the token doesn't appear in your terminal or in `ps`).

### What it does

1. Picks the next free container ID starting from 200 (override with `CTID=…`).
2. Downloads the Debian 12 LXC template if not already present.
3. Creates an unprivileged LXC with `nesting=1,keyctl=1` enabled (required for Docker).
4. Starts the container and waits for network.
5. Inside the container: installs `git`, `sqlite3`, `curl`, then runs Docker's official install script (`curl -fsSL https://get.docker.com | sh`) to get `docker-ce` + the compose plugin. Same approach as tteck's Proxmox helpers.
6. `git clone`s the bot repo to `/opt/shadow-dark-bot`.
7. Writes `.env` with your token (via a temp file + `pct push --perms 0600` — never exposed in process listings).
8. Pre-chowns `/opt/shadow-dark-bot/data` to uid 1000 so the bot's bind-mounted data directory is writable. (The container also self-heals this at startup via its entrypoint, so this is belt-and-suspenders.)
9. `docker compose up -d --build` to build the image and start the bot.
10. Prints the container ID, IP, root password, and useful follow-up commands.

The LXC is set to start on Proxmox boot (`--onboot 1`), and the bot container is set to `restart: unless-stopped`, so once the script finishes there's nothing to babysit.

### Overrides

Any of the script's defaults can be set via env vars before the `bash <(curl …)`:

```bash
CTID=205 \
MEMORY=1024 \
IP_CONFIG="192.168.1.50/24,gw=192.168.1.1" \
ROOTFS_STORAGE=local \
bash <(curl -fsSL https://raw.githubusercontent.com/bunnyUFO/shadow-dark-bot/main/scripts/install-proxmox.sh)
```

| Var | Default | |
|---|---|---|
| `CTID` | next free from 200 | LXC container ID |
| `HOSTNAME_VAR` | `shadowdark-bot` | LXC hostname |
| `MEMORY` | `512` | MB |
| `CORES` | `1` | CPU cores |
| `DISK_GB` | `4` | rootfs size in GB |
| `BRIDGE` | `vmbr0` | network bridge |
| `IP_CONFIG` | `dhcp` | or e.g. `192.168.1.50/24,gw=192.168.1.1` |
| `TEMPLATE_STORAGE` | `local` | where Proxmox templates live |
| `ROOTFS_STORAGE` | `local-lvm` | where the LXC disk lives |
| `REPO_URL` | the public repo URL | useful if you fork |
| `HWADDR` | empty | MAC for `eth0` (e.g. `AA:BB:CC:DD:EE:FF`); set it to keep a DHCP reservation or firewall rule when reinstalling |
| `WEB_PORT` | `8080` | public web port written to `.env` (compose publishes the same port) |
| `DISCORD_TOKEN` | prompted | set this to skip the prompt |

## Public web sheet (Bunnylink)

The public, Discord-login sheet (and the Discord Activity) runs inside the bot container on port 8080, behind Nginx Proxy Manager (NPM). The installer never changes an existing CT; these are **steps you run yourself**, in order.

**1. Give CT 200 a static IP without reinstalling** (run on the Proxmox host):

```bash
pct config 200 | grep net0
pct set 200 --net0 name=eth0,bridge=vmbr0,hwaddr=<keep the existing>,ip=192.168.1.34/24,gw=<your gateway>
pct reboot 200
```

- Keep the existing `hwaddr` from the first command.
- Check 192.168.1.34 is outside the router's DHCP pool.
- NPM's target must change at the same moment (step 4).

**2. Configure `.env` in CT 200** (`pct enter 200`, `nano /opt/shadow-dark-bot/.env`): set `WEB_ENABLED=true`, the keys listed in [`.env.example`](../.env.example) (`PUBLIC_BASE_URL`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `SESSION_SECRET`, `ALLOWED_GUILD_IDS`) and `FORWARDED_ALLOW_IPS=192.168.1.11` (the NPM host; never `*`). Then run the update script (see [Updating](#updating)): it pulls the new compose file and recreates the container so port 8080 is published. The bot refuses to start if a required key is missing.

**3. Route 53:** `shadowdark.bunnyufo.net` CNAME `home.bunnyufo.net`.

**4. NPM proxy host:** `shadowdark.bunnyufo.net` -> `http://192.168.1.34:8080`; Force SSL on; Websockets on; Let's Encrypt via Route 53 DNS-01; no frame-blocking headers (the Activity runs in an iframe).

**5. Optional Proxmox firewall on CT 200:** allow 8080 only from 192.168.1.11.

**6. Discord portal:** follow [Discord Developer Portal](implementation/discord-activity.md#discord-developer-portal).

**Memory:** the runtime fits in 512 MB; raise to 1 GB if an in-image frontend build runs out of memory.

**Check:**

```bash
curl -I http://192.168.1.34:8080/      # from the NPM host: should answer
```

then open `https://shadowdark.bunnyufo.net/`: it shows the login.

## Updating

Pull the latest code and rebuild the container. Migrations apply on startup automatically.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/bunnyUFO/shadow-dark-bot/main/scripts/update-bot.sh)
```

If you used a non-default CTID:

```bash
CTID=205 bash <(curl -fsSL https://raw.githubusercontent.com/bunnyUFO/shadow-dark-bot/main/scripts/update-bot.sh)
```

## Backups

The SQLite file lives at `/opt/shadow-dark-bot/data/shadowdark.db` **inside the LXC**. Use SQLite's online backup (safe while the bot is running). Easiest path: schedule a cron on the LXC itself.

`pct enter <ctid>`, then:

```bash
mkdir -p /backups
sqlite3 /opt/shadow-dark-bot/data/shadowdark.db ".backup '/backups/shadowdark-$(date +%F).db'"
```

Add to `/etc/cron.d/shadowdark-backup` (inside the LXC):

```
0 4 * * * root sqlite3 /opt/shadow-dark-bot/data/shadowdark.db ".backup '/backups/shadowdark-$(date +\%F).db'" && find /backups -name 'shadowdark-*.db' -mtime +30 -delete
```

Runs nightly at 04:00 and prunes backups older than 30 days. To push backups off the LXC, add an `rsync` to a NAS or another host after the backup command.

## Restore

`pct enter <ctid>`, then:

```bash
cd /opt/shadow-dark-bot
docker compose down
cp /backups/shadowdark-<date>.db /opt/shadow-dark-bot/data/shadowdark.db
docker compose up -d
```

## Logs and troubleshooting

From the Proxmox host:

```bash
pct exec <ctid> -- bash -c "cd /opt/shadow-dark-bot && docker compose logs -f bot"
```

| Symptom | Check |
|---|---|
| `docker compose up` exits immediately | `docker compose logs bot` — usually a bad token or a code-level error during startup |
| Bot online but commands missing | Bot was invited without `applications.commands` scope (re-invite per `docs/setup-discord.md`). The startup log prints one `Synced N command(s) to guild …` line per server it's in — confirm the server you're testing in is listed. |
| `unable to open database file` | If you somehow bypassed the entrypoint: `pct exec <ctid> -- chown -R 1000:1000 /opt/shadow-dark-bot/data && pct exec <ctid> -- bash -c "cd /opt/shadow-dark-bot && docker compose restart bot"` |
| `script_location key not found` | The Dockerfile got built non-editable; pull latest (`pip install -e .` in the Dockerfile is required so `Path(__file__).parents[2]` resolves to `/app` for finding `alembic.ini`) |
| `bad interpreter` on `docker-entrypoint.sh` | `.gitattributes` issue — file got CRLF line endings via Windows. Add `*.sh text eol=lf`, `git add --renormalize .`, recommit |
| LXC won't run Docker | Confirm `features: nesting=1,keyctl=1` in `/etc/pve/lxc/<ctid>.conf`. The install script sets this automatically. |
| `network didn't come up` during install | Re-run the script — first network init can be slow on some hosts |
| GitHub CDN cache | After pushing a script fix, `raw.githubusercontent.com` may serve the old version for ~5 min. Wait or curl with a cache-buster `?v=<random>` |

## Stopping cleanly

From the Proxmox host:

```bash
pct exec <ctid> -- bash -c "cd /opt/shadow-dark-bot && docker compose down"
```

This stops the bot but keeps the LXC running. To stop the LXC itself:

```bash
pct stop <ctid>
```

To wipe everything (don't, unless you mean it):

```bash
pct stop <ctid>
pct destroy <ctid>
```

---

## Manual install (fallback if you'd rather click through the UI)

If you'd prefer not to run the script, the same setup by hand:

### 1. Create the LXC

In the Proxmox web UI:

1. **Create CT** → uncheck *Privileged container*.
2. Template: `debian-12-standard`.
3. Hostname (e.g., `shadowdark-bot`), root password.
4. Disk: 4 GB. CPU: 1 core. Memory: 512 MB. Bridge `vmbr0`, DHCP or static.

Then edit `/etc/pve/lxc/<ctid>.conf` on the Proxmox host and add:

```
features: nesting=1,keyctl=1
```

Restart the container.

### 2. Install deps inside the LXC

`pct enter <ctid>`, then:

```bash
apt update && apt upgrade -y
apt install -y curl git sqlite3 ca-certificates gnupg
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
```

> **Why not `apt install docker-compose-plugin`?** That package isn't in Debian 12's default repos — it lives in Docker's own apt repo. `get.docker.com` adds the repo and installs `docker-ce` + the compose plugin in one shot.

### 3. Clone, configure, start

```bash
git clone https://github.com/bunnyUFO/shadow-dark-bot.git /opt/shadow-dark-bot
cd /opt/shadow-dark-bot
cp .env.example .env
# edit .env to set DISCORD_TOKEN
docker compose up -d --build
docker compose logs -f bot
```

You should see `Logged in as …` and `Synced N command(s) to guild <name> (<id>)`.

The container's entrypoint chowns `/app/data` to its `bot` user at startup, so the bind-mounted host directory works regardless of who owns it on the LXC side.
