#!/usr/bin/env bash
# Shadow Dark bot — Proxmox host installer.
#
# Creates an unprivileged Debian 12 LXC, installs Docker inside, clones the bot
# repo, writes .env with your Discord token, and starts the bot. Idempotent
# operations are skipped; existing CTIDs are refused with a clear message.
#
# Usage (run as root on a Proxmox host):
#   bash <(curl -fsSL https://raw.githubusercontent.com/bunnyUFO/shadow-dark-bot/main/scripts/install-proxmox.sh)
#
# Override defaults via env vars:
#   CTID=201 MEMORY=1024 IP_CONFIG="192.168.1.50/24,gw=192.168.1.1" \
#     bash <(curl -fsSL https://raw.githubusercontent.com/bunnyUFO/shadow-dark-bot/main/scripts/install-proxmox.sh)
#
# All env vars (with defaults):
#   CTID                 next free from 200
#   HOSTNAME_VAR         shadowdark-bot
#   MEMORY               512        (MB)
#   CORES                1
#   DISK_GB              4
#   BRIDGE               vmbr0
#   IP_CONFIG            dhcp       (or "192.168.1.50/24,gw=192.168.1.1")
#   TEMPLATE_STORAGE     local
#   ROOTFS_STORAGE       local-lvm
#   REPO_URL             https://github.com/bunnyUFO/shadow-dark-bot.git
#   DISCORD_TOKEN        (prompted securely if unset)

set -euo pipefail

# ---------- Defaults ----------
HOSTNAME_VAR="${HOSTNAME_VAR:-shadowdark-bot}"
MEMORY="${MEMORY:-512}"
CORES="${CORES:-1}"
DISK_GB="${DISK_GB:-4}"
BRIDGE="${BRIDGE:-vmbr0}"
IP_CONFIG="${IP_CONFIG:-dhcp}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
ROOTFS_STORAGE="${ROOTFS_STORAGE:-local-lvm}"
REPO_URL="${REPO_URL:-https://github.com/bunnyUFO/shadow-dark-bot.git}"
REPO_DIR="/opt/shadow-dark-bot"

# ---------- Helpers ----------
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- Preflight ----------
[ "$(id -u)" -eq 0 ] || fail "must run as root"
command -v pct >/dev/null 2>&1 || fail "pct not found — are you on a Proxmox host?"
command -v pveam >/dev/null 2>&1 || fail "pveam not found — are you on a Proxmox host?"

# ---------- Discord token ----------
if [ -z "${DISCORD_TOKEN:-}" ]; then
    if [ -r /dev/tty ]; then
        read -rsp "Discord bot token: " DISCORD_TOKEN </dev/tty
        echo
    else
        fail "DISCORD_TOKEN is not set and no terminal available for prompting"
    fi
fi
[ -n "$DISCORD_TOKEN" ] || fail "Discord token cannot be empty"

# ---------- Pick CTID ----------
if [ -z "${CTID:-}" ]; then
    CTID=200
    while pct status "$CTID" >/dev/null 2>&1; do
        CTID=$((CTID + 1))
    done
    step "Using next available container ID: $CTID"
else
    if pct status "$CTID" >/dev/null 2>&1; then
        fail "container $CTID already exists. Pick a different CTID or run: pct destroy $CTID"
    fi
fi

# ---------- Template ----------
step "Looking for Debian 12 template in storage '$TEMPLATE_STORAGE'..."
TEMPLATE_PATH=$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null \
    | awk '/debian-12-standard/ {print $1; exit}' || true)

if [ -z "$TEMPLATE_PATH" ]; then
    step "Template not found locally; downloading..."
    pveam update
    TEMPLATE_NAME=$(pveam available --section system \
        | awk '/debian-12-standard/ {print $2; exit}')
    [ -n "$TEMPLATE_NAME" ] || fail "no debian-12-standard template available"
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE_NAME"
    TEMPLATE_PATH=$(pveam list "$TEMPLATE_STORAGE" \
        | awk '/debian-12-standard/ {print $1; exit}')
fi
[ -n "$TEMPLATE_PATH" ] || fail "couldn't locate Debian 12 template after download"
echo "Template: $TEMPLATE_PATH"

