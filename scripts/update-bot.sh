#!/usr/bin/env bash
# Shadow Dark bot — Proxmox host updater.
#
# Deploys origin/main in the bot LXC by hand: backs up the database, rebuilds the
# container (migrations apply on startup) and rolls back if the bot doesn't come
# up. Pushes to main deploy on their own (.github/workflows/deploy.yml); this is
# for when the runner is down.
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

step "Deploying origin/main in container $CTID (backup, rebuild, roll back on failure)..."
# Runs main's own scripts/deploy.sh, the same steps the Deploy workflow uses.
pct exec "$CTID" -- bash -c "cd $REPO_DIR && git fetch -q origin main \
  && git show origin/main:scripts/deploy.sh > /tmp/shadowdark-deploy.sh \
  && bash /tmp/shadowdark-deploy.sh origin/main"

step "Recent logs (last 20 lines):"
pct exec "$CTID" -- bash -c "cd $REPO_DIR && docker compose logs --tail 20 bot"

echo
echo "Done. Follow live logs with:"
echo "  pct exec $CTID -- bash -c \"cd $REPO_DIR && docker compose logs -f bot\""
