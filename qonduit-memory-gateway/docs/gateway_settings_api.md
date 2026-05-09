# Gateway Settings API

Persistent JSON-backed store for gateway configuration and prompt templates.

## Data File

`<GATEWAY_DATA_DIR>/gateway_settings.json`

Default `GATEWAY_DATA_DIR` is `/app/data` inside the container.

## Endpoints

### GET /v1/gateway/settings

Return the full settings object.

```bash
curl -s http://localhost:8090/v1/gateway/settings | python3 -m json.tool
```

Response:

```json
{
  "version": 1,
  "active_prompt_template_id": "general",
  "defaults": {
    "model": null,
    "max_tokens": 2048,
    "temperature": 0.7,
    "stream": true,
    "rag_enabled": false,
    "rag_project_id": "default",
    "rag_collection": null,
    "rag_search_limit": 4
  },
  "prompt_templates": [ ... ],
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z"
}
```

### GET /v1/gateway/settings/defaults

Return only the `defaults` section.

```bash
curl -s http://localhost:8090/v1/gateway/settings/defaults | python3 -m json.tool
```

### GET /v1/gateway/settings/defaults?model=...&max_tokens=...&temperature=...

Set one or more default values. Only provided keys are updated.

```bash
curl -s -X POST "http://localhost:8090/v1/gateway/settings/defaults?model=qwen2.5-7b&max_tokens=4096" \
  | python3 -m json.tool
```

### GET /v1/gateway/settings/active-template

Return the currently active prompt template.

```bash
curl -s http://localhost:8090/v1/gateway/settings/active-template | python3 -m json.tool
```

### POST /v1/gateway/settings/defaults

Update one or more default values.

```bash
curl -s -X POST http://localhost:8090/v1/gateway/settings/defaults \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen2.5-7b", "max_tokens": 4096}' \
  | python3 -m json.tool
```

### GET /v1/gateway/prompt-templates/builtin

Return only built-in templates (immutable).

```bash
curl -s http://localhost:8090/v1/gateway/prompt-templates/builtin | python3 -m json.tool
```

### GET /v1/gateway/prompt-templates

Return all templates (built-in + custom).

```bash
curl -s http://localhost:8090/v1/gateway/prompt-templates | python3 -m json.tool
```

### POST /v1/gateway/prompt-templates

Create a new custom template.

```bash
curl -s -X POST http://localhost:8090/v1/gateway/prompt-templates \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Custom Template",
    "description": "A custom template for testing",
    "system_prompt": "You are a helpful assistant.",
    "instruction_prompt": "Be helpful and concise."
  }' \
  | python3 -m json.tool
```

### GET /v1/gateway/prompt-templates/<template_id>

Get a specific template by ID.

```bash
curl -s http://localhost:8090/v1/gateway/prompt-templates/coding | python3 -m json.tool
```

### PUT /v1/gateway/prompt-templates/<template_id>

Update a custom template (built-in templates cannot be edited).

```bash
curl -s -X PUT http://localhost:8090/v1/gateway/prompt-templates/my-template \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Updated Name",
    "description": "Updated description",
    "system_prompt": "Updated system prompt",
    "instruction_prompt": "Updated instruction prompt"
  }' \
  | python3 -m json.tool
```

### DELETE /v1/gateway/prompt-templates/<template_id>

Delete a custom template (built-in templates cannot be deleted).

```bash
curl -s -X DELETE http://localhost:8090/v1/gateway/prompt-templates/my-template \
  | python3 -m json.tool
```

### POST /v1/gateway/prompt-templates/<template_id>/activate

Activate a template (set as the active prompt template).

```bash
curl -s -X POST http://localhost:8090/v1/gateway/prompt-templates/coding/activate \
  | python3 -m json.tool
```

### POST /v1/gateway/prompt-templates/<template_id>/duplicate

Duplicate a template into a new custom template.

```bash
curl -s -X POST http://localhost:8090/v1/gateway/prompt-templates/coding/duplicate \
  | python3 -m json.tool
```

### POST /v1/gateway/settings/reset

Reset settings to defaults, preserving custom templates.

```bash
curl -s -X POST http://localhost:8090/v1/gateway/settings/reset \
  | python3 -m json.tool
```

## Built-in Templates

| ID | Name | Description |
|---|---|---|
| `general` | General | Balanced assistant behavior for normal chat |
| `coding` | Coding | Precise code and debugging behavior |
| `thinking` | Thinking | Deliberate, analytical reasoning |
| `creative` | Creative | Brainstorming, writing, and ideation |
| `concise` | Concise | Short, direct answers without elaboration |
| `rag_factual_lookup` | RAG Factual Lookup | Prioritize retrieved context |

## Template Fields

| Field | Type | Description |
|---|---|---|
| `id` | string | URL-safe slug, unique across all templates |
| `name` | string | Human-readable name |
| `description` | string | Brief description |
| `system_prompt` | string | System prompt content (preferred field) |
| `instruction_prompt` | string | Fallback instruction prompt |
| `built_in` | boolean | True for built-in templates (immutable) |
| `created_at` | ISO 8601 | Creation timestamp |
| `updated_at` | ISO 8601 | Last update timestamp |

## Template Application Priority

When the chat endpoint processes a request:

1. If the request **already contains** a `system` or `developer` role message → the gateway does **not** inject a template system prompt (the request's own message takes precedence).
2. If an **active template** is configured → its `system_prompt` (or `instruction_prompt` as fallback) is used.
3. Falls back to **mode-based prompt** (`system_prompt_for_mode(mode)`).
4. Falls back to the hardcoded `DEFAULT_SYSTEM_PROMPT`.

## Settings Defaults

| Key | Default | Description |
|---|---|---|
| `model` | null | Default model name (null = use request model) |
| `max_tokens` | 2048 | Default max output tokens |
| `temperature` | 0.7 | Default temperature |
| `stream` | true | Default streaming enabled |
| `rag_enabled` | false | Default RAG disabled |
| `rag_project_id` | "default" | Default RAG project ID |
| `rag_collection` | null | Default RAG collection |
| `rag_search_limit` | 4 | Default number of RAG search results |

## Data Persistence

Settings are persisted as JSON at `$GATEWAY_DATA_DIR/gateway_settings.json`. The directory is created automatically if it does not exist. The data directory is mounted at `/app/data` inside the container.

To back up or migrate settings:

```bash
docker exec -it <container> cat /app/data/gateway_settings.json > gateway_settings_backup.json
```
