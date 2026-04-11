# Tool Schema Support

This document describes the OpenAI-style tool calling schema support added to the Qonduit Memory Gateway.

## Supported Request Fields

### `GatewayChatRequest` Extensions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tools` | `list[ToolDefinition]` | No | List of tool definitions available for the model to use |
| `tool_choice` | `str \| dict[str, Any]` | No | Controls how the model selects tools. Can be `"none"`, `"auto"`, `"required"`, or `{"type": "function", "function": {"name": "<tool_name>"}}` |

### `ToolDefinition` Schema

```python
class ToolFunction(BaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, Any] | None = None  # JSON Schema object


class ToolDefinition(BaseModel):
    type: Literal["function"] = "function"
    function: ToolFunction
```

## Supported Message Roles

| Role | Description | Required Fields | Optional Fields |
|------|-------------|-----------------|-----------------|
| `user` | Standard user message | `content` | - |
| `assistant` | Model response | `content` | `tool_calls` |
| `tool` | Tool execution result | `content`, `tool_call_id` | - |
| `system` | System prompt | `content` | - |

## Supported Message Shapes

### User Message (Standard)
```json
{
  "role": "user",
  "content": "What is the weather in San Francisco?"
}
```

### Assistant Message with Tool Calls
```json
{
  "role": "assistant",
  "content": "Let me check the weather.",
  "tool_calls": [
    {
      "id": "call_abc123",
      "type": "function",
      "function": {
        "name": "get_weather",
        "arguments": "{\"location\": \"San Francisco\"}"
      }
    }
  ]
}
```

### Tool Message (Result)
```json
{
  "role": "tool",
  "tool_call_id": "call_abc123",
  "content": "{\"temp\": 72, \"condition\": \"sunny\"}"
}
```

### Full Example Request with Tools

```json
{
  "model": "my-project-alias",
  "messages": [
    {"role": "user", "content": "What is the weather in San Francisco?"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get current weather for a location",
        "parameters": {
          "type": "object",
          "properties": {
            "location": {"type": "string", "description": "City name"}
          },
          "required": ["location"]
        }
      }
    }
  ],
  "tool_choice": "auto"
}
```

## Backward Compatibility

- Plain chat requests without `tools` or `tool_choice` work exactly as before
- Messages without `tool_calls` or `tool_call_id` are handled identically to prior behavior
- Content coercion to text still applies for non-tool messages
- All existing fields (`conversation_id`, `project_id`, `mode`, `rag_collection`, etc.) remain unchanged

## Implementation Notes

### Message Processing

1. **User/Assistant messages**: Content is coerced to plain text using `coerce_model_content_to_text()`
2. **Assistant messages with `tool_calls`**: Content is coerced to text, but `tool_calls` array is preserved and passed to upstream
3. **Tool messages**: Content is kept as string, `tool_call_id` is required and preserved

### Upstream Payload

When `tools` or `tool_choice` are provided in the request:
- They are serialized using Pydantic's `model_dump()` and included in the upstream payload
- The upstream model server (llama-base) receives the full OpenAI-style tool definition format

### Response Handling

- Non-streaming responses: `tool_calls` from upstream are passed through to the client response
- Streaming responses: Infrastructure is in place to collect `tool_calls` deltas (currently placeholder for future implementation)

## Not Yet Implemented

- Streaming `tool_calls` delta collection (infrastructure ready, collection logic pending)
- Tool execution engine (gateway passes tool calls through but does not execute them)
- Tool registry or automatic tool resolution
