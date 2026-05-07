# Qonduit RAG Backend API Inventory

## 1. Executive Summary

- Backend RAG functionality **does exist** in the `qonduit-memory-gateway` service.
- The current backend implements:
  - Project-scoped Qdrant collections named `qonduit_rag__{project_id}`.
  - An OpenAI-compatible embedding adapter.
  - Repo/directory ingestion through an internal queue and background worker.
  - Manual/test document ingestion.
  - Upload-based document ingestion for common text/document/spreadsheet formats.
  - Basic namespace-style collection markers, listing, creation, and deletion under `/rag/*`.
  - Search/retrieval through `/rag/test-search` and automatic RAG insertion in `/v1/chat/completions`.
  - Ingestion status/debug endpoints suitable for read-only web console diagnostics.
- What is missing or weak for a web console RAG MVP:
  - No first-class Qdrant collection stats/detail endpoint.
  - No document/source list endpoint.
  - No chunk detail endpoint.
  - No ingestion cancellation/retry/clear endpoint.
  - No explicit Qdrant or embedding health endpoint on the memory gateway.
  - No stable citations field returned from chat completions.
  - `/rag/test-search` exists, but its name suggests diagnostics rather than a production search API.
- The current API is **enough for a read-only RAG visibility MVP** focused on service health, models, ingestion queue/status, known namespace collections, and raw diagnostics.
- Backend changes are **needed before full RAG management**, especially for document-level inventory, source deletion, collection stats, health checks, job control, and stable frontend-oriented search/citation contracts.

## 2. Backend Architecture Overview

### API framework and entrypoints

- Main RAG/chat API framework: **FastAPI**.
- Main server entrypoint: `qonduit-memory-gateway/app/main.py`.
- Embedding service framework: **FastAPI**.
- Embedding service entrypoint: `qonduit-embedding-service/main.py`.
- Separate model-router API framework: **Flask** in `qonduit_router_api.py`; this file primarily manages local model/router operations, not RAG storage.
- GitHub webhook receiver: **FastAPI** in `qonduit-memory-gateway/host_webhook/receiver.py`.

### Route files

- `qonduit-memory-gateway/app/main.py`
  - `/health`
  - `/v1/models`, `/models`
  - `/v1/embeddings`
  - `/v1/ingestion/status`
  - `/v1/ingestion/status/{project_id}`
  - `/v1/ingestion/debug`
  - `/v1/ingestion/enqueue`
  - `/rag/test-ingest`
  - `/rag/test-search`
  - `/rag/collections`
  - `/rag/collections/create`
  - `/rag/collections/delete`
  - `/rag/upload`
  - `/internal/webhooks/github`
  - `/v1/chat/completions`, `/chat/completions`
- `qonduit-embedding-service/main.py`
  - `/health`
  - `/v1/embeddings`
- `qonduit-memory-gateway/host_webhook/receiver.py`
  - `/health`
  - `/github/webhook`

### Qdrant/store code

- Qdrant client and RAG service: `qonduit-memory-gateway/app/rag.py`.
- Key functions/classes:
  - `EmbeddingBackend`
  - `ProjectScopedRagService`
  - `project_collection_name(project_id)`
  - `ensure_collection(project_id)`
  - `add_document(...)`
  - `search_documents(...)`
  - `list_collections(...)`
  - `create_collection_marker(...)`
- Conversation state store: `qonduit-memory-gateway/app/store.py`.
- Ingestion status/queue/history store: `IngestionStore` in `qonduit-memory-gateway/app/ingestion.py`.

### Embedding client code

- Gateway embedding client: `EmbeddingBackend` in `qonduit-memory-gateway/app/rag.py`.
- It posts to `{EMBEDDING_BASE}/v1/embeddings` and expects OpenAI-style `data[].embedding` vectors.
- Local embedding service: `qonduit-embedding-service/main.py`, using `SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")`.

### Ingestion code

- Queue/worker manager: `IngestionManager` in `qonduit-memory-gateway/app/ingestion.py`.
- Repository scanner/chunker/writer: `qonduit-memory-gateway/app/ingest_repo.py`.
- CLI ingestion script entrypoint: `python -m app.ingest_repo` via `main()` in `ingest_repo.py`.
- Enqueue helper script: `qonduit-memory-gateway/scripts/enqueue_ingestion.py`.
- Status helper script: `qonduit-memory-gateway/scripts/print_ingestion_status.py`.
- GitHub webhook receiver can spawn `scripts/sync_and_enqueue.sh` through `host_webhook/receiver.py`.

### Chat completion path

- Chat endpoint handler: `chat(req: GatewayChatRequest, request: Request)` in `qonduit-memory-gateway/app/main.py`.
- Chat request model: `GatewayChatRequest` in `main.py`.
- RAG decision function: `should_enable_rag(project_id, mode, alias, binding)` in `main.py`.
- Retrieval function call: `search_documents(...)` from `app.rag`.
- Context assembly helpers:
  - `sort_rag_results_deterministically(...)`
  - `build_bounded_rag_chunks(...)`
  - `estimate_tokens(...)` from `budget.py`
- Summary path:
  - `summarize_messages(...)` in `qonduit-memory-gateway/app/summarizer.py`.
  - Summaries are conversation memory, not document summaries.

### How RAG integrates with chat

- RAG is automatic for chat requests when:
  - `RAG_ENABLED` is true,
  - `should_enable_rag(...)` returns true for the resolved project/mode/alias/binding,
  - the request has a latest non-empty user message.
- The latest user text is embedded and searched in the project collection.
- Results are bounded and inserted as a system message titled `Relevant retrieved knowledge:` before the current user message.
- Chat does not return a dedicated citations/sources array.

### How `project_id` maps to collections

- `project_id` is normalized to lowercase alphanumeric plus `-` and `_`.
- Qdrant collection name rule: `qonduit_rag__{project_id}`.
- Examples implied by the code:
  - `default` -> `qonduit_rag__default`
  - `android-qonduit` -> `qonduit_rag__android-qonduit`
  - `chatgpt` -> `qonduit_rag__chatgpt`
- Older constant `COLLECTION_NAME = "qonduit_rag"` is used as a prefix/base when routes assemble `f"{COLLECTION_NAME}__{project_id}"`.

### Where state/status is stored

- Conversation state: JSON files below `${GATEWAY_DATA_DIR}/conversations/{project_id}/{conversation_id}.json`.
- Ingestion status: `${GATEWAY_DATA_DIR}/ingestion_status.json`.
- Ingestion queue: `${GATEWAY_DATA_DIR}/ingestion_queue.json`.
- Ingestion history: `${GATEWAY_DATA_DIR}/ingestion_history.json`.
- Ingestion log: `${GATEWAY_DATA_DIR}/ingestion.log`.
- Uploaded files: hardcoded `/mnt/models/qonduit_uploads/{user_id}/{collection}/...`.
- Vectors/chunk payloads: Qdrant.

### Ingestion execution model

- `/v1/ingestion/enqueue` is asynchronous from the API caller perspective: it writes a queue entry and returns.
- `IngestionManager.start()` creates an `asyncio` background task.
- `_worker_loop()` processes queued jobs sequentially, one at a time.
- Repository ingestion itself is asynchronous and per-file timeout bounded.
- External-script ingestion also exists through CLI and GitHub webhook helper flows.
- `/rag/upload` is synchronous: it saves, extracts, chunks, embeds, and writes before responding.

## 3. Environment and Configuration

