# Agentic Gateway Implementation Plan

## Overview

This document outlines the phased implementation plan to add OpenAI-style tool calling and enhanced project-scoped RAG capabilities to the Qonduit Memory Gateway.

---

## 1. Target API Contract (OpenAI-Compatible Tool Calling)

### Extended GatewayChatRequest

```python
class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[ContentPart] | None
    name: str | None = None
    tool_call_id: str | None = None  # For tool responses
    tool_calls: list[ToolCall] | None = None  # For assistant messages


class ToolCall(BaseModel):
    id: str
    type: Literal["function"]
    function: FunctionCall


class FunctionCall(BaseModel):
    name: str
    arguments: str  # JSON string


class Tool(BaseModel):
    type: Literal["function"]
    function: FunctionDefinition


class FunctionDefinition(BaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, Any]  # JSON Schema


class GatewayChatRequest(BaseModel):
    conversation_id: str | None
    project_id: str | None
    messages: list[ChatMessage]
    model: str
    tools: list[Tool] | None = None
    tool_choice: str | dict | None = None  # "auto", "none", "required", or specific
    context_size: int | None
    max_tokens: int = 2048
    temperature: float = 0.7
    stream: bool = False
    user: str | None
    rag_collection: str | None
    mode: str | None
    
    model_config = {"extra": "allow"}
```

### Extended Response Format

```json
{
  "id": "chatcmpl-qonduit-{uuid}",
  "object": "chat.completion",
  "created": <timestamp>,
  "model": "<model>",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_abc123",
        "type": "function",
        "function": {
          "name": "search_knowledge_base",
          "arguments": "{\"query\": \"...\"}"
        }
      }]
    },
    "finish_reason": "tool_calls"
  }],
  "usage": {}
}
```

---

## 2. What Is Missing for OpenAI-Style Tool Calling

### A. Request Parsing

1. **No `tools` field support**: Current `GatewayChatRequest` doesn't accept tool definitions
2. **No `tool_choice` handling**: No mechanism to control tool selection behavior
3. **Message structure limitations**: `ChatMessage` only has `role` and `content`, missing:
   - `tool_calls` array for assistant messages
   - `tool_call_id` for tool response messages
   - `name` field for tool messages

### B. Response Processing

1. **Tool call parsing**: Need to extract `tool_calls` from upstream response
2. **Finish reason handling**: Must recognize `tool_calls` finish reason
3. **Streaming support**: Need to stream `delta.tool_calls` in SSE format

### C. Tool Execution Loop

1. **No execution engine**: No mechanism to execute tool functions
2. **No tool registry**: No way to register/lookup available tools
3. **No recursive loop**: After tool execution, need to send results back to LLM

### D. Built-in Tools to Implement

1. **`search_knowledge_base`**: Query project RAG collection
2. **`read_file`**: Read file content from repository
3. **`list_directory`**: List files in a directory
4. **`run_command`**: Execute shell command (with safety constraints)
5. **`store_memory`**: Persist structured information to conversation memory

---

## 3. What Is Missing for Project-Scoped RAG

### A. Namespace Support

Current implementation uses flat collection per project: `qonduit_rag__{project_id}`

**Missing:**
1. **Sub-namespaces**: No way to organize documents within a project (e.g., by feature, module, user)
2. **Metadata filtering**: Limited filtering beyond `project_id`, `user_id`, `namespace`
3. **Collection listing per namespace**: Can't enumerate sub-collections

### B. Document Management

1. **No document update**: Can't update existing documents; must delete and re-add
2. **No document metadata schema**: Metadata is arbitrary dict, no validation
3. **No chunking strategy configuration**: Chunking is done in `ingest_repo.py` with fixed logic
4. **No document lifecycle**: No TTL, versioning, or expiration

### C. Retrieval Enhancements

1. **No hybrid search**: Only vector similarity, no keyword/BM25 fallback
2. **No reranking**: Results returned in raw similarity order
3. **No query expansion**: Single query vector, no multi-query or HyDE
4. **Fixed top-k**: Same K for all queries, no dynamic adjustment

### D. Ingestion Improvements

1. **No incremental updates**: Full re-index on each ingestion
2. **No change detection**: Doesn't track which files changed since last ingestion
3. **No ingestion scheduling**: Manual enqueue or webhook only
4. **No priority queue**: FIFO only, no priority-based processing

---

## 4. Minimal Phased Implementation Plan

### Phase 1: Foundation - Tool Calling Infrastructure (Week 1)

**Goal**: Accept and forward tool definitions, parse tool calls from responses

#### Tasks:
1. Extend `ChatMessage` model with `tool_calls`, `tool_call_id`, `name` fields
2. Add `Tool`, `FunctionDefinition`, `FunctionCall`, `ToolCall` models
3. Extend `GatewayChatRequest` with `tools` and `tool_choice` fields
4. Update chat endpoint to pass `tools` and `tool_choice` to upstream
5. Parse `tool_calls` from upstream response
6. Update response format to include `tool_calls` in message
7. Handle `finish_reason: "tool_calls"`

#### Files to modify:
- `app/main.py`: Models, request/response handling
- `app/store.py`: Store tool calls in conversation state

#### Validation:
- Send request with `tools` parameter, verify passthrough to upstream
- Verify tool call parsing in response
- Test streaming with tool calls

---

### Phase 2: Tool Execution Engine (Week 2)

**Goal**: Execute built-in tools and loop back to LLM

#### Tasks:
1. Create `app/tools/` module with base tool interface
2. Implement `ToolRegistry` class for tool lookup
3. Implement built-in tools:
   - `search_knowledge_base`: Query RAG with project scoping
   - `read_file`: Read file from filesystem
   - `list_directory`: List directory contents
   - `store_memory`: Save structured data to conversation metadata
