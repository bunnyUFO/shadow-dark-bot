#!/usr/bin/env bash
# Shadow Dark bot — deploy one commit of main, inside the bot's LXC (CT 200).
#
#   deploy.sh <commit-sha | origin/main>
#
# 1. Backs up the database (data/backups/, the newest $KEEP are kept).
# 2. Checks out the commit and rebuilds the container; migrations run on startup.
# 3. Waits for the bot to log in. If it doesn't, puts the previous commit and the
#    backup back and rebuilds that, so a bad migration can't leave the bot down.
# 4. After a good deploy, prunes unused images and trims the build cache to 1 GB
#    (every rebuild leaves cache behind; it filled the 4 GB disk once).
#
# The GitHub Actions runner calls an installed, root-owned copy of this script
# (/usr/local/sbin/shadowdark-deploy, see scripts/install-runner.sh) through sudo,
# so a workflow can deploy a commit of main and do nothing else. Only commits
# already on origin/main are accepted.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/shadow-dark-bot}"
DB="data/shadowdark.db"
BACKUP_DIR="data/backups"
KEEP="${KEEP:-10}"
READY_TIMEOUT="${READY_TIMEOUT:-180}"
READY_LINE="Logged in as"

step() { printf '\n==> %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ $# -eq 1 ] || fail "usage: deploy.sh <commit-sha | origin/main>"
TARGET="$1"
[[ "$TARGET" =~ ^([0-9a-f]{40}|origin/main)$ ]] || fail "not a full commit SHA or origin/main: $TARGET"

cd "$REPO_DIR"
exec 9>/run/lock/shadowdark-deploy.lock
flock -w 600 9 || fail "another deploy is still running"

step "Fetching main"
git fetch --quiet origin main
SHA="$(git rev-parse --verify "$TARGET^{commit}")"
git merge-base --is-ancestor "$SHA" origin/main || fail "$SHA is not on origin/main"
PREV="$(git rev-parse HEAD)"
echo "deploying ${SHA:0:7} (running ${PREV:0:7})"

bot_running() { [ -n "$(docker compose ps --status running -q bot 2>/dev/null)" ]; }

# Waits until the container started at $1 (a date) logs in. Fails if it never
# does, e.g. a migration crashes and the container keeps restarting.
wait_ready() {
  local since="$1" deadline=$((SECONDS + READY_TIMEOUT)) logs
  while [ $SECONDS -lt $deadline ]; do
    # Read the logs whole, then search: piping into `grep -q` stops reading at the
    # first match, and with pipefail the cut-off `docker compose logs` fails the
    # test, so a bot that did log in could be rolled back.
    logs="$(docker compose logs --since "$since" bot 2>/dev/null || true)"
    if grep -qF "$READY_LINE" <<<"$logs"; then
      sleep 10  # still up a moment later, not a crash loop
      bot_running && return 0
    fi
    sleep 3
  done
  return 1
}

step "Backing up the database"
mkdir -p "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/deploy-$(date -u +%Y%m%dT%H%M%SZ)-${PREV:0:7}.db"
if bot_running; then
  # The bot's own Python, so the copy is consistent while it's running.
  docker compose exec -T bot python -c \
    "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close()" \
    "$DB" "$BACKUP"
elif [ -f "$DB" ]; then
  cp "$DB" "$BACKUP"
  for ext in -wal -shm; do [ -f "$DB$ext" ] && cp "$DB$ext" "$BACKUP$ext"; done
else
  BACKUP=""
  echo "no database yet; nothing to back up"
fi
[ -n "$BACKUP" ] && echo "backup: $BACKUP"
# Keep the newest $KEEP deploy backups.
ls -1t "$BACKUP_DIR"/deploy-*.db 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old" "$old-wal" "$old-shm"
done

step "Building ${SHA:0:7}"
git reset --quiet --hard "$SHA"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker compose up -d --build

step "Waiting for the bot to log in (up to ${READY_TIMEOUT}s)"
if wait_ready "$STARTED"; then
  docker compose logs --since "$STARTED" bot | tail -n 15
  # Only after a good deploy (a failed one keeps everything for the rollback):
  # drop unused images, and trim the build cache to the newest 1 GB so the next
  # build still reuses the installed-packages layer.
  step "Cleaning up Docker"
  docker image prune -f >/dev/null
  docker builder prune -f --max-used-space 1GB | tail -n 1
  df -h / | tail -n 1
  echo
  echo "Deployed ${SHA:0:7}."
  exit 0
fi

echo "The bot didn't come up. Last logs:" >&2
docker compose logs --since "$STARTED" bot | tail -n 60 >&2

step "Rolling back to ${PREV:0:7}"
docker compose stop bot
if [ -n "$BACKUP" ]; then
  # A failed migration may have changed the database: put the backup back.
  rm -f "$DB-wal" "$DB-shm"
  cp "$BACKUP" "$DB"
  for ext in -wal -shm; do [ -f "$BACKUP$ext" ] && cp "$BACKUP$ext" "$DB$ext"; done
  echo "restored $BACKUP"
fi
git reset --quiet --hard "$PREV"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker compose up -d --build
if wait_ready "$STARTED"; then
  fail "deploy of ${SHA:0:7} failed; rolled back to ${PREV:0:7}, which is running"
fi
docker compose logs --since "$STARTED" bot | tail -n 40 >&2
fail "deploy of ${SHA:0:7} failed AND the rollback to ${PREV:0:7} didn't come up either"