| Name | Source file | Default value | Purpose | Required? | Example |
|---|---|---:|---|---|---|
| `LLAMA_BASE` | `app/main.py`, `app/summarizer.py`, `.env.example` | `http://127.0.0.1:8080` in main, `http://192.168.5.5:8080` in summarizer | Upstream OpenAI-compatible chat/model backend | Required for chat/models proxy | `http://127.0.0.1:8080` |
| `GATEWAY_DATA_DIR` | `app/main.py`, `app/store.py`, `.env.example` | `/app/data` | Conversation state and ingestion status/queue/history/log files | Optional with default | `/app/data` |
| `EFFECTIVE_CONTEXT_SIZE` | `app/main.py` | `131072` minimum `1024` | Effective chat context window used by gateway budget logic | Optional | `131072` |
| `DEFAULT_CONTEXT_SIZE` | `.env.example`, docs only | `65536` | Older/documented context size name; current main code uses `EFFECTIVE_CONTEXT_SIZE` | Optional/legacy | `65536` |
| `DEFAULT_MAX_TOKENS` | `app/main.py` | `4096` | Default output token cap when client override disabled/missing | Optional | `4096` |
| `ALLOW_CLIENT_CONTEXT_SIZE` | `app/main.py` | `false` | Allows request `context_size` to override effective context | Optional | `true` |
| `ALLOW_CLIENT_MAX_TOKENS` | `app/main.py` | `true` | Allows request `max_tokens` to influence upstream request | Optional | `true` |
| `DEFAULT_MODE` | `app/main.py`, `.env.example` | `chat` | Fallback mode, `chat` or `coding` | Optional | `chat` |
| `DEFAULT_PROJECT_ID` | `app/main.py`, `app/store.py`, `.env.example` | `default` | Fallback project namespace | Optional | `default` |
| `PROJECT_DEFAULT_MODE` | `app/main.py`, `.env.example` | `DEFAULT_MODE` | Fallback mode by project flow | Optional | `chat` |
| `MODEL_ALIAS_CONFIG` | `app/main.py`, `.env.example` | `{}` | Maps model aliases to target model/project/mode/RAG metadata | Optional | `{"android-qonduit":{"model":"gpt-oss:20b","project_id":"android-qonduit","rag_enabled":true}}` |
| `PROJECT_DEFAULT_MODE_MAP` | `app/main.py`, `.env.example` | `{}` | Per-project default mode map | Optional | `{"android-qonduit":"coding"}` |
| `PROJECT_HOST_BINDINGS` | `app/main.py`, `.env.example` | `{}` | Host-to-project mapping | Optional | `{"console.local":"default"}` |
| `ENDPOINT_BINDINGS` | `app/main.py`, `.env.example` | `{}` | Host binding for model/project/mode/RAG | Optional | `{"api.local":{"model":"gpt-oss:20b","project_id":"default"}}` |
| `PROJECTS_ROOT` | `app/main.py`, `app/projects.py`, `host_webhook/receiver.py`, `.env.example` | `/opt/projects` | Root used to discover git repos and default repo paths | Optional but needed for repo ingestion defaults | `/opt/projects` |
| `PROJECT_ALIAS_TARGET_MODEL` | `app/main.py`, `.env.example` | `gpt-oss:20b` | Target model for auto-discovered project aliases | Optional | `gpt-oss:20b` |
| `PROJECT_ALIAS_CACHE_TTL_SECONDS` | `app/main.py`, `.env.example` | `60` | Auto-discovered alias cache TTL | Optional | `60` |
| `CORS_ORIGINS` | `app/main.py`, `.env.example` | localhost/Vite LAN/Bolt origins | Browser origin allowlist | Optional; important for web console | `http://localhost:5173,https://bolt.qneural.org` |
| `CORS_ALLOW_ALL` | `app/main.py`, `.env.example` | `false` | Allows all CORS origins when true | Optional; local only | `false` |
| `QDRANT_URL` | `app/rag.py`, `.env.example` | `http://192.168.5.5:6333` in code; `.env.example` uses `http://127.0.0.1:6333` | Qdrant base URL | Required for RAG | `http://127.0.0.1:6333` |
| `QDRANT_API_KEY` | `app/rag.py`, `.env.example` | empty | Optional Qdrant API key | Optional | `qdrant-key` |
| `EMBEDDING_BASE` | `app/rag.py`, `.env.example` | `http://192.168.5.5:8082` in code; `.env.example` uses `http://127.0.0.1:8082` | OpenAI-compatible embedding backend base URL | Required for embeddings/RAG | `http://127.0.0.1:8082` |
| `EMBEDDING_MODEL` | `app/rag.py`, `.env.example` | `all-MiniLM-L6-v2` | Default embedding model sent to embedding backend | Optional if backend ignores model | `all-MiniLM-L6-v2` |
| `EMBEDDING_VECTOR_SIZE` | `app/rag.py`, `.env.example` | `384` | Qdrant vector dimension for created collections | Must match embedding backend | `384` |
| `RAG_TOP_K` | `app/rag.py`, `.env.example` | `4` | Number of retrieval hits requested before bounding | Optional | `4` |
| `RAG_ENABLED` | `app/rag.py`, `.env.example` | `true` | Global RAG enable flag | Optional | `true` |
| `RAG_PROJECT_FLAGS` | `app/main.py`, `.env.example` | `{}` | Per-project or `mode:{mode}` RAG enable/disable flags | Optional | `{"default":true,"mode:chat":false}` |
| `QONDUIT_MAX_RAG_CHUNKS` | `app/main.py` | `4` | Maximum snippets inserted into prompt | Optional | `4` |
| `QONDUIT_MAX_RAG_CHARS` | `app/main.py` | `6000`, minimum `1000` | Maximum total RAG chars inserted into prompt | Optional | `6000` |
| `QONDUIT_TARGET_PROMPT_TOKENS` | `app/main.py` | `8192`, minimum `1024` | Target prompt size after assembly/trimming | Optional | `8192` |
| `QONDUIT_MAX_RECENT_MESSAGES` | `app/main.py` | `8` | Recent chat history window | Optional | `8` |
| `QONDUIT_MAX_RECENT_MESSAGES_CODING` | `app/main.py` | `16`, at least normal recent value | Coding-mode recent history window | Optional | `16` |
| `INGESTION_POLL_SECONDS` | `app/main.py`, `.env.example` | `2`, minimum `1` in main | Queue worker polling interval | Optional | `2` |
| `INGESTION_FILE_TIMEOUT_SECONDS` | `app/ingestion.py`, `.env.example` | `120.0`, minimum `10.0` | Per-file ingestion timeout | Optional | `120` |
| `INGESTION_INTER_FILE_DELAY_SECONDS` | `app/ingestion.py`, `.env.example` | `0` | Optional delay between jobs/files for inference protection | Optional | `0.2` |
| `INGESTION_INTER_CHUNK_DELAY_SECONDS` | `app/ingestion.py`, `.env.example` | `0` | Optional delay during chunk embedding/writing | Optional | `0.05` |
| `INGESTION_MAX_ACTIVE_JOB_SECONDS` | `app/ingestion.py`, `.env.example` | `3600` | Declared max active job seconds; no enforcement found in inspected code | Optional/unclear | `3600` |
| `UPLOAD_DIR` | `app/main.py` constant | `/mnt/models/qonduit_uploads` | Saved upload root | Hardcoded, not env-configurable | `/mnt/models/qonduit_uploads` |
| `COLLECTION_PREFIX` | `app/rag.py` constant | `qonduit_rag` | Qdrant collection prefix | Hardcoded | `qonduit_rag` |
| `COLLECTION_NAME` | `app/rag.py` constant | `qonduit_rag` | Base/prefix used by route code | Hardcoded | `qonduit_rag` |
| `DEFAULT_INCLUDE` | `app/ingest_repo.py` constant | many code/text globs | File include patterns for repo ingestion | Hardcoded default; CLI can override | `*.py,*.md,*.json` |
| `DEFAULT_EXCLUDE_PATTERNS` | `app/ingest_repo.py` constant | generated/vendor globs | Exclude patterns for repo ingestion | Hardcoded default; CLI can override | `**/node_modules/**` |
| `DEFAULT_MAX_FILE_BYTES` | `app/ingest_repo.py` constant | `1500000` | Max file size for repo ingestion | Optional via CLI/config object | `1500000` |
| `chunk_size` | `app/ingestion.py`, `app/ingest_repo.py`, `app/main.py` upload helper | `1200` | Character chunk size for repo and upload ingestion | Hardcoded in API paths; CLI can override | `1200` |
| `chunk_overlap` | same | `200` | Character overlap between chunks | Hardcoded in API paths; CLI can override | `200` |
| `GITHUB_WEBHOOK_SECRET` | `host_webhook/receiver.py`, `.env.example` | empty | GitHub signature verification secret | Required for host webhook | `...` |
| `GATEWAY_ENQUEUE_URL` | `host_webhook/receiver.py`, `.env.example` | `http://127.0.0.1:8090/v1/ingestion/enqueue` | Gateway enqueue URL used by webhook scripts | Optional with default | `http://127.0.0.1:8090/v1/ingestion/enqueue` |
| `SYNC_ENQUEUE_SCRIPT` | `host_webhook/receiver.py` | repo `scripts/sync_and_enqueue.sh` | Script spawned by GitHub webhook receiver | Optional with default | `/opt/qonduit/scripts/sync_and_enqueue.sh` |
| `GITHUB_REPO_PATH_OVERRIDES` | `host_webhook/receiver.py`, `.env.example` | `{}` | Map GitHub repo names to local repo paths | Optional | `{"org/repo":"/opt/projects/repo"}` |
| `GITHUB_PROJECT_ID_OVERRIDES` | `host_webhook/receiver.py`, `.env.example` | `{}` | Map GitHub repo names to project IDs | Optional | `{"org/repo":"android-qonduit"}` |

