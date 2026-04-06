# Qonduit Memory Gateway — Phased Implementation Plan

## Scope and status

This document tracks the incremental modernization of the FastAPI memory
gateway for better OpenAI compatibility, strict project isolation, and
coding-focused memory quality.

- **Phase 0 (Analyze and Document):** ✅ Completed on 2026-04-05.
- **Phase 1 (OpenAI-Compatible Chat Baseline):** ✅ Completed on 2026-04-05.
- **Phase 2 (Project-scoped memory isolation):** ✅ Completed on 2026-04-06.
- **Phase 3 (Coding mode / technical memory):** ✅ Completed on 2026-04-06.
- **Phase 4 (Embeddings + RAG scaffold):** ✅ Completed on 2026-04-06.
- **Phases 5-7:** ⏳ Not started in code (deferred intentionally pending review).

---

## Phase 0 — Analysis of current baseline

### Current API contract (before Phase 1 changes)

- `POST /v1/chat/completions` expected a gateway-specific body that effectively
  depended on `conversation_id` and `context_size` behavior.
- `GET /v1/models` proxied upstream llama-compatible `/v1/models`.
- `GET /health` returned a basic service health payload.
- Additional gateway-specific RAG and upload endpoints existed under `/rag/*`.

### Current memory flow (before Phase 1 changes)

1. Load per-conversation state from local JSON storage.
2. Merge prior recent messages with incoming messages.
3. Trim to token budget.
4. Summarize overflowed older messages into a rolling summary.
5. Build prompt from system prompt + rolling summary + retrieved RAG snippets +
   trimmed recent messages.
6. Send to upstream llama backend.
7. Persist updated summary and recent context.

### Compatibility gaps vs OpenAI-style clients (before Phase 1 changes)

- Conversation/session identity depended heavily on non-standard gateway fields
  (`conversation_id`) or custom header naming.
- Context sizing was not resolved in a way that gracefully reused historical
  session settings without caller-provided fields.
- Environment config was mostly hardcoded in code (`LLAMA_BASE`, data paths,
  defaults), making deployment and client compatibility harder.
- `/v1/models` lacked robust fallback behavior for clients that refresh models
  aggressively and expect OpenAI-like response shapes even during upstream
  outages.

### Coding-task weaknesses (before Phase 1 changes)

- Single static system prompt for all modes.
- No explicit `mode` plumbing for chat vs coding.
- Summarization and trimming logic not yet mode-aware for preserving technical
  artifacts (file paths, stack traces, commands).

---

## Cross-phase assumptions

- Existing llama-compatible backend remains reachable and continues to support
  `/v1/chat/completions` and `/v1/models`.
- Existing JSON conversation persistence remains acceptable as the base storage
  primitive through early phases.
- Backward compatibility with legacy clients is maintained whenever practical.
- No breaking transport/proxy changes are introduced without explicit phase
  coverage and docs.

## Cross-phase risks

- Session derivation changes can alter how old clients “thread” conversations if
  they did not supply stable identity fields.
- New project-scoped storage (later phases) requires careful migration handling
  to avoid orphaning old conversation files.
- Optional RAG integration can impact latency and token budget quality if not
  carefully constrained.
- Model aliasing must avoid confusion between displayed alias IDs and true
  upstream llama model IDs.

## Environment dependencies and likely manual setup

- Llama-compatible chat backend endpoint.
- Embedding backend endpoint.
- Qdrant instance and credentials.
- Reverse proxy, DNS/subdomain, and client endpoint routing.
- Optional webhook and repo sync integrations.

---

## Phase 1 — OpenAI-compatible chat baseline

### Goal

Make the gateway usable by OpenAI-compatible clients without requiring
non-standard body fields.

### Implemented changes (completed)

- `POST /v1/chat/completions` now works with standard OpenAI-style request
  bodies (`model`, `messages`, optional `temperature`, `max_tokens`, `user`).
- Legacy compatibility retained for `conversation_id` and `context_size` if
  still sent by older clients.
- Conversation/session identity precedence now:
  1. `X-Conversation-ID` (or legacy `X-Qonduit-Conversation`) header
  2. request body `user`
  3. legacy request body `conversation_id`
  4. deterministic fallback session ID
- `context_size` resolution now:
  1. request `context_size` if provided
  2. stored `last_context_size` for that conversation
  3. `DEFAULT_CONTEXT_SIZE` environment default
- `/v1/models` kept and improved with better error handling and OpenAI-like
  fallback model-list response if upstream is temporarily unreachable.
