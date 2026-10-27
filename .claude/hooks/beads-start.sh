#!/usr/bin/env bash
# Tracked project: make sure Beads is installed and this clone or worktree has its issues. Never blocks a session.
command -v bd >/dev/null 2>&1 || npm i -g @beads/bd@1.3.2 >/dev/null 2>&1
command -v bd >/dev/null 2>&1 || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
top=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -d "$top/.beads" ] || exit 0
# Each clone and each linked worktree uses its own .beads and its own issues.jsonl.
export BEADS_DIR="$top/.beads"
[ -n "${CLAUDE_ENV_FILE:-}" ] && echo "export BEADS_DIR=\"$top/.beads\"" >> "$CLAUDE_ENV_FILE"
t() { if command -v timeout >/dev/null 2>&1; then timeout "$@"; else shift; "$@"; fi; }
# Issues travel in the committed .beads/issues.jsonl (no Dolt remote): bootstrap when there is no
# database, import after a pull, rebase or merge brought a newer file.
if bd ready >/dev/null 2>&1; then
  [ -s "$top/.beads/issues.jsonl" ] && t 30 bd import "$top/.beads/issues.jsonl" >/dev/null 2>&1
else
  t 60 bd bootstrap --yes >/dev/null 2>&1
fi
exit 0