## 4. Endpoint Inventory

| Method | Path | Handler/function | Source file | Purpose | Path params | Query params | Request body shape | Response shape | Success example | Error response shape | Mutates state | Long-running | Needs polling | Qdrant | Embedding | Depends on project/collection/model | Safe for web console |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| GET | `/health` | `health` | `app/main.py` | Memory gateway health | none | none | none | `{ok, service}` | `{"ok":true,"service":"qonduit-memory-gateway"}` | FastAPI default if unavailable | no | no | no | no | no | no | yes |
| GET | `/v1/models` | `list_models` | `app/main.py` | Proxy/merge upstream models plus Qonduit aliases | none | none | none | OpenAI-style `{object:"list", data:[...]}` | `{"object":"list","data":[...]}` | `HTTPException` detail from `upstream_error` | no | no | no | no | no | model/alias | yes |
| GET | `/models` | `list_models` | `app/main.py` | Alias of `/v1/models` | none | none | none | same | same | same | no | no | no | no | no | model/alias | yes |
| POST | `/v1/embeddings` | `embeddings` | `app/main.py` | Proxy to embedding backend | none | none | `{"input": string|string[], "model"?: string, "user"?: string}` | OpenAI-style embeddings response | `{"object":"list","data":[{"embedding":[...]}],"model":"all-MiniLM-L6-v2"}` | `upstream_error` or `{detail:{message,detail}}` | no | maybe | no | no | yes | model | yes for health/debug; avoid large inputs |
| GET | `/v1/ingestion/status` | `ingestion_status_all` | `app/main.py` | All ingestion project statuses and queue summary | none | none | none | `{ok, projects, queue_length, queue_project_ids}` | `{"ok":true,"projects":{},"queue_length":0,"queue_project_ids":[]}` | FastAPI default | no | no | poll if running | no | no | project | yes |
| GET | `/v1/ingestion/status/{project_id}` | `ingestion_status_project` | `app/main.py` | One project ingestion status | `project_id` | none | none | `{ok, project_id, status, queued_position}` | `{"ok":true,"project_id":"default","status":{"state":"idle"},"queued_position":null}` | FastAPI default | may initialize status file | no | poll if running/queued | no | no | project_id | yes |
| GET | `/v1/ingestion/debug` | `ingestion_debug_state` | `app/main.py` | Queue, active job, recent completed/failed jobs, worker state | none | none | none | `{ok, queue_length, queue_project_ids, active_job, recent_completed, recent_failed, worker_state}` | `{"ok":true,"queue_length":0,"active_job":null,"worker_state":"running"}` | FastAPI default | no | no | poll if active | no | no | project | yes |
| POST | `/v1/ingestion/enqueue` | `ingestion_enqueue` | `app/main.py` | Enqueue repo ingestion job | none | none | `{"project_id": string, "repo_path"?: string|null, "branch"?: string|null}` | `{ok,enqueued,reason,status}` | `{"ok":true,"enqueued":true,"reason":"queued","status":{...}}` | 400 `{detail:{ok:false,reason,status}}` | yes | no API wait; background job | yes | later job yes | later job yes | project/repo | yes with confirmation; state-mutating |
| POST | `/rag/test-ingest` | `rag_test_ingest` | `app/main.py` | Manually add one text point | none | header `X-Project-ID`; header `X-Qonduit-User` | `{"text": string, "source"?: string, "collection"?: string, "document_name"?: string}` | `{ok,id}` | `{"ok":true,"id":"uuid"}` | FastAPI/default/upstream failures | yes | maybe | no | yes | yes | project/collection/user | yes only as diagnostic; mutates data |
| POST | `/rag/test-search` | `rag_test_search` | `app/main.py` | Search one namespace collection | none | header `X-Project-ID`; header `X-Qonduit-User` | `{"query": string, "limit"?: number, "collection"?: string}` | `{ok,results:[{id,score,text,payload}]}` | `{"ok":true,"results":[...]}` | FastAPI/default/upstream failures | no | maybe | no | yes | yes | project/collection/user | yes; diagnostic name caveat |
| GET | `/rag/collections` | `rag_list_collections` | `app/main.py` | List namespace collections for user/project using marker points | none | header `X-Project-ID`; header `X-Qonduit-User` | none | `{ok, collections:string[]}` | `{"ok":true,"collections":["default"]}` | Returns empty list on Qdrant scroll failure inside helper | no | no | no | yes | no | project/user | yes |
| POST | `/rag/collections/create` | `rag_create_collection` | `app/main.py` | Create marker point for namespace collection | none | header `X-Project-ID`; header `X-Qonduit-User` | `{"name": string}` | `{ok, collection}` | `{"ok":true,"collection":"docs"}` | 400 string detail; embedding/Qdrant errors possible | yes | maybe | no | yes | yes | project/collection/user | yes with confirmation; state-mutating |
| POST | `/rag/collections/delete` | `rag_delete_collection` | `app/main.py` | Delete points and upload files for namespace collection/user/project | none | header `X-Project-ID`; header `X-Qonduit-User` | `{"name": string}` | `{ok, collection, deleted_points, deleted_files}` | `{"ok":true,"collection":"docs","deleted_points":12,"deleted_files":1}` | 400 string detail; 500 string detail | yes/destructive | maybe | no | yes | no | project/collection/user | yes only with destructive safeguards |
| POST | `/rag/upload` | `rag_upload_document` | `app/main.py` | Upload, extract, chunk, embed, and write document | none | header `X-Project-ID`; header `X-Qonduit-User` | multipart: `file`, `collection`, optional `source` | `{ok, collection, document_name, chunks_added, saved_path}` | `{"ok":true,"collection":"docs","document_name":"a.pdf","chunks_added":3,"saved_path":"..."}` | 400 for validation; 500 string detail | yes | yes sync | no | yes | yes | project/collection/user | yes with progress limitation; mutates data |
| POST | `/internal/webhooks/github` | `github_webhook_stub` | `app/main.py` | Stub returns external ingestion command; does not enqueue | none | none | `{"project_id": string, "repo_path": string, "branch"?: string, "delivery_id"?: string}` | `{ok,message,contract}` | includes command `python -m app.ingest_repo ...` | FastAPI default | no | no | no | no | no | project/repo | not needed for console |
| GET | `/v1/chat/completions` | `chat_completions_help` | `app/main.py` | Help message | none | none | none | `{ok,message}` | `{"ok":true,"message":"Use POST ..."}` | FastAPI default | no | no | no | no | no | no | yes |
| GET | `/chat/completions` | `chat_completions_help` | `app/main.py` | Alias help message | none | none | none | same | same | same | no | no | no | no | no | no | yes |
| POST | `/v1/chat/completions` | `chat` | `app/main.py` | OpenAI-compatible chat with gateway memory/RAG/tool loop | none | headers can influence project/user/conversation | `GatewayChatRequest` with `model`, `messages`, optional `project_id`, `rag_collection`, `mode`, `stream`, etc. | OpenAI-style chat completion or SSE stream | standard `choices[0].message.content` | 502 upstream errors; streaming emits error chunk in some paths | yes conversation memory | yes | no | yes when RAG active | yes when RAG active | project/model/collection | yes for chat; not a management endpoint |
| POST | `/chat/completions` | `chat` | `app/main.py` | Alias of `/v1/chat/completions` | none | same | same | same | same | same | yes | yes | no | yes when RAG active | yes when RAG active | project/model/collection | yes |
| GET | embedding service `/health` | `health` | `qonduit-embedding-service/main.py` | Direct embedding service health | none | none | none | `{ok,service}` | `{"ok":true,"service":"qonduit-embedding-service"}` | service unavailable | no | no | no | no | no | no | yes if reachable directly |
| POST | embedding service `/v1/embeddings` | `embeddings` | `qonduit-embedding-service/main.py` | Direct embeddings | none | none | `{"input": string|string[]}` | OpenAI-style embeddings response | `{"object":"list","data":[...],"model":"all-MiniLM-L6-v2"}` | FastAPI default | no | maybe | no | no | yes | no | yes if reachable directly |
| GET | webhook receiver `/health` | `health` | `host_webhook/receiver.py` | Webhook service health | none | none | none | `{ok,service}` | `{"ok":true,"service":"qonduit-github-webhook"}` | service unavailable | no | no | no | no | no | no | maybe |
| POST | webhook receiver `/github/webhook` | `github_webhook` | `host_webhook/receiver.py` | Verify GitHub push event and spawn sync/enqueue script | none | GitHub headers | GitHub webhook JSON | `{ok,accepted,event,repository,project_id,repo_path,branch,spawned_pid}` | accepted push JSON | 401/400/500 structured detail | yes external process | yes externally | yes via gateway status | no direct | no direct | project/repo | no for normal console |

