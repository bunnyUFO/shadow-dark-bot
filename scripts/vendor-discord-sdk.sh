#!/usr/bin/env bash
# Rebuilds web/static/assets/vendor/discord-sdk.js: Discord's Embedded App SDK
# bundled into one ES module, so the frontend keeps its no-build-step setup.
# Usage: scripts/vendor-discord-sdk.sh [sdk-version]
set -euo pipefail
VERSION="${1:-2.5.0}"
OUT="$(cd "$(dirname "$0")/.." && pwd)/src/shadowdark_bot/web/static/assets/vendor/discord-sdk.js"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
npm init -y >/dev/null
npm i --silent "@discord/embedded-app-sdk@$VERSION" esbuild-wasm@0.25.10
echo 'export { DiscordSDK } from "@discord/embedded-app-sdk";' > entry.mjs
npx esbuild entry.mjs --bundle --format=esm --minify --platform=browser \
  --target=es2020 --legal-comments=eof --outfile=bundle.js
{
  echo "// @discord/embedded-app-sdk $VERSION (MIT), bundled by scripts/vendor-discord-sdk.sh. Do not edit."
  cat bundle.js
} > "$OUT"
cp node_modules/@discord/embedded-app-sdk/LICENSE.md "${OUT%.js}.LICENSE.md"
echo "Wrote $OUT"
