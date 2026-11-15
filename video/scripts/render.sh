#!/bin/sh
# Render wrapper. Remotion's headless Chrome needs libnss3/libnspr4; on WSL without sudo they are unpacked into
# .local-libs/ (no system change) by `sh scripts/setup-local-libs.sh` (run once).
#
#   sh scripts/render.sh Full out/cancerflow-demo.mp4          # whole video
#   sh scripts/render.sh S5-Recovery out/s5.mp4                # one scene
#   sh scripts/render.sh --still S2-Beginning out/s2.png --frame=300
cd "$(dirname "$0")/.." || exit 1
node scripts/scan-assets.mjs
if [ -d .local-libs/usr/lib/x86_64-linux-gnu ]; then
  export LD_LIBRARY_PATH="$PWD/.local-libs/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
[ -f .local-libs/fonts.conf ] && export FONTCONFIG_FILE="$PWD/.local-libs/fonts.conf"
if [ "$1" = "--still" ]; then shift; exec npx remotion still "$@"; fi
exec npx remotion render "$@"