## 5. RAG Data Model and Naming

### `project_id`

- Logical namespace for a project/repo/chat context.
- Sanitized to alphanumeric plus `-` and `_`; missing values fall back to `default`.
- Resolved for chat from, in order:
  1. `X-Project-ID` header.
  2. Request body `project_id`.
  3. model alias `project_id`.
  4. endpoint binding `project_id`.
  5. `PROJECT_HOST_BINDINGS` host map.
  6. `DEFAULT_PROJECT_ID`.
- RAG routes outside chat use `request_project_id(request)`, which only checks `X-Project-ID` then default.

### Collection / namespace

There are two collection concepts:

1. **Physical Qdrant collection**
   - One per project.
   - Name rule: `qonduit_rag__{project_id}`.
2. **Logical namespace collection**
   - Stored as payload fields `namespace` and/or `collection` inside a physical Qdrant collection.
   - Used by `/rag/collections`, `/rag/upload`, `/rag/test-ingest`, `/rag/test-search`, and chat `rag_collection`.

### Qdrant collection name examples

- `project_id = default` -> `qonduit_rag__default`.
- `project_id = android-qonduit` -> `qonduit_rag__android-qonduit`.
- `project_id = chatgpt` -> `qonduit_rag__chatgpt`.

### Source document/file

- Upload ingestion stores:
  - `source`
  - `collection`
  - `document_name`
  - `chunk_index`
  - `file_type`
  - `saved_path`
- Repo ingestion stores:
  - `source = repo_ingest`
  - `project_id`
  - `repo_path`
  - `branch`
  - `file_path`
  - `chunk_index`
  - `commit_sha`
- Collection markers store:
  - `source = collection_marker`
  - `collection`
  - `document_name = __collection_marker__`

### Chunk

- A chunk is a text substring stored in Qdrant payload under `text`.
- Default chunk size is 1200 characters.
- Default overlap is 200 characters.
- Repo chunk point IDs are stable UUIDv5 values based on project, branch, relative path, chunk index, repo path, and `repo_ingest`.
- Upload/test chunks use generated UUIDs unless a point ID is supplied internally.

### Embedding

- Embeddings are produced by `EmbeddingBackend.embed_query` through an OpenAI-compatible `/v1/embeddings` endpoint.
- Default embedding model is `all-MiniLM-L6-v2`.
- Default vector size is `384`.
- Qdrant collections are created with cosine distance.

### Metadata

Common payload fields:

- `text`
- `project_id`
- `user_id`
- `namespace`
- `source`
- `collection`
- `document_name`
- `chunk_index`
- `repo_path`
- `branch`
- `file_path`
- `commit_sha`
- `file_type`
- `saved_path`

### Ingestion job

`IngestionJob` contains:

- `project_id`
- `repo_path`
- `branch`
- `enqueued_at`

### Ingestion status

Default project status fields:

- `project_id`
- `state`: one of `idle`, `queued`, `running`, `success`, `failed`
- `repo_path`
- `branch`
- `last_started_at`
- `last_finished_at`
- `last_error`
- `files_scanned`
- `chunks_embedded`
- `chunks_written`
- `skipped_files`
- `current_step`
- `current_file`

### Retrieval result

`search_documents(...)` returns a list of dictionaries:

```json
{
  "id": "point-id",
  "score": 0.75,
  "text": "retrieved chunk text",
  "payload": {
    "text": "retrieved chunk text",
    "project_id": "default",
    "user_id": "default",
    "namespace": "docs"
  }
}
```

### Citation/source result

- No dedicated citation model was found.
- Retrieval results include payload metadata that a frontend can display as raw source details.
- Chat completions do not return source/citation arrays.

### Chat context assembly

- Latest user message is used as retrieval query.
- Results are sorted deterministically and bounded by chunk count/character count.
- The final prompt includes a system section `Selected collection identities:` and, when results exist, `Relevant retrieved knowledge:`.

## 6. Collection Operations

