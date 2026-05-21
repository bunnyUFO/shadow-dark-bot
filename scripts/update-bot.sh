#!/usr/bin/env bash
# Shadow Dark bot — Proxmox host updater.
#
# Pulls the latest code in the bot LXC and rebuilds the container. Migrations
# apply on the next startup automatically.
#
# Usage (run as root on the Proxmox host):
#   bash <(curl -fsSL https://raw.githubusercontent.com/bunnyUFO/shadow-dark-bot/main/scripts/update-bot.sh)
#
# Override the container ID if you didn't use the default (200):
#   CTID=201 bash <(curl -fsSL https://raw.githubusercontent.com/bunnyUFO/shadow-dark-bot/main/scripts/update-bot.sh)

set -euo pipefail

CTID="${CTID:-200}"
REPO_DIR="/opt/shadow-dark-bot"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "must run as root"
command -v pct >/dev/null 2>&1 || fail "pct not found — are you on a Proxmox host?"
pct status "$CTID" >/dev/null 2>&1 || fail "container $CTID does not exist"

step "Pulling latest code in container $CTID..."
# fetch + reset (rather than pull) so the box always reflects origin/main exactly,
# even after force-pushes. The deployment LXC has no local commits worth keeping.
pct exec "$CTID" -- bash -c "cd $REPO_DIR && git fetch origin && git reset --hard origin/main"

step "Rebuilding and restarting (migrations apply on startup)..."
pct exec "$CTID" -- bash -c "cd $REPO_DIR && docker compose up -d --build"

step "Recent logs (last 20 lines):"
pct exec "$CTID" -- bash -c "cd $REPO_DIR && docker compose logs --tail 20 bot"

echo
echo "Done. Follow live logs with:"
echo "  pct exec $CTID -- bash -c \"cd $REPO_DIR && docker compose logs -f bot\""
