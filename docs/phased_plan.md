# Qonduit Memory Gateway — Phased Implementation Plan

## Scope and status

This document tracks the incremental modernization of the FastAPI memory
gateway for better OpenAI compatibility, strict project isolation, and
coding-focused memory quality.

- **Phase 0 (Analyze and Document):** ✅ Completed on 2026-04-05.
- **Phase 1 (OpenAI-Compatible Chat Baseline):** ✅ Completed on 2026-04-05.
- **Phases 2-7:** ⏳ Not started in code (deferred intentionally pending review).

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

### Planned

- Introduce internal `project_id` with strict storage isolation.
- Add project resolution via header/request/model alias hooks.
- Move storage layout to `project_id + conversation_id` namespace.
- Include migration handling for older flat conversation files.
- Add `docs/project_isolation.md`.

### Risks

- Migration complexity and potential accidental cross-project access if
  fallback rules are ambiguous.

---

## Phase 3 — Coding mode / technical memory

### Planned

- Add explicit `chat` vs `coding` modes and precedence rules.
- Mode-aware prompts and summarization/trimming.
- Preserve critical technical details in coding summaries.
- Add validation for mode-aware behavior and persistence.

### Risks

- Overly aggressive preservation can increase token pressure and latency.

---

## Phase 4 — OpenAI-compatible embeddings + RAG scaffold

### Planned

- Add `POST /v1/embeddings` OpenAI-compatible endpoint.
- Introduce RAG service abstraction with project-scoped retrieval.
- Add config for Qdrant and embeddings.
- Integrate optional retrieval into chat completion prompt assembly.
- Add `docs/rag_architecture.md`.

### Risks

- External dependency variability (Qdrant/embedding availability).

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
