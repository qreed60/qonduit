#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/opt/qonduit-repo"
BRANCH="memory-gateway"

echo "=================================================="
echo "  Qonduit Update — $BRANCH"
echo "=================================================="
echo ""

echo "==> Moving to repo: $REPO_DIR"
cd "$REPO_DIR"

echo "==> Current branch:"
git branch --show-current

echo "==> Fetching latest from origin/$BRANCH"
git fetch origin "$BRANCH"

echo "==> Switching to $BRANCH"
git checkout "$BRANCH"

echo "==> Pulling latest changes"
git pull --ff-only origin "$BRANCH"

echo ""
echo "==> [1/3] Rebuilding qonduit-memory-gateway"
cd /opt/qonduit-memory-gateway
sudo docker build -t qonduit-memory-gateway .

echo "==> [2/3] Rebuilding qonduit-embedding-service"
cd /opt/qonduit-embedding-service
sudo docker build -t qonduit-embedding-service .

echo "==> [3/3] Restarting Memory Gateway services"
sudo systemctl restart qonduit-embedding-service.service
sudo systemctl restart qonduit-memory-gateway.service

echo "==> Service status (gateway)"
sudo systemctl status qonduit-embedding-service.service --no-pager
sudo systemctl status qonduit-memory-gateway.service --no-pager

echo ""
echo "=================================================="
echo "  Router API Deployment"
echo "=================================================="

# Deploy router API files (non-fatal if files missing)
if [[ -x "$REPO_DIR/scripts/deploy_router_api.sh" ]]; then
    "$REPO_DIR/scripts/deploy_router_api.sh" "$REPO_DIR" || {
        echo ""
        echo "[WARN] Router API deployment encountered issues."
        echo "       Check logs above for details."
    }
else
    echo "[WARN] scripts/deploy_router_api.sh not found or not executable."
    echo "       Router API files not deployed automatically."
fi

echo ""
echo "==> Final service status"
sudo systemctl status qonduit-embedding-service.service --no-pager
sudo systemctl status qonduit-memory-gateway.service --no-pager
sudo systemctl status qonduit-router-api.service --no-pager

echo ""
echo "==> Done"
