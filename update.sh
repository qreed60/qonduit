#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/opt/qonduit-repo"
BRANCH="memory-gateway"

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

echo "==> Rebuilding qonduit-memory-gateway"
cd /opt/qonduit-memory-gateway
sudo docker build -t qonduit-memory-gateway .

echo "==> Rebuilding qonduit-embedding-service"
cd /opt/qonduit-embedding-service
sudo docker build -t qonduit-embedding-service .

echo "==> Restarting services"
sudo systemctl restart qonduit-embedding-service.service
sudo systemctl restart qonduit-memory-gateway.service
sudo systemctl restart qonduit-router-api.service

echo "==> Service status"
sudo systemctl status qonduit-embedding-service.service --no-pager
sudo systemctl status qonduit-memory-gateway.service --no-pager
sudo systemctl status qonduit-router-api.service --no-pager

echo "==> Done"
