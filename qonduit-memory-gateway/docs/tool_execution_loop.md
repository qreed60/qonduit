# Tool Execution Loop Documentation

## Overview

Phase C implements server-side tool execution for retrieval-only tools. When the model returns `finish_reason="tool_calls"`, the gateway:

1. Parses tool calls from the model response
2. Executes supported tools within the resolved project scope
3. Appends tool results as `role="tool"` messages
4. Re-calls the model until it returns a final answer without tool_calls

## Supported Tools

### 1. `retrieve_project_context`

Retrieves relevant context from the project's RAG collection.

**Parameters:**
- `query` (string, required): The search query
- `top_k` (integer, optional, default=4): Number of results to return
- `user_id` (string, optional): User filter for scoped results

**Example call:**
```json
{
  "name": "retrieve_project_context",
  "arguments": {
    "query": "MainActivity lifecycle",
    "top_k": 3
  }
}
```

### 2. `search_project_files`

Searches for files matching a glob pattern in the project.

**Parameters:**
- `pattern` (string, required): Glob pattern (e.g., "*.py", "**/test_*.java")
- `max_results` (integer, optional, default=20): Maximum files to return

**Example call:**
```json
{
  "name": "search_project_files",
  "arguments": {
    "pattern": "**/MainActivity.java",
    "max_results": 10
  }
}
```

### 3. `list_project_files`

Lists files in a project directory with optional filtering.

**Parameters:**
- `directory` (string, optional): Directory path relative to project root
- `extensions` (array[string], optional): File extensions to filter (e.g., [".py", ".java"])
- `max_results` (integer, optional, default=50): Maximum files to return

**Example call:**
```json
{
  "name": "list_project_files",
  "arguments": {
    "directory": "src/main",
    "extensions": [".java"],
    "max_results": 30
  }
}
```

## Complete Request/Response Example

### Initial Request

```json
POST /v1/chat/completions
{
  "model": "my-project-alias",
  "messages": [
    {"role": "user", "content": "What does MainActivity do in this project?"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "retrieve_project_context",
        "description": "Retrieve relevant context from project documentation",
        "parameters": {
          "type": "object",
          "properties": {
            "query": {"type": "string", "description": "Search query"},
            "top_k": {"type": "integer", "description": "Number of results"}
          },
          "required": ["query"]
        }
      }
    }
  ],
  "tool_choice": "auto"
}
```

### Model Tool Call Response (Intermediate)

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "",
        "tool_calls": [
          {
            "id": "call_xyz789",
            "type": "function",
            "function": {
              "name": "retrieve_project_context",
              "arguments": "{\"query\": \"MainActivity purpose functionality\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ]
}
```

### Tool Execution Result (Internal)

The gateway executes the tool and creates a tool message:

```json
{
  "role": "tool",
  "content": "[1] (score: 0.847)\nMainActivity is the main entry point of the Android application...",
  "tool_call_id": "call_xyz789"
}
```

### Final Model Response (After Tool Execution)

```json
{
  "id": "chatcmpl-def456",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Based on the project documentation, MainActivity serves as the main entry point for the Android application. It initializes the core components and sets up the primary user interface."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 150,
    "completion_tokens": 45,
    "total_tokens": 195
  }
}
```

## Tool Execution Flow

```
┌─────────────┐
│   Client    │
│  Request    │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────┐
│  Gateway receives request with      │
│  tools and sends to model           │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Model responds with                │
│  finish_reason="tool_calls"         │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Gateway parses tool_calls and      │
│  executes each tool within the      │
│  resolved project scope             │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Gateway appends tool results as    │
│  role="tool" messages               │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Gateway re-calls model with        │
│  extended conversation              │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  Model returns final answer with    │
│  finish_reason="stop"               │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────┐
│  Response   │
│  to Client  │
└─────────────┘
```

## Implementation Details

### Project Scope Enforcement

All tools execute strictly within the resolved `project_id`:
- `retrieve_project_context` queries only the project's RAG collection
- `search_project_files` searches under `PROJECTS_ROOT/{project_id}`
- `list_project_files` lists files within the project directory

### Iteration Limits

To prevent infinite loops:
- Maximum 5 tool execution iterations per request
- Loop exits when model returns no tool_calls or reaches limit

### Backward Compatibility

- Plain chat requests (without tools) work unchanged
- Tool execution only triggers when:
  - `tools` are provided in request
  - Model returns `tool_calls` in response
  - Non-streaming mode (streaming tool support TBD)

### Error Handling

- Unknown tools return an error message
- Tool execution errors are captured and returned as tool results with `is_error=true`
- Errors are logged but don't crash the request

## Limitations

1. **Streaming not supported**: Tool execution loop only works for non-streaming requests
2. **Read-only tools**: Only retrieval/search tools implemented (no write/build/shell tools)
3. **Fixed tool set**: Tools must be pre-registered in `TOOL_HANDLERS`
4. **No parallel execution**: Tools execute sequentially

## Testing

Run the validation script:

```bash
python scripts/validate_phaseC.py
```

Tests cover:
- Plain chat still works
- Tool call execution
- Final assistant response after tool execution
- Cross-project isolation
