# Gateway Setup

## 1) Configure environment

Create `qonduit-memory-gateway/.env` from `.env.example` and set values for your environment.

## 2) Start dependencies

- llama-compatible chat backend (for `/v1/chat/completions`, `/v1/models`)
- embedding backend (for `/v1/embeddings`)
- Qdrant (for project-scoped retrieval)

## 3) Start gateway

From `qonduit-memory-gateway`:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8081
```

## 4) Validate core behavior

```bash
python scripts/validate_phase1.py
python scripts/validate_phase23.py
python scripts/validate_phase4.py
python scripts/validate_phase5.py
```

## 5) Optional: repo ingest

```bash
python -m app.ingest_repo --project-id my_project --repo-path /path/to/repo --branch main
```
