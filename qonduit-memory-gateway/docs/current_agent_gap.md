# Agent Gap Verification Report

**Date:** 2025-12-14  
**Scope:** Qonduit Memory Gateway - Agentic Capabilities Assessment

---

## 1. Project-Scoped RAG Wiring

### Current Implementation

Project-scoped RAG is implemented via `ProjectScopedRagService` in `app/rag.py`:

**Collection Naming:**
```python
def project_collection_name(project_id: str | None) -> str:
    project = _normalize_identifier(project_id, "default")
    return f"{COLLECTION_PREFIX}__{project}"
```
- Collections are named `qonduit_rag__{project_id}` (e.g., `qonduit_rag__myproject`)
- Default project uses `qonduit_rag__default`

**Document Storage:**
- Each document payload includes: `project_id`, `user_id`, `namespace` (used as collection/sub-collection)
- Vector embeddings generated via `EmbeddingBackend` calling upstream embedding service

**Search Filtering:**
```python
must_conditions = [
    FieldCondition(key="project_id", match=MatchValue(value=project_id)),
]
# Optional filters for user_id and namespace
```

**RAG Enablement Logic** (`should_enable_rag()` in main.py):
1. Check global `RAG_ENABLED` env flag
2. Check alias-level `rag_enabled` override
3. Check endpoint binding `rag_enabled` override  
4. Check `RAG_PROJECT_FLAGS` per-project or per-mode flags
5. Default: enabled if none of the above disable it

**Namespace Support:**
- The `namespace` field exists in payloads and can be used for sub-filtering
- Used internally as "collection" parameter in legacy API
- **Limitation:** No explicit namespace isolation API; namespaces are just metadata filters

---

## 2. Project-Specific Models at `/v1/models`

### Discovery Mechanisms

Project-specific models are exposed through **model aliases** with three configuration sources:

**Source 1: Auto-Discovered Repo Aliases** (`PROJECTS_ROOT`)
- Scans `.qonduit/model_alias.json` files in project directories
- Target model must match `PROJECT_ALIAS_TARGET_MODEL` env (default: `gpt-oss:20b`)
- Source marked as `"auto_discovered_repo"`

**Source 2: Endpoint Bindings** (`ENDPOINT_BINDINGS` env JSON)
```json
{
  "host.example.com": {
    "model_alias": "my-alias",
    "model": "actual-upstream-model",
    "project_id": "my-project",
    "default_mode": "coding",
    "rag_enabled": true
  }
}
```

**Source 3: Static Config** (`MODEL_ALIAS_CONFIG` env JSON)

### `/v1/models` Response Format

```python
{
  "id": "alias-id",
  "object": "model",
  "owned_by": "qonduit-alias" | "qonduit-auto-discovery" | "qonduit-endpoint-binding",
  "metadata": {
    "project_id": "...",
    "default_mode": "chat" | "coding",
    "target_model": "...",
    "rag_enabled": true/false,
    "source": "...",
    "repo_path": "...",
    "branch": "...",
    "host": "..."
  }
}
```

**Merge Behavior:**
- Upstream llama-base models are fetched and merged with alias models
- Alias models take precedence by ID deduplication

---

## 3. Coding Mode Selection

### Resolution Priority Order (`resolve_mode()` function)

Mode is resolved in this exact priority order:

1. **Request field** `req.mode` (if "chat" or "coding")
2. **Header** `X-Gateway-Mode` (if "chat" or "coding")
3. **Model alias** `default_mode` from `MODEL_ALIAS_CONFIG` / discovery / endpoint binding
4. **Endpoint binding** `default_mode` from `ENDPOINT_BINDINGS`
5. **Project mode map** `PROJECT_DEFAULT_MODE_MAP[project_id]`
6. **Global default** `PROJECT_DEFAULT_MODE` env (defaults to `DEFAULT_MODE` = "chat")
7. **Fallback:** `"chat"`

### Coding Mode Effects