# ---------- Create LXC ----------
step "Creating unprivileged LXC $CTID ($HOSTNAME_VAR)..."
ROOT_PW=$(openssl rand -base64 18)
pct create "$CTID" "$TEMPLATE_PATH" \
    --hostname "$HOSTNAME_VAR" \
    --unprivileged 1 \
    --features "nesting=1,keyctl=1" \
    --memory "$MEMORY" \
    --cores "$CORES" \
    --rootfs "${ROOTFS_STORAGE}:${DISK_GB}" \
    --net0 "name=eth0,bridge=${BRIDGE},ip=${IP_CONFIG}" \
    --password "$ROOT_PW" \
    --onboot 1

# ---------- Start ----------
step "Starting container..."
pct start "$CTID"

step "Waiting for container network..."
network_ready=0
for _ in $(seq 1 60); do
    if pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1; then
        network_ready=1
        break
    fi
    sleep 2
done
[ "$network_ready" -eq 1 ] || fail "container network didn't come up within ~2 min"

# ---------- Install dependencies ----------
step "Installing base packages (git, sqlite3, curl)..."
pct exec "$CTID" -- bash -c '
    set -e
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq curl git sqlite3 ca-certificates gnupg >/dev/null
'

step "Installing Docker via the official get.docker.com script..."
# docker-compose-plugin isn't in Debian 12's default repos. Docker's own
# convenience script adds the official apt repo and installs docker-ce plus
# the compose plugin in one shot. (Same approach the tteck Docker LXC helper
# uses.) Skipped if docker is already installed (re-runs of the script).
pct exec "$CTID" -- bash -c '
    set -e
    if ! command -v docker >/dev/null 2>&1; then
        curl -fsSL https://get.docker.com | sh
    fi
    systemctl enable --now docker
'

# ---------- Clone repo ----------
step "Cloning bot repo into $REPO_DIR..."
pct exec "$CTID" -- bash -c "git clone $REPO_URL $REPO_DIR"

# ---------- Write .env (via temp file + pct push for security) ----------
step "Writing .env (token written via a temp file, not exposed in process listings)..."
ENV_TMP=$(mktemp)
chmod 600 "$ENV_TMP"
cat > "$ENV_TMP" <<EOF
DISCORD_TOKEN=$DISCORD_TOKEN
DATABASE_URL=sqlite:///./data/shadowdark.db
EOF
pct push "$CTID" "$ENV_TMP" "$REPO_DIR/.env" --perms 0600
rm -f "$ENV_TMP"
unset DISCORD_TOKEN

# ---------- Prepare data dir ----------
# Docker bind-mounts ./data to /app/data inside the container. If we let Docker
# create ./data on first run, it lands owned by root, and the in-container bot
# user (uid 1000) can't write to it — SQLite errors with "unable to open
# database file". Pre-create with the correct ownership.
step "Preparing data directory (owned by uid 1000 for the in-container bot user)..."
pct exec "$CTID" -- bash -c "
    mkdir -p $REPO_DIR/data
    chown 1000:1000 $REPO_DIR/data
"

# ---------- Build + start ----------
step "Building and starting the bot (first build takes 2–3 minutes)..."
pct exec "$CTID" -- bash -c "cd $REPO_DIR && docker compose up -d --build"

# ---------- Done ----------
IP=$(pct exec "$CTID" -- bash -c "hostname -I | awk '{print \$1}'" 2>/dev/null || echo "(unknown)")

step "Deployment complete."
cat <<EOF

  Container ID:  $CTID
  Hostname:      $HOSTNAME_VAR
  IP:            $IP
  Root password: $ROOT_PW
                 (note this down — won't be shown again)

  The LXC is set to start on Proxmox boot, and the bot container restarts
  automatically inside it. No further action required.

  Useful commands (run on this Proxmox host):
    Live logs:   pct exec $CTID -- bash -c "cd $REPO_DIR && docker compose logs -f bot"
    Status:      pct exec $CTID -- bash -c "cd $REPO_DIR && docker compose ps"
    Shell in:    pct enter $CTID
    Update:      CTID=$CTID bash <(curl -fsSL https://raw.githubusercontent.com/bunnyUFO/shadow-dark-bot/main/scripts/update-bot.sh)
    Destroy:     pct stop $CTID && pct destroy $CTID

EOF