| Operation | Status | Endpoint/function/file | Notes |
|---|---|---|---|
| list RAG collections | Implemented, logical only | `GET /rag/collections`; `rag_list_collections`; `list_collections`; `app/main.py`, `app/rag.py` | Lists marker-defined logical namespace collections, not physical Qdrant collections. |
| get collection metadata/details | Missing | none | No endpoint returns detailed collection metadata beyond names. |
| get collection point/chunk count | Missing | none | Delete endpoint internally counts deleted points only after deletion. |
| create collection | Partially implemented | `POST /rag/collections/create`; `create_collection_marker`; `ProjectScopedRagService.ensure_collection` | Creates a marker point and ensures the physical project Qdrant collection exists. Does not create a separate Qdrant collection per logical collection. |
| delete collection | Partially implemented/destructive | `POST /rag/collections/delete`; `rag_delete_collection`; `app/main.py` | Deletes points matching logical collection/user/project and removes upload directory. No dry-run. |
| clear/reset collection | Partially implemented | `POST /rag/collections/delete` | Equivalent to deleting the logical collection namespace for a user/project. No physical collection reset endpoint. |
| rename collection | Missing | none | No rename/update endpoint. |
| list source documents/files inside a collection | Missing | none | Metadata exists in payload but no API lists distinct documents. |
| delete one document/source from a collection | Missing | none | Only whole logical collection deletion exists. |
| get chunk/source details | Missing | none | Search returns chunks, but no point-detail endpoint. |
| get Qdrant health/status | Missing on gateway | none in `app/main.py` | Direct Qdrant client is used internally; no health endpoint wraps it. |

## 7. Ingestion Operations

| Operation | Status | Endpoint/function/file | Notes |
|---|---|---|---|
| upload single file for ingestion | Implemented | `POST /rag/upload`; `rag_upload_document`; `app/main.py` | Multipart synchronous upload, extract, chunk, embed, write. |
| upload multiple files | Missing | none | Client must call single-file endpoint repeatedly. |
| ingest a directory | Implemented via repo path | `POST /v1/ingestion/enqueue`; `IngestionManager`; `ingest_repository_with_progress` | Requires local directory path; intended for repos but walks files by patterns. |
| ingest a project/repo | Implemented | `POST /v1/ingestion/enqueue`; `app/ingestion.py`; `app/ingest_repo.py` | Auto-discovers defaults from `PROJECTS_ROOT` when repo path omitted and project is discoverable. |
| ingest ChatGPT export | Missing | none found | No ChatGPT export parser/import endpoint found. |
| ingest JSON/HTML/text/markdown/code files | Implemented | `DEFAULT_INCLUDE` in `ingest_repo.py`; `TEXT_EXTENSIONS` and extractors in `main.py` | Repo ingestion supports many code/text globs. Upload supports text-like extensions plus PDF/DOCX/CSV/XLS/XLSX. |
| enqueue ingestion job | Implemented | `POST /v1/ingestion/enqueue`; `IngestionStore.enqueue` | Prevents duplicate queued/running job for same project. |
| view current active job | Implemented | `GET /v1/ingestion/debug`; `IngestionManager.debug_state` | Returns first running project status as `active_job`. |
| view queue | Implemented summary only | `GET /v1/ingestion/debug`; `GET /v1/ingestion/status` | Returns queue length and project IDs, not full job objects. |
| view ingestion progress | Implemented | `GET /v1/ingestion/status/{project_id}` | Shows counts, current step/file. |
| cancel ingestion | Missing | none | No cancellation endpoint. |
| retry failed ingestion | Missing explicit endpoint | none | Re-enqueue may work manually if no queued/running status, but no retry endpoint. |
| clear hung job | Missing | none | No admin endpoint to reset status/queue. |
| monitor chunks embedded/written | Implemented | status endpoints | `chunks_embedded` and `chunks_written` are updated from progress. |
| view current_file | Implemented | status endpoints | `current_file` in project status. |
| view errors | Implemented | status/debug endpoints | `last_error`, `recent_failed`. |
| ingestion debug/status | Implemented | `GET /v1/ingestion/debug`; `GET /v1/ingestion/status`; `GET /v1/ingestion/status/{project_id}` | Good for web console diagnostics. |

### Supported file types

Repo ingestion include globs:

- `*.py`, `*.ts`, `*.tsx`, `*.js`, `*.jsx`, `*.java`, `*.kt`, `*.go`, `*.rs`, `*.c`, `*.cpp`, `*.h`, `*.hpp`, `*.dart`, `*.md`, `*.json`, `*.yaml`, `*.yml`, `*.toml`, `*.ini`, `*.sh`, `*.sql`.

Upload text-like extensions:

- `.txt`, `.md`, `.json`, `.csv`, `.py`, `.c`, `.cpp`, `.h`, `.hpp`, `.java`, `.kt`, `.kts`, `.xml`, `.html`, `.css`, `.js`, `.ts`, `.sql`, `.yaml`, `.yml`, `.toml`, `.ini`, `.sh`, `.go`, `.rs`, `.swift`, `.php`, `.rb`, `.pl`, `.lua`, `.vhdl`, `.vhd`, `.v`, `.dart`.

Upload document/spreadsheet extractors:

- `.pdf`
- `.docx`
- `.csv`
- `.xlsx`
- `.xls`

## 8. Retrieval/Search Operations

| Operation | Status | Endpoint/function/file | Notes |
|---|---|---|---|
| search/query a RAG collection | Implemented | `POST /rag/test-search`; `search_documents`; `ProjectScopedRagService.search` | Endpoint name is diagnostic but usable. |
| retrieve top-k chunks | Implemented | `RagSearchRequest.limit`; `RAG_TOP_K` for chat | `limit` controls test search; chat uses env `RAG_TOP_K`. |
| specify collection/project_id | Implemented | `collection` body field; `X-Project-ID` header; chat `project_id`/`rag_collection` | `/rag/*` only reads project from header/default, not body. |
| specify retrieval filters | Partially implemented | `user_id`, `namespace`, `project_id` filters in `app/rag.py` | No arbitrary metadata filters exposed. |
| inspect retrieval debug info | Partially implemented | `/rag/test-search` returns score/payload | No prompt assembly debug endpoint. |
| test embedding generation | Implemented | `POST /v1/embeddings` | Safe for small test strings. |
| view assembled prompt/context | Missing | none | Prompt info goes to perf logs/conversation metadata, not API. |
| return citations/sources | Partially implemented | `/rag/test-search` payload metadata | Chat does not return citations. |
| return scores/distances | Implemented | `/rag/test-search` and internal search result `score` | Chat does not expose them in response. |
| retrieve source document from chunk | Missing | none | `saved_path` may exist in payload, but no read/download endpoint. |
| query multiple collections | Partially implemented/unclear | chat accepts comma-separated `rag_collection` for identity text only; search filter expects one namespace string | Search uses one namespace value. Comma-separated chat `rag_collection` appears not to perform multi-filter search. |

## 9. Chat Integration

- RAG is **automatic** when enabled; clients do not need to call a separate retrieve endpoint before chat.
- RAG enablement is controlled by:
  - global `RAG_ENABLED`,
  - alias `rag_enabled`,
  - endpoint binding `rag_enabled`,
  - `RAG_PROJECT_FLAGS[project_id]`,
  - `RAG_PROJECT_FLAGS["mode:{mode}"]`.
- RAG target project is selected by:
  - `X-Project-ID` header,
  - request body `project_id`,
  - model alias metadata,
  - endpoint binding metadata,
  - host binding map,
  - fallback default project.
- RAG logical namespace is selected by request body `rag_collection`; if omitted, retrieval runs without namespace filtering but selected identity text defaults to the `project_id`.
- Request fields influencing RAG:
  - `model` for alias lookup.
  - `project_id`.
  - `rag_collection`.
  - `mode`.
  - `messages`, specifically the latest user message.
  - `stream` does not change retrieval logic.
- Context insertion limits:
  - Retrieval requests `RAG_TOP_K` hits.
  - Inserted chunks are bounded by `QONDUIT_MAX_RAG_CHUNKS`.
  - Inserted total characters are bounded by `QONDUIT_MAX_RAG_CHARS`.
  - Final prompt is trimmed toward `QONDUIT_TARGET_PROMPT_TOKENS`.
- Summaries:
  - Conversation summaries are used when recent conversation history overflows.
  - Coding mode uses local heuristic summary generation.
  - Chat mode tries upstream summarization and falls back to compact local summary on failure.
  - Summaries are inserted as `Conversation summary:` system messages when present.