When `mode == "coding"`:
- **System prompt:** Uses specialized CODING mode prompt emphasizing correctness, exact paths, commands
- **Context window:** `recent_window = 16` (vs 8 for chat)
- **Message protection:** `is_technical_message()` protects code blocks, errors, file paths from trimming
- **Technical patterns detected:**
  - Code fences (```)
  - Traceback/Exception/Error keywords
  - File extensions (.py, .ts, .java, etc.)
  - Tool names (python, pytest, npm, cargo, etc.)
  - Constraint language (must, required, cannot)

---

## 4. Mode Binding Summary

| Binding Mechanism | Type | Example |
|-------------------|------|---------|
| Request field | `GatewayChatRequest.mode` | `{"mode": "coding"}` |
| HTTP Header | `X-Gateway-Mode` | `X-Gateway-Mode: coding` |
| Model Alias | `MODEL_ALIAS_CONFIG` / `.qonduit/model_alias.json` | `{"default_mode": "coding"}` |
| Endpoint Binding | `ENDPOINT_BINDINGS` host-based | `{"default_mode": "coding"}` |
| Project Map | `PROJECT_DEFAULT_MODE_MAP` | `{"my-project": "coding"}` |
| Global Env | `PROJECT_DEFAULT_MODE` | `PROJECT_DEFAULT_MODE=coding` |

**Answer:** Coding mode can be selected via **request field**, **headers**, **model alias metadata**, **endpoint bindings**, **project config**, or **environment variable**. The resolution follows the priority order above.

---

## 5. Missing for OpenAI-Style Tool Calling

### Schema Gaps

**`ChatMessage` model lacks:**
```python
class ChatMessage(BaseModel):
    role: str
    content: Any
    # MISSING:
    # tool_calls: list[ToolCall] | None
    # tool_call_id: str | None  (for tool responses)
    # name: str | None
```

**`GatewayChatRequest` model lacks:**
```python
class GatewayChatRequest(BaseModel):
    # ... existing fields ...
    # MISSING:
    # tools: list[ToolDefinition] | None
    # tool_choice: str | dict | None  ("auto", "none", "required", or specific tool)
```

### Runtime Gaps

1. **No tool definition parsing:** Incoming `tools` array would be ignored (extra fields allowed but not processed)

2. **No tool call response parsing:** Upstream responses with `tool_calls` in assistant messages are not extracted or handled

3. **No tool execution loop:** 
   - No registry of callable tools
   - No logic to execute tools and feed results back to model
   - No `tool_call_id` correlation between request/response

4. **Content coercion loses structure:** `coerce_model_content_to_text()` flattens all content to plain text, discarding any tool-related structure

5. **No tool result message type:** OpenAI uses `role: "tool"` with `tool_call_id` - not supported

### Required Additions

```python
# New models needed:
class ToolFunction(BaseModel):
    name: str
    description: str
    parameters: dict  # JSON Schema

class ToolDefinition(BaseModel):
    type: str = "function"
    function: ToolFunction

class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: dict  # {name, arguments}

# GatewayChatRequest additions:
tools: list[ToolDefinition] | None = None
tool_choice: str | dict | None = None

# ChatMessage additions:
tool_calls: list[ToolCall] | None = None
tool_call_id: str | None = None  # For tool response messages
```

---

## Summary

| Capability | Status | Notes |
|------------|--------|-------|
| Project-scoped RAG collections | ✅ Implemented | Per-project Qdrant collections with filtering |
| Namespace isolation | ⚠️ Partial | Metadata field exists, no dedicated API |
| Project-specific model aliases | ✅ Implemented | 3 discovery mechanisms, exposed at /v1/models |
| Coding mode selection | ✅ Implemented | 6-layer priority resolution |
| OpenAI tool calling schema | ❌ Missing | No tools/tool_calls fields |
| Tool execution engine | ❌ Missing | No tool registry or execution loop |
| Tool response handling | ❌ Missing | No role:"tool" support |
