#!/usr/bin/env bash
# Shadow Dark bot — install the GitHub Actions runner that deploys main.
#
# Run as root on the Proxmox host, once. It sets up, inside the bot's LXC:
#   - the official actions/runner, as user gh-runner, registered to this repo with
#     the labels bunnylink,shadow-dark-bot and run as a systemd service;
#   - /usr/local/sbin/shadowdark-deploy, a root-owned copy of scripts/deploy.sh,
#     and a sudo rule letting gh-runner run that one command and nothing else.
#
# gh-runner is deliberately NOT in the docker group (that would be root in all
# but name). The repo is public, so the runner can deploy a commit of main and
# do nothing else as root.
#
# Usage — get a one-hour registration token, then pass it in:
#   gh api -X POST repos/bunnyUFO/shadow-dark-bot/actions/runners/registration-token --jq .token
#   RUNNER_TOKEN=<token> bash scripts/install-runner.sh
#
# Re-run it to update the installed deploy script after scripts/deploy.sh changes
# (RUNNER_TOKEN is then only needed if the runner isn't registered yet).

set -euo pipefail

CTID="${CTID:-200}"
REPO_DIR="/opt/shadow-dark-bot"
REPO_URL="https://github.com/bunnyUFO/shadow-dark-bot"
RUNNER_VERSION="2.338.0"
# From the release notes of actions/runner v2.338.0 (actions-runner-linux-x64).
RUNNER_SHA256="af4b794c1bc41d73d40535e3fe092a39f9679cd8d965954c2aca25a05ca41d32"
RUNNER_NAME="bunnylink-shadowdark"
RUNNER_LABELS="bunnylink,shadow-dark-bot"
RUNNER_DIR="/opt/actions-runner"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "must run as root"
command -v pct >/dev/null 2>&1 || fail "pct not found — are you on a Proxmox host?"
pct status "$CTID" >/dev/null 2>&1 || fail "container $CTID does not exist"
in_ct() { pct exec "$CTID" -- bash -c "$1"; }

step "Packages and the gh-runner user"
in_ct "command -v sudo >/dev/null || (apt-get update -qq && apt-get install -y -qq sudo)"
in_ct "id gh-runner >/dev/null 2>&1 || useradd --system --create-home --home-dir $RUNNER_DIR --shell /bin/bash gh-runner"

step "Deploy command and its sudo rule"
in_ct "install -o root -g root -m 0755 $REPO_DIR/scripts/deploy.sh /usr/local/sbin/shadowdark-deploy"
in_ct "printf '%s\n' 'gh-runner ALL=(root) NOPASSWD: /usr/local/sbin/shadowdark-deploy' > /etc/sudoers.d/shadowdark-deploy \
  && chmod 0440 /etc/sudoers.d/shadowdark-deploy && visudo -cf /etc/sudoers.d/shadowdark-deploy"

if in_ct "[ -f $RUNNER_DIR/.runner ]"; then
  echo "runner already registered; left as is"
else
  [ -n "${RUNNER_TOKEN:-}" ] || fail "set RUNNER_TOKEN (see the usage at the top of this script)"
  step "Downloading actions/runner $RUNNER_VERSION"
  TARBALL="actions-runner-linux-x64-$RUNNER_VERSION.tar.gz"
  in_ct "cd $RUNNER_DIR && curl -fsSLo $TARBALL https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/$TARBALL \
    && echo '$RUNNER_SHA256  $TARBALL' | sha256sum -c - \
    && tar xzf $TARBALL && rm $TARBALL && chown -R gh-runner:gh-runner $RUNNER_DIR \
    && ./bin/installdependencies.sh >/dev/null"

  step "Registering $RUNNER_NAME to $REPO_URL"
  # The token goes in on stdin, so it stays out of the host's process list and shell history.
  printf '%s\n' "$RUNNER_TOKEN" | pct exec "$CTID" -- runuser -u gh-runner -- bash -c \
    "read -r token && cd $RUNNER_DIR && ./config.sh --unattended --url $REPO_URL --token \"\$token\" \
       --name $RUNNER_NAME --labels $RUNNER_LABELS --work _work --replace"

  step "Installing the runner service"
  in_ct "cd $RUNNER_DIR && ./svc.sh install gh-runner && ./svc.sh start"
fi

step "Status"
in_ct "cd $RUNNER_DIR && ./svc.sh status | head -n 5"
echo
echo "Done. Pushes to main now deploy through .github/workflows/deploy.yml."
