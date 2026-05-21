# Deploy on Proxmox

Target: a Debian 12 unprivileged LXC running Docker Compose, with the SQLite database in a host-bind-mounted volume so you can back it up trivially.

## Resource sizing

| | Value | Note |
|---|---|---|
| CPU | 1 vCPU | discord.py is largely idle waiting on the gateway |
| RAM | 512 MB | comfortable headroom for SQLite + Python |
| Disk | 4 GB | OS + image + tiny DB |
| Network | DHCP or static | static recommended for stable backups/SSH |

## 1. Create the LXC

In the Proxmox web UI:

1. **Create CT** → uncheck *Privileged container* (keep it unprivileged for security).
2. Template: `debian-12-standard`.
3. Set hostname (e.g., `shadowdark-bot`), root password (or SSH key).
4. Disk: 4 GB on your preferred storage.
5. CPU: 1 core. Memory: 512 MB.
6. Network: assign your bridge (usually `vmbr0`) and configure DHCP or a static IP.
7. Confirm and start.

### Enable Docker in an unprivileged LXC

Edit the LXC config on the Proxmox host (e.g., `/etc/pve/lxc/<ctid>.conf`) and add:
```
features: nesting=1,keyctl=1
```
Restart the container after the edit.

## 2. Install Docker + git inside the LXC

`pct enter <ctid>` from the Proxmox host (or SSH in), then:
```bash
apt update && apt upgrade -y
apt install -y docker.io docker-compose-plugin git sqlite3
systemctl enable --now docker
```

## 3. Clone and configure

```bash
mkdir -p /opt && cd /opt
git clone <your-repo-url> shadow-dark-bot
cd shadow-dark-bot
cp .env.example .env
# edit .env to set DISCORD_TOKEN (the only required value)
```

## 4. Start

```bash
docker compose up -d
docker compose logs -f bot
```
You should see `Logged in as …` and `Synced N command(s) to guild <name> (<id>)`. Ctrl+C exits the log stream; the container keeps running.

## 5. Update workflow

```bash
cd /opt/shadow-dark-bot
git pull
docker compose up -d --build
```
Alembic migrations run at container start, so schema changes apply automatically.

## 6. Backups

The SQLite file lives at `/opt/shadow-dark-bot/data/shadowdark.db` on the LXC. Use SQLite's online backup (safe while the bot is running):

```bash
mkdir -p /backups
sqlite3 /opt/shadow-dark-bot/data/shadowdark.db ".backup '/backups/shadowdark-$(date +%F).db'"
```

Add to `/etc/cron.d/shadowdark-backup`:
```
0 4 * * * root sqlite3 /opt/shadow-dark-bot/data/shadowdark.db ".backup '/backups/shadowdark-$(date +\%F).db'" && find /backups -name 'shadowdark-*.db' -mtime +30 -delete
```
This runs nightly at 04:00 and prunes backups older than 30 days. Consider rsyncing `/backups` to another host or Proxmox storage pool.

## 7. Restore

```bash
docker compose down
cp /backups/shadowdark-<date>.db /opt/shadow-dark-bot/data/shadowdark.db
docker compose up -d
```

## 8. Logs and troubleshooting

| Symptom | Check |
|---|---|
| `docker compose up` exits immediately | `docker compose logs bot` — usually a bad token or DB permission |
| Bot online but commands missing | Bot was invited without `applications.commands` scope (re-invite with the URL from `docs/setup-discord.md`), or it joined a second guild and is syncing to the wrong one (check the startup log line for the guild name/id) |
| Permission denied writing DB | Mount issue: `ls -l /opt/shadow-dark-bot/data` — container runs as non-root user; chown the data dir |
| LXC won't run Docker | Confirm `features: nesting=1,keyctl=1` in the LXC config and restart the container |

## 9. Stopping cleanly

```bash
docker compose down
```
This stops the container without removing data. To wipe everything (don't, unless you mean it):
```bash
docker compose down -v   # ⚠️ removes volumes
```
