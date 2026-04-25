#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="qonduit-memory-gateway"
PROJECTS_ROOT="/opt/projects"

run_ingest() {
  local repo_path="$1"

  if [ ! -d "$repo_path/.git" ]; then
    echo "Skipping non-git directory: $repo_path"
    return 0
  fi

  local project_id
  project_id="$(basename "$repo_path")"

  local branch
  branch="$(git -C "$repo_path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"

  echo "=== ingest start project_id=$project_id repo_path=$repo_path branch=$branch ==="

  docker exec "$CONTAINER_NAME" python3 -m app.ingest_repo \
    --project-id "$project_id" \
    --repo-path "$repo_path" \
    --branch "$branch"

  echo "=== ingest complete project_id=$project_id ==="
}

if [ "${1:-}" != "" ]; then
  run_ingest "$1"
else
  find "$PROJECTS_ROOT" -mindepth 1 -maxdepth 2 -type d -name ".git" -print0 | while IFS= read -r -d '' gitdir; do
    repo_path="$(dirname "$gitdir")"
    run_ingest "$repo_path"
  done
fi
