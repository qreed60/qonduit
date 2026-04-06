# GitHub-Backed Project RAG Ingestion (Phase 5)

## Goal

Index a local Git repository into project-scoped RAG storage so each project can
retrieve only its own code/docs context.

## CLI entrypoint

Run from `qonduit-memory-gateway` directory:

```bash
python -m app.ingest_repo \
  --project-id my_project \
  --repo-path /path/to/local/repo \
  --branch main
```

## CLI options

- `--project-id` (required): project namespace for strict isolation.
- `--repo-path` (required): local path to cloned Git repository.
- `--branch` (optional): branch metadata override; auto-detected from git if omitted.
- `--include` (optional): comma-separated include globs.
- `--exclude` (optional): comma-separated exclude globs.
- `--chunk-size` (optional): chunk size in characters (default `1200`).
- `--chunk-overlap` (optional): overlap in characters (default `200`).

## Stored metadata per chunk

Each chunk stores:

- `project_id`
- `repo_path`
- `branch`
- `file_path`
- `chunk_index`
- `commit_sha`
- `source=repo_ingest`

## Isolation model

- One Qdrant collection per project: `qonduit_rag__<project_id>`.
- Optional namespace by branch (`namespace=<branch>`).
- No cross-project retrieval path is used.

## Stale chunk cleanup

After each ingest run, stale chunks from removed files are deleted where practical
for the same `(project_id, repo_path, branch, source=repo_ingest)` scope.

## Webhook automation scaffold

A stub endpoint exists for contract-based automation:

- `POST /internal/webhooks/github`

It returns a deterministic ingest command payload for an external worker/CI job.
It does **not** run git/pull/ingest directly.

## Suggested operator flow

1. Clone or pull the target GitHub repo locally.
2. Run `python -m app.ingest_repo ...`.
3. Send chat requests with the same `project_id` (header or configured mapping).
4. Optionally call webhook stub from your webhook receiver to standardize ingest job payloads.