4. Add tool execution loop in chat endpoint:
   - Detect tool calls in response
   - Execute each tool
   - Append tool results as `role: "tool"` messages
   - Recursively call LLM until no more tool calls
5. Add recursion limit to prevent infinite loops
6. Update conversation state to include tool interactions

#### Files to create:
- `app/tools/__init__.py`
- `app/tools/registry.py`
- `app/tools/builtin.py`

#### Files to modify:
- `app/main.py`: Tool execution loop
- `app/rag.py`: Expose search function for tool use
- `app/store.py`: Store tool execution history

#### Validation:
- Test each built-in tool independently
- Test full tool execution loop
- Verify conversation state includes tool history

---

### Phase 3: Enhanced RAG Namespaces (Week 3)

**Goal**: Add namespace support within projects

#### Tasks:
1. Update `ProjectScopedRagService` to support explicit namespaces
2. Add `namespace` parameter to all RAG operations
3. Implement `list_namespaces(project_id)` function
4. Add metadata schema validation for documents
5. Update RAG endpoints to accept `namespace` parameter
6. Modify `search_knowledge_base` tool to accept `namespace` filter
7. Add namespace-aware collection management

#### Files to modify:
- `app/rag.py`: Namespace support in service layer
- `app/main.py`: RAG endpoints with namespace parameter
- `app/tools/builtin.py`: Update search tool with namespace option

#### Validation:
- Create documents in different namespaces
- Search within specific namespace
- List namespaces for a project

---

### Phase 4: Streaming Tool Calls (Week 4)

**Goal**: Stream tool call deltas in real-time

#### Tasks:
1. Parse streaming `delta.tool_calls` from upstream
2. Accumulate tool call fragments
3. Emit SSE chunks with tool call deltas
4. Execute tools after complete tool call received
5. Stream tool execution progress (optional)
6. Resume streaming LLM response after tool execution

#### Files to modify:
- `app/main.py`: Streaming handler for tool calls

#### Validation:
- Test streaming with tool calls
- Verify delta accumulation
- Test interleaved content and tool calls

---

### Phase 5: Advanced RAG Features (Week 5)

**Goal**: Improve retrieval quality

#### Tasks:
1. Implement hybrid search (vector + keyword)
2. Add simple reranking (MMR or diversity scoring)
3. Implement query expansion (multi-query averaging)
4. Add dynamic top-k based on score threshold
5. Implement document deduplication in results

#### Files to modify:
- `app/rag.py`: Enhanced search algorithms

#### Validation:
- Compare retrieval quality before/after
- Test edge cases (empty results, low scores)

---

### Phase 6: Incremental Ingestion (Week 6)

**Goal**: Efficient repository updates

#### Tasks:
1. Track file hashes from previous ingestion
2. Detect changed/added/removed files
3. Incremental add/update/delete in Qdrant
4. Add ingestion scheduling (cron-like)
5. Implement priority queue for ingestion jobs

#### Files to modify:
- `app/ingestion.py`: Incremental logic
- `app/ingest_repo.py`: Hash tracking

#### Validation:
- Ingest repo, modify one file, re-ingest
- Verify only changed file was processed
- Test scheduled ingestion

---

## 5. Risk Mitigation

### Tool Safety

1. **Command execution restrictions**: Whitelist allowed commands, block dangerous patterns
2. **File access limits**: Restrict to project directories, no absolute paths outside workspace
3. **Timeout enforcement**: Max execution time per tool call
4. **Resource limits**: Max file size, max output size

### Conversation State Bloat

1. **Tool message trimming**: Apply same trimming logic to tool messages
2. **Summary inclusion**: Include tool call summaries in rolling summary
3. **History pagination**: Optional separate storage for detailed tool history

### Upstream Compatibility

1. **Graceful degradation**: If upstream doesn't support tools, fall back to text-only
2. **Feature detection**: Probe upstream capabilities on startup
3. **Configuration flags**: Enable/disable tool features per deployment

---

## 6. Testing Strategy

### Unit Tests

- Tool parsing and serialization
- Tool execution with mocked dependencies
- RAG namespace operations
- Message trimming with tool messages

### Integration Tests

- Full tool execution loop with test LLM
- End-to-end RAG workflow
- Streaming tool call handling

### Validation Scripts

Extend existing scripts in `scripts/`:
- `validate_tool_calling.py`: Test tool call flow
- `validate_rag_namespaces.py`: Test namespace features
- `validate_incremental_ingestion.py`: Test delta updates

---

## 7. Backward Compatibility

All changes are backward compatible:

1. **New fields are optional**: Existing clients continue to work
2. **Tool calling is opt-in**: Only triggered when `tools` parameter provided
3. **Namespace is optional**: Defaults to project-level collection
4. **Extra fields allowed**: `model_config = {"extra": "allow"}` preserves unknown fields

---

## 8. Success Metrics

### Tool Calling

- [ ] Tool definitions accepted and forwarded
- [ ] Tool calls parsed from responses
- [ ] Built-in tools execute correctly
- [ ] Tool results loop back to LLM
- [ ] Streaming tool calls work

### RAG Enhancement

- [ ] Namespaces isolate documents
- [ ] Search respects namespace filter
- [ ] Hybrid search improves recall
- [ ] Incremental ingestion reduces processing time by >80%

### Overall

- [ ] No regression in existing chat functionality
- [ ] Latency increase <100ms for non-tool requests
- [ ] Tool execution completes within 5s average