- Retrieval result insertion:
  - Retrieved result `text` fields are stripped, bounded, joined with blank lines, and inserted as a system message beginning `Relevant retrieved knowledge:`.
- Source citations:
  - Chat responses do not include a dedicated source/citation field.
  - Retrieval payloads are not surfaced in the OpenAI response shape.
- Disable/target controls:
  - RAG can be globally disabled or disabled by alias/binding/project/mode config.
  - There is no explicit request field like `rag_enabled: false` in `GatewayChatRequest`.
  - A request can target a project via `project_id` and logical collection via `rag_collection`.
- Failure behavior:
  - If RAG retrieval raises an exception, the gateway logs `chat_rag_retrieval_failed`, clears `rag_results`/`rag_chunks`, and continues chat without RAG.
  - If the upstream chat backend fails, the endpoint raises/proxies a 502-style upstream error or emits an error chunk in some streaming paths.

## 10. Validation Commands

Use:

```bash
BASE="http://127.0.0.1:8090"
```

### Health/status

```bash
curl -sS "$BASE/health" | jq .
```

```bash
curl -sS "$BASE/v1/models" | jq .
```

```bash
curl -sS "$BASE/v1/ingestion/debug" | jq .
```

```bash
curl -sS "$BASE/v1/ingestion/status" | jq .
```

```bash
curl -sS "$BASE/v1/ingestion/status/default" | jq .
```

```bash
curl -sS "$BASE/v1/ingestion/status/android-qonduit" | jq .
```

### Collections and retrieval diagnostics

```bash
curl -sS -H "X-Project-ID: default" "$BASE/rag/collections" | jq .
```

```bash
curl -sS -X POST "$BASE/rag/test-search" \
  -H "Content-Type: application/json" \
  -H "X-Project-ID: default" \
  -d '{"query":"What is indexed in this project?","limit":4,"collection":"default"}' \
  | jq .
```

### Embedding diagnostic

```bash
curl -sS -X POST "$BASE/v1/embeddings" \
  -H "Content-Type: application/json" \
  -d '{"input":"hello from qonduit"}' \
  | jq '{object, model, dimensions: (.data[0].embedding | length)}'
```

### Chat completion with RAG/project context

```bash
curl -sS -X POST "$BASE/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "X-Project-ID: default" \
  -d '{
    "model":"qonduit-default",
    "project_id":"default",
    "rag_collection":"default",
    "messages":[{"role":"user","content":"Summarize the indexed project context relevant to setup."}],
    "stream":false
  }' \
  | jq .
```

### State-mutating commands: label before use

The following endpoints exist but are destructive or mutating. Do not run them from a read-only dashboard without explicit user confirmation:

- `POST /v1/ingestion/enqueue`
- `POST /rag/test-ingest`
- `POST /rag/collections/create`
- `POST /rag/collections/delete`
- `POST /rag/upload`

## 11. Frontend API Client Recommendations

| Function | Backend endpoint | Method | Input | Output | Frontend use case | Notes/limitations |
|---|---|---|---|---|---|---|
| `getGatewayHealth()` | `/health` | GET | none | `{ok, service}` | Show gateway availability | Does not verify Qdrant/embedding/upstream health. |
| `getGatewayModels()` | `/v1/models` | GET | none | OpenAI model list plus aliases | Show available aliases/projects | Upstream may be down; endpoint returns fallback alias list on connection failure. |
| `getRagIngestionDebug()` | `/v1/ingestion/debug` | GET | none | debug object | Queue overview, active job, recent history, worker status | Read-only and frontend-safe. |
| `getRagIngestionStatus(projectId)` | `/v1/ingestion/status/{project_id}` | GET | `projectId` | `{ok, project_id, status, queued_position}` | Selected project progress card | May create default status entry. |
| `getAllRagIngestionStatus()` | `/v1/ingestion/status` | GET | none | `{ok, projects, queue_length, queue_project_ids}` | Dashboard overview | Read-only. |
| `listRagCollections(projectId, userId?)` | `/rag/collections` | GET | headers `X-Project-ID`, optional `X-Qonduit-User` | `{ok, collections}` | Known logical collection cards | Lists marker collections only. Empty may mean no markers or Qdrant failure. |
| `searchRagCollection(projectId, collection, query, limit)` | `/rag/test-search` | POST | headers plus body `{query, limit, collection}` | `{ok, results}` | Diagnostic search panel | Endpoint name is `test`; no arbitrary filters. |
| `createRagCollection(projectId, name)` | `/rag/collections/create` | POST | headers plus `{name}` | `{ok, collection}` | Admin collection creation | Mutates Qdrant by writing marker point. Needs confirmation. |
| `deleteRagCollection(projectId, name)` | `/rag/collections/delete` | POST | headers plus `{name}` | `{ok, collection, deleted_points, deleted_files}` | Admin deletion | Destructive. Require typed confirmation. |
| `uploadRagDocument(projectId, collection, file, source?)` | `/rag/upload` | POST | multipart | `{ok, collection, document_name, chunks_added, saved_path}` | Upload document into RAG | Synchronous; no progress/job ID. |
| `enqueueRepoIngestion(projectId, repoPath?, branch?)` | `/v1/ingestion/enqueue` | POST | `{project_id, repo_path?, branch?}` | `{ok,enqueued,reason,status}` | Start repo ingestion | Needs polling via status/debug. Mutating. |
| `createEmbedding(input, model?)` | `/v1/embeddings` | POST | `{input, model?}` | OpenAI embeddings response | Embedding service diagnostic | Avoid displaying full vector; show dimensions/status. |
| `chatWithRag(payload)` | `/v1/chat/completions` | POST | `GatewayChatRequest` | OpenAI-compatible response | Test chat against selected project/collection | No citations field. |
| `getQdrantStatus()` | none | n/a | n/a | n/a | Desired health card | Missing backend endpoint; cannot implement directly unless browser can reach Qdrant separately. |
| `getEmbeddingStatus()` | none on gateway | n/a | n/a | n/a | Desired health card | Gateway has `/v1/embeddings` proxy; direct embedding service `/health` may be on another port. |
| `getRagCollectionStats(collectionId)` | none | n/a | n/a | n/a | Collection details | Missing backend endpoint. |
| `startRagIngestion(collectionId, options)` | partial: `/v1/ingestion/enqueue` | POST | project/repo options | queue response | Repo ingestion only | Does not ingest by logical collection ID. |
| `cancelRagIngestion(jobId)` | none | n/a | n/a | n/a | Stop active job | Missing backend endpoint. |

## 12. Recommended Web Console RAG MVP

The smallest frontend-only MVP possible with current backend capabilities:

### RAG service health

- Use `/health` for memory gateway status.
- Use `/v1/models` to indicate upstream model proxy/alias availability.
- Use a small `/v1/embeddings` request as an embedding smoke test, only on demand or with conservative refresh.
- Display Qdrant health as `unknown` unless `/rag/collections` or `/rag/test-search` succeeds.

### Ingestion queue overview

- Use `/v1/ingestion/debug`.
- Show:
  - `worker_state`
  - `queue_length`
  - `queue_project_ids`
  - `active_job`
  - recent completed/failed jobs.

### Known collections cards

- Use `/rag/collections` with selected `X-Project-ID` and optional `X-Qonduit-User`.
- Present these as logical namespace collections, not physical Qdrant collections.
- Include warning text: collections may not appear unless marker points exist.

### Selected collection status/details

- Current backend cannot return true collection stats.
- Use:
  - `/v1/ingestion/status/{project_id}` for project ingestion status.
  - `/rag/test-search` for an optional diagnostic query.
