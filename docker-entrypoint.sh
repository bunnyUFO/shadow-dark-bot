#!/usr/bin/env bash
# Container entrypoint: ensure /app/data is writable by the bot user, then
# drop privileges and exec the command. This makes the container resilient
# to bind-mounted data dirs that arrive with arbitrary host ownership.
set -e

mkdir -p /app/data
chown -R bot:bot /app/data

exec gosu bot "$@"
