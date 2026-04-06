# Project-Scoped Isolation

## Overview

The gateway now stores conversations in a strict project namespace to prevent
cross-project memory bleed.

## Resolution order for `project_id`

The gateway resolves project scope in this order:

1. `X-Project-ID` request header
2. `project_id` request body field
3. model alias mapping from `MODEL_ALIAS_CONFIG`
4. host binding map from `PROJECT_HOST_BINDINGS`
5. fallback `DEFAULT_PROJECT_ID` (defaults to `default`)

## Storage layout

Conversation files are stored as:

- `GATEWAY_DATA_DIR/conversations/<project_id>/<conversation_id>.json`

Each state file persists:

- `project_id`
- `conversation_id`
- `summary`
- `recent_messages`
- `last_model`
- `last_context_size`
- `last_mode`
- `metadata` (including rag collection hints and model alias)

## Backward compatibility migration

Legacy flat files at:

- `GATEWAY_DATA_DIR/conversations/<conversation_id>.json`

are still readable. On first load, the gateway automatically normalizes and
writes them into the resolved project namespace.

## Notes

- If no project is explicitly set, all traffic stays in the safe `default`
  namespace.
- Future phases can bind project scope through model aliases/endpoints without
  requiring client-only custom fields.
