#!/usr/bin/env bash
set -euo pipefail

/opt/qonduit-memory-gateway/scripts/ingest_changed_repo.sh >> /opt/qonduit-memory-gateway/state/reconcile.log 2>&1
