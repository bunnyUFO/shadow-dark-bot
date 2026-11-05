#!/usr/bin/env bash
# Beads in a cloud session: send this sandbox's changes to S3 at the end of each turn. An address never reaches the store's history. Never blocks.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || [ "${CLOUD_BEADS:-}" = "1" ] || exit 0
replica="${BEADS_REPLICA:-/tmp/beads-replica}"
[ -d "$replica/.beads" ] || exit 0
command -v bd >/dev/null 2>&1 || exit 0
export BEADS_DIR="$replica/.beads" BD_NON_INTERACTIVE=1 GIT_TERMINAL_PROMPT=0
cd "$replica" || exit 0
# The same rule as the Dolt server's check: only noreply and placeholder addresses.
if timeout 60 bd export 2>/dev/null | grep -oE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' | grep -qviE '@users\.noreply\.github\.com$|@(example\.(com|org|net)|localhost)$|\.(test|example|invalid)$'; then
  echo "Beads: a personal address is in the rows, so nothing was pushed to S3." >&2
  exit 0
fi
timeout 120 bd sync >/dev/null 2>&1 || echo "Beads: bd sync failed; the changes are still in this sandbox." >&2
exit 0
