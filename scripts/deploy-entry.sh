#!/usr/bin/env bash
# Shadow Dark bot — the deploy command the GitHub Actions runner may run as root.
#
#   shadowdark-deploy <full commit sha>
#
# scripts/install-runner.sh installs this as /usr/local/sbin/shadowdark-deploy,
# root-owned, with a sudo rule for gh-runner. It accepts only a commit on
# origin/main and runs that commit's own scripts/deploy.sh, so changes to the
# deploy steps ship like any other change. Running main's code as root adds
# nothing: the deploy builds and runs main's code anyway. What keeps this safe on
# a public repo is that only the owner can put commits on main.

set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/shadow-dark-bot}"

[ $# -eq 1 ] && [[ "$1" =~ ^[0-9a-f]{40}$ ]] || {
  echo "usage: shadowdark-deploy <full commit sha>" >&2
  exit 2
}
SHA="$1"

cd "$REPO_DIR"
git fetch --quiet origin main
git merge-base --is-ancestor "$SHA" origin/main || {
  echo "ERROR: $SHA is not on origin/main" >&2
  exit 1
}

script="$(mktemp)"
trap 'rm -f "$script"' EXIT
git show "$SHA:scripts/deploy.sh" > "$script"
bash "$script" "$SHA"
