# Qonduit Router Slots Architecture

## Overview

The Qonduit router supports **multiple independent llama.cpp inference slots**, each representing a separate Docker container running `llama-server`. This replaces the previous single-instance architecture where only one container could run at a time.

---

## Current Single-Instance Behavior (Legacy)

Before slots, the router had:

- **One fixed container name**: `llama_server`
- **One fixed port**: 8080
- **One fixed base URL**: `http://192.168.5.5:8080`
- **One model at a time**: Launching a model stopped/removed the old container
- **Status/logs/ready**: All targeted only the single container

All existing single-slot endpoints (`/api/v1/qonduit-router/launch`, `/stop`, `/status`, `/logs`, etc.) are preserved and **automatically map to the `primary` slot** for backward compatibility.

---

## New Slot Model

A **slot** is a user-created llama.cpp deployment endpoint. Each slot has:

| Field | Type | Description |
|---|---|---|
| `slot_id` | string | Unique identifier (lowercase letters, numbers, dash, underscore) |
| `display_name` | string | Human-readable name |
| `purpose` | string | Use case: `primary`, `openhands`, `utility`, `testing`, `embeddings` |
| `container_name` | string | Docker container name (unique across slots) |
| `host` | string | Host address (e.g., `192.168.5.5`) |
| `host_port` | int | Host-facing port (unique across slots) |
| `internal_port` | int | Container-internal port (usually same as host_port) |
| `endpoint_base` | string | Base URL (e.g., `http://192.168.5.5:8080`) |
| `openai_base` | string | OpenAI-compatible API URL (e.g., `http://192.168.5.5:8080/v1`) |
| `model` | string/null | Loaded model filename (null if none loaded) |
| `context_size` | int | Context window size |
| `gpu_devices` | string | GPU assignment: `"all"` or `"0,1,2"` |
| `tensor_split` | string | Tensor split: `"auto"` or `"2,2"` |
| `embeddings_enabled` | bool | Enable `--embeddings` flag |
| `extra_args` | list | Additional llama-server CLI arguments |
| `running` | bool | Docker container is running (live status) |
| `exists` | bool | Docker container exists (live status) |
| `ready` | bool | `/health` endpoint responds (live status) |
| `created_at` | ISO 8601 | Slot creation timestamp |
| `updated_at` | ISO 8601 | Last config update timestamp |
| `last_started_at` | ISO 8601/null | Last successful launch timestamp |
| `last_stopped_at` | ISO 8601/null | Last stop timestamp |
| `last_error` | string/null | Last error message |

### Example Setup

```
primary-large  → port 8080 → 35B model @ 262k context
openhands      → port 8081 → coding model @ 64k context
utility-7b     → port 8082 → 7B model @ 64k context
utility-13b    → port 8083 → 13B model @ 64k context
testing        → port 8084 → any temporary model
```

---

## Compatibility Mapping

All legacy endpoints automatically map to the **`primary` slot**:

| Legacy Endpoint | Mapped To |
|---|---|
| `GET /api/v1/qonduit-router/status` | `GET /api/v1/qonduit-router/slots/primary` |
| `POST /api/v1/qonduit-router/launch` | `POST /api/v1/qonduit-router/slots/primary/launch` |
| `POST /api/v1/qonduit-router/stop` | `POST /api/v1/qonduit-router/slots/primary/stop` |
| `GET /api/v1/qonduit-router/logs` | `GET /api/v1/qonduit-router/slots/primary/logs` |
| `GET /api/v1/qonduit-router/ready` | `GET /api/v1/qonduit-router/slots/primary/ready` |
| `GET /api/v1/qonduit-router/models` | `GET /api/v1/qonduit-router/slots/primary/models` |
| `GET /api/v1/qonduit-router/context/suggest` | Same (unchanged) |
| `GET /api/v1/qonduit-router/health` | Same (unchanged) |
| `GET /api/v1/qonduit-router/gpu` | Same (unchanged) |

The web console continues to work without frontend changes.

---

## Data Storage

**Config file**: `router_slots.json`

**Location** (in order of priority):
1. `$QONDUIT_ROUTER_DATA_DIR/router_slots.json` (env-configurable)
2. `/app/data/router_slots.json` (default)
3. `/opt/qonduit-router/data/router_slots.json` (host deployment)

**Default slot**: If no config exists, exactly one default slot is created:

```json
{
  "slot_id": "primary",
  "display_name": "Primary",
  "purpose": "primary",
  "container_name": "llama_server",
  "host": "192.168.5.5",
  "host_port": 8080,
  "internal_port": 8080,
  "endpoint_base": "http://192.168.5.5:8080",
  "openai_base": "http://192.168.5.5:8080/v1",
  "model": null,
  "context_size": 65536,
  "gpu_devices": "all",
  "tensor_split": "auto",
  "embeddings_enabled": true,
  "extra_args": [],
  "created_at": "...",
  "updated_at": "...",
  "last_started_at": null,
  "last_stopped_at": null,
  "last_error": null
}
```

The primary slot uses the legacy container name `llama_server` for backward
compatibility with existing Qonduit Android/web console logs and legacy router
behavior. Additional slots use generated names like `llama_server_openhands`.

### GPU Auto-Detection

When `gpu_devices` is `"all"`, the router auto-detects **usable** GPUs by:
1. Running `nvidia-smi` to collect GPU info
2. Excluding GPUs with `memory_total_mib` below the threshold (default: 8192 MiB / 8 GiB)
3. Excluding GPUs whose names match the exclude regex (default: `K620|Quadro K620`)

With current hardware (Tesla P100s + Quadro K620):
- **Usable GPUs**: `0,2,3,4,5,6,7` (Tesla P100 16GB)
- **Excluded**: GPU 1 (Quadro K620 2GB — low memory)

**Environment variables**:

| Variable | Default | Description |
|---|---|---|
| `QONDUIT_GPU_MIN_TOTAL_MIB` | `8192` | Min GPU memory (MiB) to be considered usable |
| `QONDUIT_GPU_EXCLUDE_NAME_REGEX` | `K620\|Quadro K620` | Regex to exclude GPUs by name |
| `QONDUIT_DEFAULT_GPU_DEVICES` | `auto` | `"auto"` for detection, or explicit list like `"0,2,3"` |

### Data Directory

The slot config file (`router_slots.json`) is stored at:

1. `$QONDUIT_ROUTER_DATA_DIR/router_slots.json` (env-configurable)
2. `/opt/qonduit-router-api/data/router_slots.json` (default for host deployment)

The directory is created lazily on first use — **not** at module import time.

**Atomic writes**: Config is written to a temporary file, synced, then atomically renamed to prevent corruption.

---

## Safety Behavior

### Launch Safety
- **Launching one slot does NOT stop other slots**
- Only the target slot's existing container is stopped/removed before launch
- Model file existence is validated before launch
- Port availability is checked before launch
- GPU availability is checked via `nvidia-smi`
- Preflight warnings are non-blocking; errors block launch

### Delete Safety
- **Cannot delete the `primary` slot without `force=true`**
- **Cannot delete a running slot without `force=true`**
- Container removal is opt-in (`remove_container=true` query param)

### Update Safety
- Immutable fields (`host_port`, `internal_port`, `container_name`, `gpu_devices`, `tensor_split`, `model`, `context_size`) cannot be changed while a slot is running, unless `force=true`
- Safe metadata fields (`display_name`, `purpose`) can be updated while running

---

## Endpoint Design

### Slot CRUD

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/qonduit-router/slots` | List all slots with live status |
| `POST` | `/api/v1/qonduit-router/slots` | Create a new slot |
| `GET` | `/api/v1/qonduit-router/slots/{slot_id}` | Get a specific slot |
| `PATCH` | `/api/v1/qonduit-router/slots/{slot_id}` | Update slot config |
| `DELETE` | `/api/v1/qonduit-router/slots/{slot_id}` | Delete a slot |

### Slot Lifecycle

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/qonduit-router/slots/{slot_id}/launch` | Launch a model in a slot |
| `POST` | `/api/v1/qonduit-router/slots/{slot_id}/stop` | Stop a slot's container |
| `POST` | `/api/v1/qonduit-router/slots/{slot_id}/restart` | Restart a slot |

### Slot Status & Diagnostics

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/qonduit-router/slots/{slot_id}/ready` | Check if slot health endpoint responds |
| `GET` | `/api/v1/qonduit-router/slots/{slot_id}/models` | Proxy `/v1/models` from slot |
| `GET` | `/api/v1/qonduit-router/slots/{slot_id}/logs` | Stream container logs |
| `POST` | `/api/v1/qonduit-router/slots/{slot_id}/preflight` | Pre-flight check for launch |

### System Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/qonduit-router/endpoints` | Simplified endpoint list |
| `GET` | `/api/v1/qonduit-router/gpu` | `nvidia-smi` summary |
| `GET` | `/api/v1/qonduit-router/slot-templates` | Suggested slot templates |

---

## Validation Rules