- `/health` preserved.
- Environment configuration added/expanded:
  - `LLAMA_BASE`
  - `DEFAULT_CONTEXT_SIZE`
  - `DEFAULT_MODE`
  - `GATEWAY_DATA_DIR`
- Logging and upstream error handling improved for chat/model proxy calls.

### Deferred within Phase 1

- Full model alias management is deferred to later phases.
- Dedicated pytest suite deferred; added a lightweight validation script in
  this phase.

---

## Phase 2 — Project-scoped memory isolation

### Status

✅ Completed in code on 2026-04-06.

### Implemented

- Added `project_id` support end-to-end in gateway request handling and storage.
- Added project binding precedence hooks:
  1. `X-Project-ID` header
  2. request `project_id`
  3. model alias mapping (`MODEL_ALIAS_CONFIG`)
  4. host binding hooks (`PROJECT_HOST_BINDINGS`)
  5. safe default namespace (`DEFAULT_PROJECT_ID`)
- Migrated storage layout to `.../conversations/<project_id>/<conversation_id>.json`.
- Added transparent migration support from legacy flat conversation files.
- Persisted `project_id`, `conversation_id`, `last_context_size`, `last_mode`, and
  `metadata` fields for future project RAG integration.
- Added dedicated documentation in `docs/project_isolation.md`.

### Deferred

- Full project alias registry and endpoint-specific routing controls are left for
  later native-client phases.

---

## Phase 3 — Coding mode / technical memory

### Status

✅ Completed in code on 2026-04-06.

### Implemented

- Added explicit `chat` / `coding` mode handling with precedence:
  1. request `mode`
  2. `X-Gateway-Mode` header
  3. model alias default mode
  4. per-project mode map (`PROJECT_DEFAULT_MODE_MAP`)
  5. env default (`PROJECT_DEFAULT_MODE` then `DEFAULT_MODE`)
- Added separate system prompt behavior for coding mode.
- Replaced generic coding summarization with deterministic technical summaries
  that preserve file paths, symbols, API endpoints, commands, errors/logs,
  active task, decisions, constraints, and unresolved issues.
- Expanded coding-mode recent-message retention window.
- Added technical-message protection during trimming (code fences, errors, file
  names/extensions, constraints, commands).
- Added dedicated documentation in `docs/coding_mode.md`.
- Added a validation helper script for project persistence + mode behavior.

### Deferred

- Additional weighting/ranking for technical snippets can be expanded in later
  phases if needed.

---

## Phase 4 — OpenAI-compatible embeddings + RAG scaffold

### Status

✅ Completed in code on 2026-04-06.

### Implemented

- Added `POST /v1/embeddings` with OpenAI-compatible request/response passthrough behavior.
- Added a clean adapter (`EmbeddingBackend`) and project-scoped RAG service abstraction (`ProjectScopedRagService`).
- Added environment-driven config for:
  - `QDRANT_URL`
  - `QDRANT_API_KEY`
  - `EMBEDDING_BASE`
  - `EMBEDDING_MODEL`
  - `RAG_TOP_K`
  - `RAG_ENABLED`
- Enforced project-scoped retrieval by using one Qdrant collection per project namespace.
- Integrated optional retrieval into chat prompt assembly with mode/project-aware enable checks.
- Added `docs/rag_architecture.md` describing architecture and setup.

### Deferred

- Full live Qdrant verification depends on environment-provided infrastructure; service abstraction and setup docs are included for operators.

---

## Phase 5 — Project repo ingestion for GitHub-backed projects

### Planned

- Build ingestion CLI/module for project repositories.
- Chunk/embed/upsert repo data with strict project metadata.
- Add stale chunk cleanup strategy where practical.
- Add `docs/github_project_rag.md` and webhook contract/stub if needed.

### Risks

- Repository scale and chunking strategy can impact indexing time/cost.

---

## Phase 6 — Native client compatibility strategy

### Planned

- Add model alias mapping for project-bound behavior.
- Add endpoint/provider binding hooks for future multi-endpoint routing.
- Ensure shared llama backend compatibility while exposing client-friendly
  alias models.
- Add `docs/native_client_compatibility.md`.

### Risks

- Alias-to-upstream mapping drift if not centrally validated.

---

## Phase 7 — Cleanup, testing, and operator docs

### Planned

- Refactor for readability as needed.
- Update setup and manual-steps documentation.
- Expand tests and operator checklist.

### Risks

- Documentation can lag code if not maintained phase-by-phase.
