#!/usr/bin/env bash
# Beads in a cloud session: a replica cloned from S3, in the sandbox. Never blocks a session.
# On a machine with the hub-hooks mod this does nothing: the mod connects the checkout to the Dolt server.
# Claude Code runs it at session start. Codex runs it by hand, as AGENTS.md says: CLOUD_BEADS=1 bash .claude/hooks/beads-start.sh
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || [ "${CLOUD_BEADS:-}" = "1" ] || exit 0
top="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
[ -n "$top" ] || exit 0
remote=$(sed -n 's/^[[:space:]]*remote:[[:space:]]*"\{0,1\}\(s3:[^"[:space:]]*\)"\{0,1\}.*/\1/p' "$top/.beads/config.yaml" 2>/dev/null | head -n 1)
prefix=$(sed -n 's/.*Issue prefix: `\([a-z0-9]*\)`.*/\1/p' "$top/CLAUDE.md" 2>/dev/null | head -n 1)
{ [ -n "$remote" ] && [ -n "$prefix" ]; } || { echo "Beads: no S3 store named in this repository"; exit 0; }
command -v bd >/dev/null 2>&1 && command -v dolt >/dev/null 2>&1 || { echo "Beads: bd and dolt are not installed: the environment's setup script installs them"; exit 0; }
[ -n "${AWS_ACCESS_KEY_ID:-}" ] || { echo "Beads: AWS_ACCESS_KEY_ID is not set in this environment"; exit 0; }
replica="${BEADS_REPLICA:-/tmp/beads-replica}"
noreply=bunnyUFO@users.noreply.github.com
export GIT_AUTHOR_EMAIL="$noreply" GIT_COMMITTER_EMAIL="$noreply" BD_NON_INTERACTIVE=1 GIT_TERMINAL_PROMPT=0
unset BEADS_DIR
if [ ! -d "$replica/.beads" ]; then
  mkdir -p "$replica" && cd "$replica" || exit 0
  dolt config --global --get user.name >/dev/null 2>&1 || dolt config --global --add user.name beads >/dev/null 2>&1
  dolt config --global --get user.email >/dev/null 2>&1 || dolt config --global --add user.email beads@local >/dev/null 2>&1
  git init -q . 2>/dev/null
  git config user.email "$noreply"; git config user.name bunnyUFO; git config beads.role maintainer
  timeout 170 bd init --server --remote "$remote" --prefix "$prefix" --skip-agents --skip-hooks --non-interactive >/dev/null 2>&1 || { echo "Beads: could not clone $remote"; rm -rf "$replica"; exit 0; }
fi
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  { echo "export BEADS_DIR=\"$replica/.beads\""; echo "export GIT_AUTHOR_EMAIL=\"$noreply\""; echo "export GIT_COMMITTER_EMAIL=\"$noreply\""; echo "export BD_NON_INTERACTIVE=1"; } >> "$CLAUDE_ENV_FILE"
else
  echo "Beads: run this before using bd: export BEADS_DIR=\"$replica/.beads\" GIT_AUTHOR_EMAIL=\"$noreply\" BD_NON_INTERACTIVE=1"
fi
echo "Beads: a replica of $remote is ready. Run bd sync before finishing, or your changes stay in this sandbox."
exit 0
