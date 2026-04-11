# Current Baseline: Qonduit Memory Gateway

## Overview

The Qonduit Memory Gateway is a FastAPI-based service that provides:
- Chat completion proxying to an upstream LLM (llama.cpp server)
- Conversation memory management with rolling summaries
- Project-scoped RAG (Retrieval Augmented Generation) using Qdrant vector database
- Repository ingestion pipeline for automatic knowledge base population

---

## 1. Current API Contract

### Core Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET/POST | `/v1/chat/completions`, `/chat/completions` | Chat completions (OpenAI-compatible) |
| GET | `/v1/models`, `/models` | List available models |
| POST | `/v1/embeddings` | Generate embeddings |

### RAG Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/rag/test-ingest` | Manual document ingestion |
| POST | `/rag/test-search` | Manual document search |
| GET | `/rag/collections` | List collections |
| POST | `/rag/collections/create` | Create collection |
| POST | `/rag/collections/delete` | Delete collection |
| POST | `/rag/upload` | Upload file for RAG (PDF, DOCX, XLSX, TXT) |

### Ingestion Management Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/ingestion/status` | Status of all projects |
| GET | `/v1/ingestion/status/{project_id}` | Status for specific project |
| GET | `/v1/ingestion/debug` | Full debug state (queue, active job, history) |
| POST | `/v1/ingestion/enqueue` | Enqueue repository ingestion job |

### Webhook Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/internal/webhooks/github` | GitHub webhook receiver for auto-ingestion |

---

## 2. Request/Response Schemas

### GatewayChatRequest

```python
class GatewayChatRequest(BaseModel):
    conversation_id: str | None
    project_id: str | None
    messages: list[ChatMessage]
    model: str
    context_size: int | None
    max_tokens: int = 2048
    temperature: float = 0.7
    stream: bool = False
    user: str | None
    rag_collection: str | None
    mode: str | None  # "chat" or "coding"
    
    model_config = {"extra": "allow"}  # Allows passthrough of extra fields
```

### ChatMessage

```python
class ChatMessage(BaseModel):
    role: str
    content: Any  # Currently coerced to text
```

### Chat Completion Response

```json
{
  "id": "chatcmpl-qonduit-{uuid}",
  "object": "chat.completion",
  "created": <timestamp>,
  "model": "<requested_model>",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "<response_text>"
    },
    "finish_reason": "stop"
  }],
  "usage": {}
}
```

---

## 3. Current Memory Behavior

### Conversation Storage

- **Location**: `$GATEWAY_DATA_DIR/conversations/` (default: `/app/data/conversations/`)
- **Format**: JSON files per conversation
- **Project scoping**: Conversations are stored under project subdirectories

### State Structure

```json
{
  "project_id": "default",
  "conversation_id": "<id>",
  "summary": "<rolling_summary>",
  "recent_messages": [],
  "last_model": "<model>",
  "last_context_size": 65536,
  "last_mode": "chat",
  "last_prompt_tokens": 0,
  "last_reserved_output": 4096,
  "metadata": {
    "mode": "chat",
    "project_id": "default",
    "rag_collection": "default",
    "rag_enabled": true,
    "request_model": "gpt-oss:20b",
    "effective_model": "gpt-oss:20b"
  }
}
```

### Message Trimming & Summarization

1. **Budget calculation**: Based on `context_size` env var (default 65536)
   - Reserved output: 4096 tokens (for 64k context)
   - Safety margin: 4096 tokens
   - Prompt target: ~46000 tokens

2. **Trimming strategy**:
   - Oldest non-technical messages removed first (in coding mode)
   - Technical messages (code, errors, commands) are protected
   - When overflow occurs, older messages are summarized

3. **Summary generation**:
   - Triggered when message count exceeds budget
   - Uses upstream LLM to summarize older messages
   - Summary is prepended as a system message

### RAG Integration

- **Vector store**: Qdrant at `$QDRANT_URL` (default: `http://192.168.5.5:6333`)
- **Embeddings**: OpenAI-compatible endpoint at `$EMBEDDING_BASE` (default: `http://192.168.5.5:8082`)
- **Collection naming**: `qonduit_rag__{project_id}`
- **Top-K retrieval**: Configurable via `RAG_TOP_K` (default: 4)

### RAG Injection Flow

1. Extract latest user message text
2. Search documents in project collection
3. Fallback to user_id=None if no results with user filter
4. Inject retrieved chunks as system message before conversation messages

```
System: <mode prompt>
System: Rolling summary: <summary>
System: Relevant retrieved knowledge: <rag_chunks>
User: <message 1>
Assistant: <response 1>
...
```

---

## 4. Project Resolution

Project ID is resolved in priority order:

1. `X-Project-ID` header
2. `project_id` field in request body
3. Model alias configuration (`MODEL_ALIAS_CONFIG`)
4. Endpoint binding configuration (`ENDPOINT_BINDINGS`)
5. Host-based binding (`PROJECT_HOST_BINDINGS`)
6. Default: `DEFAULT_PROJECT_ID` ("default")

---

## 5. Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLAMA_BASE` | `http://192.168.5.5:8080` | Upstream llama.cpp server |
| `DEFAULT_CONTEXT_SIZE` | `65536` | Context window size |
| `DEFAULT_MODE` | `chat` | Default mode (chat/coding) |
| `GATEWAY_DATA_DIR` | `/app/data` | Data directory |
| `DEFAULT_PROJECT_ID` | `default` | Default project namespace |
| `QDRANT_URL` | `http://192.168.5.5:6333` | Qdrant vector DB URL |
| `EMBEDDING_BASE` | `http://192.168.5.5:8082` | Embedding service URL |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Embedding model name |
| `RAG_ENABLED` | `true` | Enable/disable RAG |
| `RAG_TOP_K` | `4` | Number of RAG results |
| `MODEL_ALIAS_CONFIG` | `{}` | JSON config for model aliases |
| `PROJECT_HOST_BINDINGS` | `{}` | JSON config for host->project mapping |
| `RAG_PROJECT_FLAGS` | `{}` | JSON config for per-project RAG flags |

---

## 6. Repository Ingestion

### Ingestion Pipeline

1. Queue jobs via `/v1/ingestion/enqueue` or GitHub webhook
2. Worker processes jobs sequentially
3. For each repo:
   - Scan files matching include patterns
   - Skip excluded patterns and binary files
   - Chunk files into segments
   - Generate embeddings for each chunk
   - Store in Qdrant with metadata

### Job States

- `idle`: No activity
- `queued`: Waiting in queue
- `running`: Currently processing
- `success`: Completed successfully
- `failed`: Error occurred

### Status Tracking

- Queue: `$GATEWAY_DATA_DIR/ingestion_queue.json`
- Status: `$GATEWAY_DATA_DIR/ingestion_status.json`
- History: `$GATEWAY_DATA_DIR/ingestion_history.json`
- Log: `$GATEWAY_DATA_DIR/ingestion.log`

---

## 7. Known Limitations

1. **No tool calling support**: The `GatewayChatRequest` does not accept `tools` or `tool_choice` parameters
2. **Content coercion**: All message content is coerced to plain text; structured content (images, tool calls) is lost
3. **Single collection per project**: RAG uses one flat collection per project, no namespace isolation within project
4. **No function execution**: Tool call responses from the model are not parsed or executed
5. **Limited response parsing**: Assistant responses are stored as raw text, not structured
