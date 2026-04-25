#!/usr/bin/env bash
set -euo pipefail

GATEWAY_BASE="http://127.0.0.1:8090"

repo_path="${1:-}"
if [ -z "$repo_path" ]; then
  echo "Usage: $0 /path/to/repo"
  exit 1
fi

if [ ! -d "$repo_path/.git" ]; then
  echo "Not a git repo: $repo_path"
  exit 1
fi

project_id="$(basename "$repo_path")"
branch="$(git -C "$repo_path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"

curl -s "$GATEWAY_BASE/v1/ingestion/enqueue" \
  -H "Content-Type: application/json" \
  -d "{
    \"project_id\": \"$project_id\",
    \"repo_path\": \"$repo_path\",
    \"branch\": \"$branch\"
  }"
echo