- Display raw payload metadata from search results for source clues.

### Raw JSON diagnostics

- Include collapsible raw JSON panels for:
  - `/v1/ingestion/debug`
  - `/v1/ingestion/status/{project_id}`
  - `/rag/collections`
  - optional `/rag/test-search` result.

### Active ingestion polling

- Poll `/v1/ingestion/debug` and `/v1/ingestion/status/{project_id}` only while:
  - `active_job` is non-null,
  - selected project status is `queued` or `running`, or
  - queue length is greater than zero.
- Use bounded polling, such as every 2-5 seconds for up to a fixed time window, then degrade to manual refresh.

### Endpoint error display

- Display each endpoint independently.
- Treat missing endpoints, network failures, and unexpected shapes as per-card warnings rather than page failures.
- Preserve raw error JSON/text for operator debugging.

## 13. Backend Gaps for Full RAG Management

### Required for MVP

- **Stable list collections endpoint semantics.** Existing `/rag/collections` lists marker logical collections only; it does not list physical Qdrant collections or project collections.
- **Qdrant/embedding health visibility.** No gateway endpoint directly reports Qdrant health or embedding backend health without generating embeddings.
- **Collection stats endpoint.** No point/chunk count endpoint exists.
- **Defensive CORS/private network validation.** CORS is configured, but web console deployments need verification for the actual Dyad/console origins.

### Nice to have

- **Document/source list endpoint.** Payload metadata exists, but no endpoint lists distinct source documents/files.
- **Document delete endpoint.** Only whole logical collection deletion exists.
- **Retrieval search endpoint with production name.** `/rag/test-search` works but is named as a test endpoint.
- **Prompt/context debug endpoint.** No endpoint returns assembled RAG context before chat.
- **Consistent JSON errors.** Some errors use structured details; others use plain strings.
- **Upload progress/job model.** `/rag/upload` is synchronous with no progress reporting.
- **Active collection selector clarity.** `rag_collection` can select a namespace, but multi-collection behavior is unclear.

### Later

- **Create/delete physical project collection endpoints.** Current design creates physical collections lazily.
- **Rename collection endpoint.** No rename support.
- **Ingestion cancel endpoint.** No job ID or cancellation path.
- **Retry failed ingestion endpoint.** Manual enqueue may be sufficient but is not first-class.
- **Clear hung job/reset status endpoint.** No administrative recovery endpoint.
- **Citation/source return shape in chat.** Chat does not expose retrieved sources.
- **Retrieve original source document from chunk.** No source-file read/download endpoint.
- **Query multiple collections.** No robust multi-namespace filter API.
- **ChatGPT export importer.** No discovered parser/importer for ChatGPT export files.

## 14. Suggested Backend Endpoint Additions

Do not implement these now. These are minimal additions for a clean web console RAG MVP.

| Method | Path | Request shape | Response shape | Why needed | Can wrap existing internals? |
|---|---|---|---|---|---|
| GET | `/v1/rag/health` | none | `{ok, qdrant:{ok,url,error?}, embedding:{ok,base,model,error?}}` | One safe health card for RAG dependencies | Yes: `qdrant.get_collections()` and small embedding request or backend health call. |
| GET | `/v1/rag/projects/{project_id}/collections` | path project, optional user header/query | `{ok, project_id, qdrant_collection, collections:[{name, marker_present, point_count?}]}` | Clear semantics for logical collections | Partly: `list_collections`; stats require Qdrant scroll/count. |
| GET | `/v1/rag/projects/{project_id}/stats` | path project | `{ok, project_id, qdrant_collection, points_count, vectors_size, distance}` | Dashboard project cards | Yes: Qdrant collection info/count. |
| GET | `/v1/rag/projects/{project_id}/documents` | optional `collection`, pagination | `{ok, documents:[{document_name, source, chunks, file_type?, saved_path?}]}` | Needed for document inventory | Yes: Qdrant scroll distinct payload fields. |
| DELETE | `/v1/rag/projects/{project_id}/documents` | `{collection, document_name}` or query params | `{ok, deleted_points, deleted_files?}` | Delete one document/source | Yes: Qdrant delete by payload filter plus upload file cleanup. |
| POST | `/v1/rag/projects/{project_id}/search` | `{query, collection?, limit?, filters?}` | `{ok, results:[{id, score, text, source, payload}]}` | Production retrieval API | Yes: wraps `search_documents`. |
| GET | `/v1/ingestion/jobs` | none | `{ok, queue:[...], active_job, recent_completed, recent_failed}` | Full queue display | Partly: expands `debug_state` with full queue jobs. |
| POST | `/v1/ingestion/jobs/{job_id}/cancel` | none or `{reason?}` | `{ok, cancelled, status}` | Job control | Requires job IDs/cancellation checks; not currently present. |
| POST | `/v1/ingestion/status/{project_id}/reset` | `{state?:"idle"}` | `{ok,status}` | Recover hung jobs | Wraps `IngestionStore.update_status`; should be admin-protected. |
| POST | `/v1/chat/completions` enhancement | existing body plus optional `rag_enabled` and response metadata flag | OpenAI response plus optional `qonduit.rag.sources` metadata | Per-request RAG control and citations | Partly: chat already has `rag_results`; response shape change needs compatibility care. |

## 15. Suggested Dyad Frontend Prompt

```text
Build a frontend-only Qonduit Web Console RAG diagnostics MVP. Prioritize web console functionality; do not constrain the design for Android compatibility. Do not change backend code, API response shapes, deployment files, Android/Flutter/Dart/Kotlin/Gradle files, or Docker/systemd files.

Use only these discovered safe backend endpoints from the Qonduit memory gateway:
- GET /health
- GET /v1/models
- GET /v1/ingestion/debug
- GET /v1/ingestion/status
- GET /v1/ingestion/status/{project_id}
- GET /rag/collections with X-Project-ID header
- POST /rag/test-search for explicit user-triggered diagnostic searches
- POST /v1/embeddings only as a small on-demand embedding smoke test
- POST /v1/chat/completions only for an explicit user-triggered RAG chat test

Frontend requirements:
1. Add a RAG dashboard page with cards for gateway health, model/alias availability, ingestion worker state, active job, queue length, selected project status, logical collections, and raw diagnostics.
2. Use stale-while-revalidate behavior: render cached/previous data immediately, refresh in the background, and show per-card loading/error states without blanking the page.
3. Poll only when ingestion is active: if /v1/ingestion/debug reports active_job, queue_length > 0, or selected project status is queued/running, poll every 2-5 seconds for a bounded window, then fall back to manual refresh.
4. Treat /rag/collections as logical namespace collections, not physical Qdrant collections. If it returns an empty list, show “No marker collections found or Qdrant unavailable” rather than a fatal error.
5. Add a selected project input/default. Send X-Project-ID for RAG collection and diagnostic search calls.
6. Add a diagnostic search form requiring the user to click Search. Call POST /rag/test-search with {query, limit, collection}. Display result id, score, text preview, and raw payload metadata.
7. Do not expose destructive actions by default. If adding buttons for enqueue/upload/create/delete later, gate them behind explicit confirmations and feature flags.
8. Add defensive response handling for missing endpoints, non-JSON responses, unexpected shapes, and network failures. Display raw error details in collapsible panels.
9. Do not assume citations exist in chat responses. If testing chat, display the normal OpenAI-compatible response and a note that source citations are not currently returned.
10. Build/validation plan: run typecheck/lint/build for the web console; manually verify cards against a local BASE URL such as http://127.0.0.1:8090; test unavailable-backend and empty-collection states.
```

## 16. Appendix: Code References

