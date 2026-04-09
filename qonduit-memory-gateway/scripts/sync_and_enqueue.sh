#!/usr/bin/env bash
set -euo pipefail

log() {
  echo "[sync_and_enqueue] $*"
}

REPO_PATH=""
BRANCH=""
PROJECT_ID=""
GATEWAY_URL="http://127.0.0.1:8090/v1/ingestion/enqueue"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-path)
      REPO_PATH="$2"
      shift 2
      ;;
    --branch)
      BRANCH="$2"
      shift 2
      ;;
    --project-id)
      PROJECT_ID="$2"
      shift 2
      ;;
    --gateway-url)
      GATEWAY_URL="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${REPO_PATH}" || -z "${BRANCH}" || -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 --repo-path <path> --branch <branch> --project-id <project_id> [--gateway-url <url>]" >&2
  exit 2
fi

if [[ ! -d "${REPO_PATH}" ]]; then
  echo "Repo path not found: ${REPO_PATH}" >&2
  exit 1
fi

log "Repo=${REPO_PATH} Branch=${BRANCH} Project=${PROJECT_ID}"
log "Fetching latest refs"
git -C "${REPO_PATH}" fetch --all

log "Checking out branch ${BRANCH}"
git -C "${REPO_PATH}" checkout "${BRANCH}"

log "Pulling latest with ff-only"
git -C "${REPO_PATH}" pull --ff-only

PAYLOAD=$(cat <<EOF
{"project_id":"${PROJECT_ID}","repo_path":"${REPO_PATH}","branch":"${BRANCH}"}
EOF
)

log "Calling gateway enqueue API: ${GATEWAY_URL}"
HTTP_CODE=$(curl -sS -o /tmp/qonduit_enqueue_response.json \
  -w "%{http_code}" \
  -X POST "${GATEWAY_URL}" \
  -H "Content-Type: application/json" \
  -d "${PAYLOAD}")

if [[ "${HTTP_CODE}" -lt 200 || "${HTTP_CODE}" -ge 300 ]]; then
  log "Enqueue failed: HTTP ${HTTP_CODE}"
  cat /tmp/qonduit_enqueue_response.json >&2 || true
  exit 1
fi

log "Enqueue succeeded: HTTP ${HTTP_CODE}"
cat /tmp/qonduit_enqueue_response.json
