# Native Client Compatibility (Dyad / Android Studio)

## Objective

Use OpenAI-compatible native clients without requiring custom request body fields.

## Model alias strategy

Configure `MODEL_ALIAS_CONFIG` with alias IDs that clients can select directly.

Example:

```json
{
  "android-qonduit": {
    "model": "llama-3.1-8b-instruct",
    "project_id": "android_project",
    "default_mode": "coding",
    "rag_enabled": true
  },
  "dyad-dashboard": {
    "model": "llama-3.1-8b-instruct",
    "project_id": "dashboard_project",
    "default_mode": "chat",
    "rag_enabled": true
  }
}
```

When a request uses an alias model ID:

- gateway resolves project/mode defaults from alias config
- request is still OpenAI-style (`model`, `messages`, `max_tokens`, `temperature`)
- gateway routes upstream using alias target model while preserving client-facing alias semantics

## Endpoint binding strategy (optional)

Configure `ENDPOINT_BINDINGS` to map hostnames to project/model defaults.

Example:

```json
{
  "android.local.example": {
    "project_id": "android_project",
    "model": "llama-3.1-8b-instruct",
    "model_alias": "android-qonduit",
    "default_mode": "coding",
    "rag_enabled": true
  },
  "dyad.local.example": {
    "project_id": "dashboard_project",
    "model": "llama-3.1-8b-instruct",
    "model_alias": "dyad-dashboard",
    "default_mode": "chat",
    "rag_enabled": true
  }
}
```

This supports per-endpoint/provider routing without custom body fields.

## `/v1/models` behavior

`/v1/models` includes:

- upstream model list from llama-compatible backend
- configured alias models from `MODEL_ALIAS_CONFIG`
- optional alias models from `ENDPOINT_BINDINGS` (`model_alias`)

## Dyad usage

1. Point Dyad to gateway base URL.
2. Refresh model list via `/v1/models`.
3. Select alias model (e.g., `dyad-dashboard`).
4. Send normal OpenAI chat requests.

## Android Studio usage

1. Point provider URL to gateway endpoint.
2. Refresh models and select alias (e.g., `android-qonduit`).
3. Continue standard OpenAI chat completion flow.

No custom `project_id` fields are required when aliases or endpoint bindings are configured.