| Field | Rules |
|---|---|
| `slot_id` | Lowercase letters, numbers, dash, underscore only. Unique across slots. |
| `display_name` | Required (or derived from `slot_id`) |
| `container_name` | Required and safe. Unique across slots. |
| `host_port` | Valid integer. Unique across slots. |
| `internal_port` | Valid integer |
| `model` | Must exist on disk before launch |
| `context_size` | Must be positive |
| `gpu_devices` | `"all"` or comma-separated GPU IDs (e.g., `"0,1,2"`) |
| `tensor_split` | `"auto"` or comma-separated numeric values |
| `extra_args` | List of strings |

---

## Validation Commands

### Full update cycle

```bash
cd /opt/qonduit-repo
./update.sh
```

### GPU detection

```bash
BASE=http://127.0.0.1:5001
curl -s $BASE/api/v1/qonduit-router/gpu | python3 -m json.tool
```

**Expected**: `usable_gpu_devices` excludes K620, `excluded_gpus` includes index 1 / Quadro K620, `default_gpu_devices` is the usable P100 set.

### List slots

```bash
curl -s $BASE/api/v1/qonduit-router/slots | python3 -m json.tool
```

**Expected**: Primary slot exists with `container_name: "llama_server"`, slot status includes `effective_gpu_devices`.

### Create OpenHands slot (without hardcoding P100s)

```bash
curl -s -X POST $BASE/api/v1/qonduit-router/slots \
  -H "Content-Type: application/json" \
  -d '{
    "slot_id": "openhands",
    "display_name": "OpenHands",
    "purpose": "openhands",
    "host_port": 8081,
    "context_size": 65536,
    "gpu_devices": "all",
    "tensor_split": "auto",
    "embeddings_enabled": false
  }' | python3 -m json.tool
```

### Preflight check

```bash
curl -s -X POST $BASE/api/v1/qonduit-router/slots/openhands/preflight \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3.6-35B-A3B-UD-Q5_K_XL.gguf",
    "context_size": 65536,
    "gpu_devices": "all",
    "tensor_split": "auto",
    "embeddings_enabled": false
  }' | python3 -m json.tool
```

**Expected**: `requested_gpu_devices = "all"`, `effective_gpu_devices = "0,2,3,4,5,6,7"` on current hardware, K620 excluded, primary container remains `llama_server`.

### Launch a slot

```bash
curl -s -X POST $BASE/api/v1/qonduit-router/slots/openhands/launch \
  -H "Content-Type: application/json" \
  -d '{
    "model": "YOUR_MODEL.gguf",
    "context_size": 65536,
    "gpu_devices": "all",
    "tensor_split": "auto",
    "embeddings_enabled": false
  }' | python3 -m json.tool
```

### Check slot readiness

```bash
curl -s $BASE/api/v1/qonduit-router/slots/openhands/ready | python3 -m json.tool
```

### List all endpoints

```bash
curl -s $BASE/api/v1/qonduit-router/endpoints | python3 -m json.tool
```

### Query OpenAI-compatible models from a slot

```bash
curl -s http://127.0.0.1:8081/v1/models | python3 -m json.tool
```

### Stop a slot

```bash
curl -s -X POST $BASE/api/v1/qonduit-router/slots/openhands/stop | python3 -m json.tool
```

### Verify primary slot is still running

```bash
curl -s $BASE/api/v1/qonduit-router/slots/primary | python3 -m json.tool
```

### Backward compatibility — legacy endpoints

```bash
curl -s $BASE/api/v1/qonduit-router/status | python3 -m json.tool
curl -i $BASE/api/v1/qonduit-router/logs
```

**Expected**: Legacy endpoints map to primary slot, primary uses `container_name: "llama_server"`.

---

## Architecture Files

| File | Description |
|---|---|
| `qonduit_slots.py` | Slot config storage: load, save, validate, CRUD operations |
| `qonduit_docker_helpers.py` | Slot-aware Docker helpers: container lifecycle, GPU, ports |
| `qonduit_router_api_slots.py` | New multi-slot API endpoints |
| `qonduit_router_api.py` | Main router — wraps new endpoints, preserves legacy endpoints |
| `tests/test_slots.py` | 75 unit tests covering all phases |

---

## Known Limitations

1. **VRAM estimation is approximate**: Preflight checks use heuristics, not exact llama.cpp VRAM math. Multiple containers may still fail due to actual VRAM usage.
2. **No automatic slot scheduling**: The router does not automatically balance slots across GPUs. Users must configure `gpu_devices` per slot.
3. **No slot health auto-healing**: If a container crashes, the slot's `running` status will be `false` until the user restarts it.
4. **No resource limits**: Docker resource limits (memory, CPU) are not enforced per-slot by default.
5. **No slot migration**: Slots are tied to specific host/GPU configuration.
6. **Web console frontend not updated**: New slot endpoints are available via API; the web console still uses the legacy single-slot endpoints.
