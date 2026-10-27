#!/usr/bin/env bash
# Tracked project: make sure Beads is installed and this clone has its issues. Never blocks a session.
command -v bd >/dev/null 2>&1 || npm i -g @beads/bd@1.3.2 >/dev/null 2>&1
command -v bd >/dev/null 2>&1 || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
t() { if command -v timeout >/dev/null 2>&1; then timeout "$@"; else shift; "$@"; fi; }
# Issues come from the committed .beads/issues.jsonl (bd bootstrap imports it). No dolt remote: see the PR body.
bd ready >/dev/null 2>&1 || t 60 bd bootstrap --yes >/dev/null 2>&1
exit 0