| File path | Function/class/name | Why it matters | Relevant endpoint |
|---|---|---|---|
| `qonduit-memory-gateway/app/main.py` | `app = FastAPI(...)` | Main memory gateway API app | all gateway endpoints |
| `qonduit-memory-gateway/app/main.py` | CORS setup | Web console browser access | all gateway endpoints |
| `qonduit-memory-gateway/app/main.py` | `RagIngestRequest` | Manual text ingest request shape | `POST /rag/test-ingest` |
| `qonduit-memory-gateway/app/main.py` | `RagSearchRequest` | Diagnostic search request shape | `POST /rag/test-search` |
| `qonduit-memory-gateway/app/main.py` | `RagCollectionCreateRequest` | Collection marker creation body | `POST /rag/collections/create` |
| `qonduit-memory-gateway/app/main.py` | `RagCollectionDeleteRequest` | Collection deletion body | `POST /rag/collections/delete` |
| `qonduit-memory-gateway/app/main.py` | `EmbeddingsRequest` | Gateway embedding proxy request shape | `POST /v1/embeddings` |
| `qonduit-memory-gateway/app/main.py` | `IngestionEnqueueRequest` | Ingestion enqueue body | `POST /v1/ingestion/enqueue` |
| `qonduit-memory-gateway/app/main.py` | `GatewayChatRequest` | Chat request fields including `project_id`, `rag_collection`, `mode` | `POST /v1/chat/completions` |
| `qonduit-memory-gateway/app/main.py` | `health` | Gateway health | `GET /health` |
| `qonduit-memory-gateway/app/main.py` | `ingestion_status_all` | All project ingestion summary | `GET /v1/ingestion/status` |
| `qonduit-memory-gateway/app/main.py` | `ingestion_status_project` | Per-project ingestion status | `GET /v1/ingestion/status/{project_id}` |
| `qonduit-memory-gateway/app/main.py` | `ingestion_debug_state` | Queue/active/history diagnostics | `GET /v1/ingestion/debug` |
| `qonduit-memory-gateway/app/main.py` | `ingestion_enqueue` | Queue a repo ingestion job | `POST /v1/ingestion/enqueue` |
| `qonduit-memory-gateway/app/main.py` | `list_models` | Upstream/alias model list | `GET /v1/models`, `GET /models` |
| `qonduit-memory-gateway/app/main.py` | `embeddings` | Embedding proxy | `POST /v1/embeddings` |
| `qonduit-memory-gateway/app/main.py` | `get_request_user_id` | User namespace header behavior | `/rag/*`, chat |
| `qonduit-memory-gateway/app/main.py` | `resolve_project_id` | Chat project selection precedence | `POST /v1/chat/completions` |
| `qonduit-memory-gateway/app/main.py` | `request_project_id` | Non-chat RAG route project selection | `/rag/*` |
| `qonduit-memory-gateway/app/main.py` | `should_enable_rag` | Global/project/mode/alias RAG enablement | `POST /v1/chat/completions` |
| `qonduit-memory-gateway/app/main.py` | `chunk_text` | Upload chunking helper | `POST /rag/upload` |
| `qonduit-memory-gateway/app/main.py` | `extract_text_from_file` | Upload file type dispatch | `POST /rag/upload` |
| `qonduit-memory-gateway/app/main.py` | `rag_test_ingest` | Manual point insertion | `POST /rag/test-ingest` |
| `qonduit-memory-gateway/app/main.py` | `rag_test_search` | Diagnostic retrieval endpoint | `POST /rag/test-search` |
| `qonduit-memory-gateway/app/main.py` | `rag_list_collections` | Logical namespace list | `GET /rag/collections` |
| `qonduit-memory-gateway/app/main.py` | `rag_create_collection` | Marker creation | `POST /rag/collections/create` |
| `qonduit-memory-gateway/app/main.py` | `rag_delete_collection` | Logical namespace deletion | `POST /rag/collections/delete` |
| `qonduit-memory-gateway/app/main.py` | `rag_upload_document` | Synchronous upload ingestion | `POST /rag/upload` |
| `qonduit-memory-gateway/app/main.py` | `github_webhook_stub` | Returns external repo ingest command | `POST /internal/webhooks/github` |
| `qonduit-memory-gateway/app/main.py` | `chat` | Main chat/RAG integration path | `POST /v1/chat/completions` |
| `qonduit-memory-gateway/app/rag.py` | `EmbeddingBackend` | OpenAI-compatible embedding client | `/v1/embeddings`, RAG internals |
| `qonduit-memory-gateway/app/rag.py` | `ProjectScopedRagService` | Qdrant-backed project RAG service | RAG internals |
| `qonduit-memory-gateway/app/rag.py` | `project_collection_name` | Qdrant physical collection naming | RAG internals |
| `qonduit-memory-gateway/app/rag.py` | `add_document` | Embeds/writes one point | `/rag/test-ingest`, `/rag/upload`, repo ingestion |
| `qonduit-memory-gateway/app/rag.py` | `search_documents` | Embeds query/searches Qdrant | `/rag/test-search`, chat |
| `qonduit-memory-gateway/app/rag.py` | `list_collections` | Marker-point namespace listing | `GET /rag/collections` |
| `qonduit-memory-gateway/app/rag.py` | `create_collection_marker` | Marker-point namespace creation | `POST /rag/collections/create` |
| `qonduit-memory-gateway/app/ingestion.py` | `IngestionJob` | Queue job model | `POST /v1/ingestion/enqueue` |
| `qonduit-memory-gateway/app/ingestion.py` | `IngestionStore` | Status/queue/history JSON persistence | ingestion endpoints |
| `qonduit-memory-gateway/app/ingestion.py` | `IngestionManager` | Background ingestion worker | ingestion endpoints |
| `qonduit-memory-gateway/app/ingest_repo.py` | `DEFAULT_INCLUDE` | Repo ingestion supported file globs | repo ingestion |
| `qonduit-memory-gateway/app/ingest_repo.py` | `DEFAULT_EXCLUDE_PATTERNS` | Repo ingestion default skips | repo ingestion |
| `qonduit-memory-gateway/app/ingest_repo.py` | `IngestConfig` | Repo ingestion config | repo ingestion |
| `qonduit-memory-gateway/app/ingest_repo.py` | `ingest_repository_with_progress` | Walk/chunk/embed/write repo files with progress callbacks | queued ingestion |
| `qonduit-memory-gateway/app/ingest_repo.py` | `_delete_stale_chunks` | Removes repo chunks for deleted files | repo ingestion |
| `qonduit-memory-gateway/app/projects.py` | `discover_git_projects` | Discovers repo defaults from `PROJECTS_ROOT` | enqueue defaults/model aliases |
| `qonduit-memory-gateway/app/projects.py` | `ProjectAliasCache` | Cached auto-discovered aliases | `/v1/models`, chat alias resolution |
| `qonduit-memory-gateway/app/store.py` | `load_conversation`, `save_conversation` | Conversation memory and metadata persistence | chat |
| `qonduit-memory-gateway/app/summarizer.py` | `summarize_messages` | Rolling conversation summaries | chat |
| `qonduit-embedding-service/main.py` | `SentenceTransformer(...)` | Local embedding model | embedding service `/v1/embeddings` |
| `qonduit-embedding-service/main.py` | `health`, `embeddings` | Direct embedding service routes | embedding service endpoints |
| `qonduit-memory-gateway/host_webhook/receiver.py` | `github_webhook` | GitHub push -> spawn sync/enqueue script | webhook `/github/webhook` |
| `qonduit-memory-gateway/.env.example` | RAG/env sample values | Operator configuration reference | all services |
| `qonduit_router_api.py` | Flask router endpoints | Model/router management, not RAG inventory focus | router `/api/v1/qonduit-router/*` |
