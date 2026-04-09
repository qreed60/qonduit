# GitHub Push Webhook Auto-Ingestion (Phase 3)

This phase adds a lightweight host-side webhook receiver that validates GitHub
signatures, syncs the local repo, and enqueues ingestion through the existing
gateway queue worker.

## Components

- Receiver app: `qonduit-memory-gateway/host_webhook/receiver.py`
- Sync/enqueue script: `qonduit-memory-gateway/scripts/sync_and_enqueue.sh`
- Existing gateway enqueue endpoint: `POST /v1/ingestion/enqueue`

## Environment variables (receiver)

- `GITHUB_WEBHOOK_SECRET` (required)
- `PROJECTS_ROOT` (default: `/opt/projects`)
- `GATEWAY_ENQUEUE_URL` (default: `http://127.0.0.1:8090/v1/ingestion/enqueue`)
- `SYNC_ENQUEUE_SCRIPT` (optional override path)
- `GITHUB_REPO_PATH_OVERRIDES` (optional JSON map)
  - Example: `{"android-qonduit":"/opt/projects/android-qonduit"}`
- `GITHUB_PROJECT_ID_OVERRIDES` (optional JSON map)
  - Example: `{"org/android-qonduit":"android-qonduit"}`

## Run receiver manually

```bash
cd /opt/qonduit-memory-gateway
export GITHUB_WEBHOOK_SECRET='replace-me'
export PROJECTS_ROOT='/opt/projects'
export GATEWAY_ENQUEUE_URL='http://127.0.0.1:8090/v1/ingestion/enqueue'
python -m uvicorn host_webhook.receiver:app \
  --host 0.0.0.0 --port 9010
```

## GitHub webhook settings

- Payload URL: `http://<host>:9010/github/webhook`
- Content type: `application/json`
- Secret: same value as `GITHUB_WEBHOOK_SECRET`
- Events: **Just the push event**

## Local verification

1. Build payload file:

```json
{"ref":"refs/heads/main","repository":{"name":"android-qonduit","full_name":"org/android-qonduit"}}
```

2. Compute signature and send:

```bash
SECRET='replace-me'
PAYLOAD_FILE='/tmp/github_push.json'
SIG="sha256=$(openssl dgst -sha256 -hmac \"$SECRET\" \"$PAYLOAD_FILE\" | sed 's/^.* //')"
curl -s -X POST http://127.0.0.1:9010/github/webhook \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -H "X-Hub-Signature-256: ${SIG}" \
  --data-binary "@${PAYLOAD_FILE}" | jq
```

The receiver should return quickly and spawn `sync_and_enqueue.sh`, which
performs git sync and then calls gateway enqueue.
