# Ingestion Phase 1 (Queue + Status Worker)

This phase adds a sequential background ingestion worker to the gateway.

## Persistent files

Inside `GATEWAY_DATA_DIR` (default `/app/data`):

- `ingestion_queue.json`  
  Persistent FIFO queue of project ingest jobs.
- `ingestion_status.json`  
  Latest status per `project_id`.
- `ingestion.log`  
  Dedicated ingestion lifecycle log file.

## API endpoints

- `POST /v1/ingestion/enqueue`
- `GET /v1/ingestion/status`
- `GET /v1/ingestion/status/{project_id}`

Example enqueue payload:

```json
{
  "project_id": "android-qonduit",
  "repo_path": "/opt/projects/android-qonduit",
  "branch": "memory-gateway"
}
```

If `repo_path` or `branch` are omitted, the gateway tries to derive them from
auto-discovered repos in `PROJECTS_ROOT`.

## Status states

- `idle`
- `queued`
- `running`
- `success`
- `failed`

Each project status includes:

- `project_id`
- `state`
- `repo_path`
- `branch`
- `last_started_at`
- `last_finished_at`
- `last_error`
- `files_scanned`
- `chunks_embedded`
- `chunks_written`
- `current_step`
- `current_file`
- `last_progress_at`
- `skipped_files`

## Timeout + skip behavior

- `INGESTION_STALL_TIMEOUT_SECONDS` (default `600`):
  - if no heartbeat progress update occurs longer than this timeout,
    the running job is auto-failed (`state=failed`, `current_step=failed`).
- `INGESTION_FILE_TIMEOUT_SECONDS` (default `120`):
  - each file is processed in a worker-thread boundary with timeout; timed-out files are skipped and
    ingestion continues.
- `INGESTION_EMBED_TIMEOUT_SECONDS` and `INGESTION_QDRANT_TIMEOUT_SECONDS`:
  - chunk-level embed/write guardrails; these are clamped below file timeout.
  - timed-out chunks are skipped so one pathological chunk does not wedge a job.
- Max file size safeguard:
  - `INGESTION_MAX_FILE_BYTES` controls max text file size;
    files above this limit are skipped during scanning.
- Generated/minified defaults:
  - ingestion excludes common generated/minified/vendor paths by default.

## Recovery behavior

- Failed/stalled jobs are re-enqueueable.
- If an operator wants immediate recovery control, use:
  - `POST /v1/ingestion/fail/{project_id}`
  - optional body: `{"reason":"manual reset"}`

## Manual helper scripts

From repo root:

```bash
python qonduit-memory-gateway/scripts/enqueue_ingestion.py \
  --project-id android-qonduit \
  --repo-path /opt/projects/android-qonduit \
  --branch memory-gateway
```

```bash
python qonduit-memory-gateway/scripts/print_ingestion_status.py
```

```bash
python qonduit-memory-gateway/scripts/print_ingestion_status.py \
  --project-id android-qonduit
```

## Server/operator steps after code updates

1. Rebuild/restart the gateway container so startup hooks load the worker.
2. Confirm worker startup:
   - API: `GET /v1/ingestion/status`
   - Log: `/app/data/ingestion.log`
3. Enqueue projects via API or helper script.

No webhook automation is included in this phase.
