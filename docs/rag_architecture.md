# RAG Architecture (Phase 4)

## Goals

- OpenAI-compatible embeddings endpoint for clients.
- Project-scoped retrieval with no cross-project bleed.
- Keep shared llama chat backend unchanged.

## Components

1. **`POST /v1/embeddings`**
   - Accepts OpenAI-style body (`input`, optional `model`, optional `user`).
   - Delegates to configured embedding backend (`EMBEDDING_BASE`).

2. **Embedding adapter (`EmbeddingBackend`)**
   - Isolates embedding provider calls behind a small interface.
   - Supports single string and list-of-string embedding requests.

3. **Project RAG service (`ProjectScopedRagService`)**
   - Uses one Qdrant collection per project:
     - `qonduit_rag__<project_id>`
   - Stores payload metadata including `project_id`, `user_id`, and `namespace`.
   - Restricts search to the same project collection and optional namespace.

4. **Chat integration path**
   - During `/v1/chat/completions`, retrieval is optional and controlled by:
     - global `RAG_ENABLED`
     - optional per-project/per-mode flags (`RAG_PROJECT_FLAGS`)
   - Retrieved snippets are injected as plain text in a system message block.

## Environment variables

- `QDRANT_URL` (default: `http://192.168.5.5:6333`)
- `QDRANT_API_KEY` (optional)
- `EMBEDDING_BASE` (default: `http://192.168.5.5:8082`)
- `EMBEDDING_MODEL` (default: `all-MiniLM-L6-v2`)
- `EMBEDDING_VECTOR_SIZE` (default: `384`)
- `RAG_TOP_K` (default: `4`)
- `RAG_ENABLED` (default: `true`)
- `RAG_PROJECT_FLAGS` (JSON map, optional)

Example:

```json
{
  "default": true,
  "mobile_app": true,
  "mode:coding": true,
  "mode:chat": false
}
```

## Setup notes

1. Deploy Qdrant and make it reachable by `QDRANT_URL`.
2. Deploy an OpenAI-compatible embeddings service and set `EMBEDDING_BASE`.
3. Ensure embedding vector dimensions match `EMBEDDING_VECTOR_SIZE`.
4. Restart gateway after env updates.

## Known limitations in this repo environment

- Live Qdrant connectivity and real embedding backend availability are
  environment-dependent and cannot be guaranteed in this repository alone.
- The code includes abstraction + plumbing paths so operators can wire live
  infrastructure without changing gateway business logic.
