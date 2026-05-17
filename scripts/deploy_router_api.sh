#!/usr/bin/env bash
set -euo pipefail

REPO="/opt/qonduit-repo"
OPT_DIR="/opt"

echo "=================================================="
echo "  Router API Deployment"
echo "=================================================="
echo "Deploying router API symlinks from: $REPO"

cd "$REPO" || exit 1

echo
echo "==> Verifying required router entrypoint"
if [ ! -f "$REPO/qonduit_router_api.py" ]; then
  echo "[ERROR] Missing router entrypoint: $REPO/qonduit_router_api.py" >&2
  exit 1
fi

echo
echo "==> Symlinking all top-level Qonduit backend Python modules"
for f in "$REPO"/qonduit_*.py; do
  [ -e "$f" ] || continue
  base="$(basename "$f")"
  echo "  /opt/$base -> $f"
  ln -sfn "$f" "$OPT_DIR/$base"
done

echo
echo "==> Symlinking update script"
if [ -f "$REPO/update.sh" ]; then
  ln -sfn "$REPO/update.sh" "$OPT_DIR/update.sh"
fi

echo
echo "==> Verifying active symlinks"
ls -l /opt/qonduit_*.py /opt/update.sh 2>/dev/null || true

echo
echo "==> Python compile check"
python3 -m py_compile \
  "$REPO/qonduit_router_api.py" \
  "$REPO/qonduit_router_api_slots.py" \
  "$REPO/qonduit_slots.py" \
  "$REPO/qonduit_docker_helpers.py"

echo
echo "==> Restarting router API"
systemctl restart qonduit-router-api.service

sleep 2

echo
echo "==> Router API health"
curl -sS http://127.0.0.1:5001/api/v1/qonduit-router/health | python3 -m json.tool

echo
echo "==> Router API deployment complete"
