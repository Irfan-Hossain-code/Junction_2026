#!/usr/bin/env bash
# Build both front-ends against the public URL, then deploy everything to Modal.
# Usage (from anywhere, inside WSL/Linux):  bash deploy/deploy.sh
# Override the URL if your Modal workspace name differs:  CANCERFLOW_URL=https://... bash deploy/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

MODAL="${MODAL:-.venv/bin/modal}"
[ -x "$MODAL" ] || MODAL=modal
[ -s "$HOME/.nvm/nvm.sh" ] && source "$HOME/.nvm/nvm.sh" >/dev/null

if [ -z "${CANCERFLOW_URL:-}" ]; then
  WORKSPACE="$("$MODAL" profile current)"
  CANCERFLOW_URL="https://${WORKSPACE}--cancerflow.modal.run"
fi
echo "==> Public URL: $CANCERFLOW_URL"

echo "==> Building care app"
(cd care-app && npm ci --no-audit --no-fund && VITE_API="$CANCERFLOW_URL" npx vite build)

echo "==> Building national twin UI"
(cd twin-ui && npm ci --no-audit --no-fund && VITE_API="$CANCERFLOW_URL" npx vite build --base=/app/twin/)

echo "==> Deploying to Modal"
"$MODAL" deploy deploy/modal_app.py

echo
echo "Done. Open: $CANCERFLOW_URL"
echo "  care app:  $CANCERFLOW_URL/app/care/"
echo "  twin:      $CANCERFLOW_URL/app/twin/"
