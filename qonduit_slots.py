"""
Qonduit Router — slot configuration storage & management.

Handles persistent slot configuration with atomic JSON writes,
validation, and CRUD operations.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── Configuration ────────────────────────────────────────────────────────────

# Default data directory for host deployment
_DEFAULT_DATA_DIR = Path("/opt/qonduit-router-api/data")

# Lazy-initialized data dir (resolved at first use, not at import time)
_QONDUIT_ROUTER_DATA_DIR: Path | None = None
_QONDUIT_SLOTS_FILE: Path | None = None

# Default host that slots connect to by default
_QONDUIT_DEFAULT_HOST = os.getenv("QONDUIT_DEFAULT_HOST", "192.168.5.5")

# Legacy container name for primary slot
_PRIMARY_CONTAINER_NAME = "llama_server"

# ── Parallel slots & KV cache constants ──────────────────────────────────────

_ALLOWED_CACHE_TYPES: list[str] = [
    "f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1",
]

_DEFAULT_CACHE_TYPE_K = "f16"
_DEFAULT_CACHE_TYPE_V = "f16"

_DEFAULT_PARALLEL_SLOTS = 1
_MIN_PARALLEL_SLOTS = 1
_MAX_PARALLEL_SLOTS = 16

# ── Batch & micro-batch constants ────────────────────────────────────────────

_DEFAULT_BATCH_SIZE = 8192
_DEFAULT_UBATCH_SIZE = 2048

_BATCH_SIZE_OPTIONS: list[int] = [512, 1024, 2048, 4096, 8192]
_UBATCH_SIZE_OPTIONS: list[int] = [256, 512, 1024, 2048]


def _validate_batch_size(value: object) -> tuple[int | None, str | None]:
    """Validate and normalize a batch_size value.

    Returns (normalized_value, error_string).
    - ``None`` value means "reset to default".
    """
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, "batch_size must be a positive integer"
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None, None
        try:
            value = int(stripped)
        except ValueError:
            return None, "batch_size must be a positive integer"
    if not isinstance(value, int):
        return None, "batch_size must be a positive integer"
    if value <= 0:
        return None, "batch_size must be a positive integer"
    return value, None


def _validate_ubatch_size(value: object) -> tuple[int | None, str | None]:
    """Validate and normalize a ubatch_size value.

    Returns (normalized_value, error_string).
    - ``None`` value means "reset to default".
    """
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, "ubatch_size must be a positive integer"
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None, None
        try:
            value = int(stripped)
        except ValueError:
            return None, "ubatch_size must be a positive integer"
    if not isinstance(value, int):
        return None, "ubatch_size must be a positive integer"
    if value <= 0:
        return None, "ubatch_size must be a positive integer"
    return value, None


# Byte multipliers per element for KV cache estimate
_KV_CACHE_BYTES: dict[str, float] = {
    "f32": 4.0,
    "f16": 2.0,
    "bf16": 2.0,
    "q8_0": 1.0,
    "q5_0": 0.625,
    "q5_1": 0.625,
    "q4_0": 0.5,
    "q4_1": 0.5,
    "iq4_nl": 0.5,
}

def _format_bytes_human(nbytes: int) -> str:
    if nbytes < 0:
        return '0 B'
    units = [('GiB', 1 << 30), ('MiB', 1 << 20), ('KiB', 1 << 10), ('B', 1)]
    for unit, divisor in units:
        if nbytes >= divisor:
            value = nbytes / divisor
            if unit == 'GiB':
                return f'{value:.1f} {unit}'
            return f'{int(value)} {unit}'
    return f'{nbytes} B'


# ── Lazy data dir initialization ─────────────────────────────────────────────

def _ensure_data_dir() -> Path:
    """Resolve and return the data directory path (lazy init)."""
    global _QONDUIT_ROUTER_DATA_DIR, _QONDUIT_SLOTS_FILE
    if _QONDUIT_ROUTER_DATA_DIR is not None and _QONDUIT_SLOTS_FILE is not None:
        return _QONDUIT_ROUTER_DATA_DIR

    env_dir = os.getenv("QONDUIT_ROUTER_DATA_DIR")
    if env_dir:
        _QONDUIT_ROUTER_DATA_DIR = Path(env_dir)
    else:
        _QONDUIT_ROUTER_DATA_DIR = _DEFAULT_DATA_DIR

    _QONDUIT_SLOTS_FILE = _QONDUIT_ROUTER_DATA_DIR / "router_slots.json"
    return _QONDUIT_ROUTER_DATA_DIR


_slots_lock = threading.Lock()

# ── Validation helpers ───────────────────────────────────────────────────────

_SLOT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _validate_slot_id(slot_id: str) -> Optional[str]:
    """Return error string if slot_id is invalid, else None."""
    if not slot_id:
        return "slot_id is required"
    if len(slot_id) > 64:
        return "slot_id must be 64 characters or fewer"
    if not _SLOT_ID_RE.match(slot_id):
        return (
            "slot_id must contain only lowercase letters, numbers, dash, "
            "and underscore, and start with a letter or number"
        )
    return None


def _validate_container_name(name: str) -> Optional[str]:
    """Return error string if container_name is invalid, else None."""
    if not name:
        return "container_name is required"
    if len(name) > 128:
        return "container_name must be 128 characters or fewer"
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$", name):
        return (
            "container_name must contain only alphanumeric characters, "
            "dot, dash, and underscore, and start with a letter or number"
        )
    return None


def _validate_port(port: Any, field_name: str = "host_port") -> Optional[str]:
    """Return error string if port is invalid, else None."""
    try:
        port_int = int(port)
    except (ValueError, TypeError):
        return f"{field_name} must be a valid integer"
    if port_int < 1 or port_int > 65535:
        return f"{field_name} must be between 1 and 65535"
    return None


def _validate_gpu_devices(gpu_devices: Any) -> Optional[str]:
    """Return error string if gpu_devices is invalid, else None."""
    if gpu_devices is None:
        return "gpu_devices is required"
    gpu_str = str(gpu_devices).strip()
    if gpu_str == "all":
        return None
    if not re.match(r"^[0-9]+(,[0-9]+)*$", gpu_str):
        return (
            'gpu_devices must be "all" or comma-separated GPU IDs '
            'like "0,1,2"'
        )
    for part in gpu_str.split(","):
        try:
            int(part)
        except ValueError:
            return f"Invalid GPU ID: {part}"
    return None


def _validate_tensor_split(tensor_split: Any) -> Optional[str]:
    """Return error string if tensor_split is invalid, else None.

    None and empty string are valid clear values. The literal "auto" is
    still accepted for callers that already rely on backend auto-splitting.
    """
    if tensor_split is None:
        return None
    ts_str = str(tensor_split).strip()
    if ts_str == "" or ts_str == "auto":
        return None
    parts = ts_str.split(",")
    for part in parts:
        part = part.strip()
        try:
            float(part)
        except ValueError:
            return (
                'tensor_split must be "auto" or '
                "comma-separated numeric values like \"0.5,0.5\""
            )
    return None


def _normalize_tensor_split(tensor_split: Any) -> Optional[str]:
    """Normalize a valid tensor_split value for persistence.

    ``None`` and blank strings clear the saved value. Non-empty values are
    trimmed before they are stored. Callers must validate before normalizing.
    """
    if tensor_split is None:
        return None
    ts_str = str(tensor_split).strip()
    return ts_str or None


def _validate_extra_args(extra_args: Any) -> Optional[str]:
    """Return error string if extra_args is invalid, else None."""
    if extra_args is None:
        return None
    if isinstance(extra_args, list):
        for item in extra_args:
            if not isinstance(item, str):
                return "extra_args must be a list of strings"
    else:
        return "extra_args must be a list of strings"
    return None


def _validate_context_size(context_size: Any) -> Optional[str]:
    """Return error string if context_size is invalid, else None."""
    try:
        ctx = int(context_size)
    except (ValueError, TypeError):
        return "context_size must be a positive integer"
    if ctx <= 0:
        return "context_size must be a positive integer"
    return None


def _validate_embeddings(embeddings_enabled: Any) -> Optional[str]:
    """Return error string if embeddings_enabled is invalid, else None."""
    if embeddings_enabled is None:
        return None
    if not isinstance(embeddings_enabled, bool):
        try:
            bool(embeddings_enabled)
        except (ValueError, TypeError):
            return "embeddings_enabled must be a boolean"
    return None


def _validate_parallel_slots(parallel_slots: Any) -> Optional[str]:
    """Return error string if parallel_slots is invalid, else None.

    Accepts None (treated as default). Rejects bools, floats, strings, etc.
    """
    if parallel_slots is None:
        return None
    if not isinstance(parallel_slots, int) or isinstance(parallel_slots, bool):
        return "parallel_slots must be an integer"
    if parallel_slots < _MIN_PARALLEL_SLOTS or parallel_slots > _MAX_PARALLEL_SLOTS:
        return (
            f"parallel_slots must be between {_MIN_PARALLEL_SLOTS} and "
            f"{_MAX_PARALLEL_SLOTS}"
        )
    return None


def _validate_cache_type(cache_type: Any) -> Optional[str]:
    """Return error string if cache_type is invalid, else None.

    Accepts None or empty string (treated as default f16 reset).
    """
    if cache_type is None:
        return None
    if isinstance(cache_type, (int, float)):
        return "cache_type must be a string"
    val = str(cache_type).strip().lower()
    if not val:
        return None  # empty → reset to default
    if val not in _ALLOWED_CACHE_TYPES:
        return (
            f"cache_type must be one of: {_ALLOWED_CACHE_TYPES}"
        )
    return None


def validate_slot(slot: dict[str, Any]) -> list[dict[str, str]]:
    """Validate a slot dict. Return list of errors (empty if valid)."""
    errors: list[dict[str, str]] = []

    # slot_id
    err = _validate_slot_id(slot.get("slot_id", ""))
    if err:
        errors.append({"field": "slot_id", "message": err})

    # display_name
    display_name = slot.get("display_name")
    if not display_name or not str(display_name).strip():
        errors.append({
            "field": "display_name",
            "message": "display_name is required (or will be derived from slot_id)",
        })

    # container_name
    err = _validate_container_name(slot.get("container_name", ""))
    if err:
        errors.append({"field": "container_name", "message": err})

    # host_port
    err = _validate_port(
        slot.get("host_port"),
        "host_port",
    )
    if err:
        errors.append({"field": "host_port", "message": err})

    # internal_port
    err = _validate_port(
        slot.get("internal_port", 8080),
        "internal_port",
    )
    if err:
        errors.append({"field": "internal_port", "message": err})

    # context_size
    err = _validate_context_size(slot.get("context_size", 65536))
    if err:
        errors.append({"field": "context_size", "message": err})

    # gpu_devices
    err = _validate_gpu_devices(slot.get("gpu_devices"))
    if err:
        errors.append({"field": "gpu_devices", "message": err})

    # tensor_split
    err = _validate_tensor_split(slot.get("tensor_split"))
    if err:
        errors.append({"field": "tensor_split", "message": err})

    # extra_args
    err = _validate_extra_args(slot.get("extra_args"))
    if err:
        errors.append({"field": "extra_args", "message": err})

    # embeddings_enabled
    err = _validate_embeddings(slot.get("embeddings_enabled"))
    if err:
        errors.append({"field": "embeddings_enabled", "message": err})

    # parallel_slots
    err = _validate_parallel_slots(slot.get("parallel_slots"))
    if err:
        errors.append({"field": "parallel_slots", "message": err})

    # cache_type_k
    err = _validate_cache_type(slot.get("cache_type_k"))
    if err:
        errors.append({"field": "cache_type_k", "message": err})

    # cache_type_v
    err = _validate_cache_type(slot.get("cache_type_v"))
    if err:
        errors.append({"field": "cache_type_v", "message": err})

    # batch_size
    bs_valid, bs_err = _validate_batch_size(slot.get("batch_size"))
    if bs_err:
        errors.append({"field": "batch_size", "message": bs_err})

    # ubatch_size
    us_valid, us_err = _validate_ubatch_size(slot.get("ubatch_size"))
    if us_err:
        errors.append({"field": "ubatch_size", "message": us_err})

    # Cross-validate: ubatch_size must not exceed batch_size.
    # Missing/null values are accepted above as reset-to-default, so compare the
    # effective defaults here rather than raw None values.
    if not bs_err and not us_err:
        effective_bs = bs_valid or _DEFAULT_BATCH_SIZE
        effective_us = us_valid or _DEFAULT_UBATCH_SIZE
        if effective_us > effective_bs:
            errors.append({
                "field": "ubatch_size",
                "message": "ubatch_size must not exceed batch_size",
            })

    return errors


# ── Default slot ─────────────────────────────────────────────────────────────

def _build_default_slot() -> dict[str, Any]:
    """Build the default primary slot with legacy container name."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "slot_id": "primary",
        "display_name": "Primary",
        "purpose": "primary",
        "container_name": _PRIMARY_CONTAINER_NAME,
        "host": _QONDUIT_DEFAULT_HOST,
        "host_port": 8080,
        "internal_port": 8080,
        "endpoint_base": f"http://{_QONDUIT_DEFAULT_HOST}:8080",
        "openai_base": f"http://{_QONDUIT_DEFAULT_HOST}:8080/v1",
        "model": None,
        "context_size": 65536,
        "gpu_devices": "all",
        "tensor_split": "auto",
        "embeddings_enabled": True,
        "extra_args": [],
        "parallel_slots": _DEFAULT_PARALLEL_SLOTS,
        "cache_type_k": _DEFAULT_CACHE_TYPE_K,
        "cache_type_v": _DEFAULT_CACHE_TYPE_V,
        "batch_size": _DEFAULT_BATCH_SIZE,
        "ubatch_size": _DEFAULT_UBATCH_SIZE,
        "running": False,
        "exists": False,
        "ready": False,
        "created_at": now,
        "updated_at": now,
        "last_started_at": None,
        "last_stopped_at": None,
        "last_error": None,
    }


# ── File I/O (atomic writes) ────────────────────────────────────────────────

def _write_slots_file(slots: list[dict[str, Any]]) -> None:
    """Atomically write slots list to disk."""
    data_dir = _ensure_data_dir()
    slots_file = data_dir / "router_slots.json"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(data_dir),
            prefix=".router_slots_",
            suffix=".json",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(slots, f, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, str(slots_file))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError:
        pass


def _migrate_primary_container_name(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Migrate primary slot from llama_server_primary to llama_server.

    If the primary slot has container_name 'llama_server_primary',
    migrate it to the legacy-compatible name 'llama_server'.
    Only migrates if no other non-primary slot uses 'llama_server'.

    NOTE: This is called from within load_slots() which holds _slots_lock,
    so we call _write_slots_file() directly to avoid deadlock via save_slots().
    """
    migrated = False
    for i, s in enumerate(slots):
        if s.get("slot_id") == "primary":
            if s.get("container_name") == "llama_server_primary":
                # Check no other non-primary slot uses 'llama_server'
                conflict = any(
                    s2.get("container_name") == _PRIMARY_CONTAINER_NAME
                    and s2.get("slot_id") != "primary"
                    for s2 in slots
                )
                if not conflict:
                    slots[i]["container_name"] = _PRIMARY_CONTAINER_NAME
                    migrated = True

    if migrated:
        _write_slots_file(slots)

    return slots


def load_slots() -> list[dict[str, Any]]:
    """Load slots from disk. Creates default primary if none exist."""
    data_dir = _ensure_data_dir()
    slots_file = data_dir / "router_slots.json"

    with _slots_lock:
        if not slots_file.exists():
            default = [_build_default_slot()]
            _write_slots_file(default)
            return default

        try:
            with open(slots_file, "r") as f:
                data = json.load(f)
            if isinstance(data, list) and all(
                isinstance(s, dict) and "slot_id" in s for s in data
            ):
                # Ensure every slot has all fields (migrate old configs)
                result = []
                default_template = _build_default_slot()
                for slot in data:
                    merged = {**default_template, **slot}
                    # Re-derive derived fields from persisted values
                    merged["endpoint_base"] = (
                        f"http://{merged.get('host', _QONDUIT_DEFAULT_HOST)}:{merged.get('host_port', 8080)}"
                    )
                    merged["openai_base"] = (
                        f"http://{merged.get('host', _QONDUIT_DEFAULT_HOST)}:{merged.get('host_port', 8080)}/v1"
                    )
                    result.append(merged)

                # Apply container name migration
                result = _migrate_primary_container_name(result)

                if result:
                    return result
                # Empty list — recreate with default
        except (json.JSONDecodeError, OSError):
            pass

        # Corrupt or empty file — recreate
        default = [_build_default_slot()]
        _write_slots_file(default)
        return default


def save_slots(slots: list[dict[str, Any]]) -> None:
    """Persist slots to disk."""
    with _slots_lock:
        _write_slots_file(slots)


def _get_all_slots_list() -> list[dict[str, Any]]:
    """Get raw slots list (caller must hold lock or use load_slots)."""
    slots = load_slots()
    # Update live status for all slots
    return [slot_to_live_status(s) for s in slots]


def get_slot(slot_id: str) -> Optional[dict[str, Any]]:
    """Get a single slot by slot_id, with live status. Returns None if not found."""
    slots = _get_all_slots_list()
    for s in slots:
        if s["slot_id"] == slot_id:
            return s
    return None


def _find_slot_index(slot_id: str) -> int:
    """Find index of slot_id in stored slots. Returns -1 if not found."""
    slots = load_slots()
    for i, s in enumerate(slots):
        if s["slot_id"] == slot_id:
            return i
    return -1


# ── CRUD operations ──────────────────────────────────────────────────────────

def create_slot(payload: dict[str, Any]) -> tuple[dict[str, Any], Optional[str]]:
    """
    Create a new slot. Returns (slot_dict, error_string).
    On success, error_string is None.
    """
    slot_id = (payload.get("slot_id") or "").strip().lower()
    err = _validate_slot_id(slot_id)
    if err:
        return {}, err

    # Check for duplicate slot_id
    slots = load_slots()
    for s in slots:
        if s["slot_id"] == slot_id:
            return {}, "duplicate_slot"

    # Derive container_name if not provided
    container_name = (payload.get("container_name") or "").strip()
    if not container_name:
        container_name = f"llama_server_{slot_id}"
    err = _validate_container_name(container_name)
    if err:
        return {}, err

    # Check duplicate container_name
    for s in slots:
        if s["container_name"] == container_name:
            return {}, "duplicate_container_name"

    # Host / ports
    host = (payload.get("host") or _QONDUIT_DEFAULT_HOST).strip()
    host_port = payload.get("host_port", 8081)
    err = _validate_port(host_port, "host_port")
    if err:
        return {}, err["message"] if isinstance(err, dict) else err

    # Check duplicate host_port
    for s in slots:
        if s["host_port"] == host_port:
            return {}, "duplicate_port"

    internal_port = int(payload.get("internal_port", host_port))
    err = _validate_port(internal_port, "internal_port")
    if err:
        return {}, err["message"] if isinstance(err, dict) else err

    context_size = int(payload.get("context_size", 65536))
    err = _validate_context_size(context_size)
    if err:
        return {}, err

    gpu_devices = payload.get("gpu_devices", "all")
    err = _validate_gpu_devices(gpu_devices)
    if err:
        return {}, err

    tensor_split = payload.get("tensor_split", "auto")
    err = _validate_tensor_split(tensor_split)
    if err:
        return {}, err
    tensor_split = _normalize_tensor_split(tensor_split)

    extra_args = payload.get("extra_args", [])
    err = _validate_extra_args(extra_args)
    if err:
        return {}, err

    embeddings_enabled = payload.get("embeddings_enabled", False)

    # Parallel slots
    if "parallel_slots" in payload:
        parallel_slots = payload["parallel_slots"]
        if parallel_slots is None or (
            isinstance(parallel_slots, str) and not parallel_slots.strip()
        ):
            parallel_slots = _DEFAULT_PARALLEL_SLOTS
        elif not isinstance(parallel_slots, int) or isinstance(parallel_slots, bool):
            return {}, "invalid_slot"
        if parallel_slots < _MIN_PARALLEL_SLOTS or parallel_slots > _MAX_PARALLEL_SLOTS:
            return {}, "invalid_slot"
    else:
        parallel_slots = _DEFAULT_PARALLEL_SLOTS

    # Cache types
    if "cache_type_k" in payload:
        raw_k = payload["cache_type_k"]
        if raw_k is None or (isinstance(raw_k, str) and not raw_k.strip()):
            cache_type_k = _DEFAULT_CACHE_TYPE_K
        else:
            cache_type_k = str(raw_k).strip().lower()
            if cache_type_k not in _ALLOWED_CACHE_TYPES:
                return {}, "invalid_slot"
    else:
        cache_type_k = _DEFAULT_CACHE_TYPE_K

    if "cache_type_v" in payload:
        raw_v = payload["cache_type_v"]
        if raw_v is None or (isinstance(raw_v, str) and not raw_v.strip()):
            cache_type_v = _DEFAULT_CACHE_TYPE_V
        else:
            cache_type_v = str(raw_v).strip().lower()
            if cache_type_v not in _ALLOWED_CACHE_TYPES:
                return {}, "invalid_slot"
    else:
        cache_type_v = _DEFAULT_CACHE_TYPE_V

    # Batch size
    if "batch_size" in payload:
        raw_bs = payload["batch_size"]
        if raw_bs is None or (isinstance(raw_bs, str) and not raw_bs.strip()):
            batch_size = _DEFAULT_BATCH_SIZE
        else:
            bs_valid, bs_err = _validate_batch_size(raw_bs)
            if bs_err:
                return {}, bs_err
            batch_size = bs_valid
    else:
        batch_size = _DEFAULT_BATCH_SIZE

    # Micro-batch size
    if "ubatch_size" in payload:
        raw_us = payload["ubatch_size"]
        if raw_us is None or (isinstance(raw_us, str) and not raw_us.strip()):
            ubatch_size = _DEFAULT_UBATCH_SIZE
        else:
            us_valid, us_err = _validate_ubatch_size(raw_us)
            if us_err:
                return {}, us_err
            ubatch_size = us_valid
    else:
        ubatch_size = _DEFAULT_UBATCH_SIZE

    # Cross-validate: ubatch_size must not exceed batch_size
    if ubatch_size > batch_size:
        return {}, "ubatch_size must not exceed batch_size"

    display_name = (payload.get("display_name") or slot_id).strip()
    if not display_name:
        display_name = slot_id

    purpose = (payload.get("purpose") or "custom").strip()
    if not purpose:
        purpose = "custom"

    now = datetime.now(timezone.utc).isoformat()
    endpoint_base = f"http://{host}:{host_port}"
    openai_base = f"http://{host}:{host_port}/v1"

    new_slot = {
        "slot_id": slot_id,
        "display_name": display_name,
        "purpose": purpose,
        "container_name": container_name,
        "host": host,
        "host_port": host_port,
        "internal_port": internal_port,
        "endpoint_base": endpoint_base,
        "openai_base": openai_base,
        "model": None,
        "context_size": context_size,
        "gpu_devices": gpu_devices,
        "tensor_split": tensor_split,
        "embeddings_enabled": bool(embeddings_enabled),
        "extra_args": extra_args,
        "parallel_slots": parallel_slots,
        "cache_type_k": cache_type_k,
        "cache_type_v": cache_type_v,
        "batch_size": batch_size,
        "ubatch_size": ubatch_size,
        "running": False,
        "exists": False,
        "ready": False,
        "created_at": now,
        "updated_at": now,
        "last_started_at": None,
        "last_stopped_at": None,
        "last_error": None,
    }

    slots.append(new_slot)
    save_slots(slots)

    return new_slot, None


def update_slot(
    slot_id: str,
    payload: dict[str, Any],
    force: bool = False,
) -> tuple[dict[str, Any], Optional[str]]:
    """
    Update a slot's configuration. Returns (updated_slot, error_string).
    """
    idx = _find_slot_index(slot_id)
    if idx == -1:
        return {}, "slot_not_found"

    slots = load_slots()
    slot = slots[idx]

    # If running, reject changes to immutable fields unless force
    if slot.get("running", False) and not force:
        immutable_fields = [
            "host_port",
            "internal_port",
            "container_name",
            "gpu_devices",
            "tensor_split",
            "model",
            "context_size",
        ]
        for field in immutable_fields:
            if field in payload:
                return {}, "slot_running_requires_force"

    # Apply updates
    updatable = [
        "display_name",
        "purpose",
        "host",
        "embeddings_enabled",
        "extra_args",
        "model",
        "context_size",
        "gpu_devices",
        "tensor_split",
        "parallel_slots",
        "cache_type_k",
        "cache_type_v",
        "batch_size",
        "ubatch_size",
    ]

    for field in updatable:
        if field in payload:
            value = payload[field]
            if field == "tensor_split":
                err = _validate_tensor_split(value)
                if err:
                    return {}, err
                value = _normalize_tensor_split(value)
            # PATCH explicit null/empty for cache types → reset to default f16
            elif field == "cache_type_k":
                if value is None or (isinstance(value, str) and not value.strip()):
                    value = _DEFAULT_CACHE_TYPE_K
                else:
                    value = str(value).strip().lower()
                    if value not in _ALLOWED_CACHE_TYPES:
                        return {}, "invalid_slot"
            elif field == "cache_type_v":
                if value is None or (isinstance(value, str) and not value.strip()):
                    value = _DEFAULT_CACHE_TYPE_V
                else:
                    value = str(value).strip().lower()
                    if value not in _ALLOWED_CACHE_TYPES:
                        return {}, "invalid_slot"
            # PATCH explicit null/empty for parallel_slots → reset to default 1
            elif field == "parallel_slots":
                if value is None or (isinstance(value, str) and not value.strip()):
                    value = _DEFAULT_PARALLEL_SLOTS
                elif isinstance(value, bool):
                    return {}, "invalid_slot"
                else:
                    try:
                        value = int(value)
                    except (ValueError, TypeError):
                        return {}, "invalid_slot"
                if value < _MIN_PARALLEL_SLOTS or value > _MAX_PARALLEL_SLOTS:
                    return {}, "invalid_slot"
            # PATCH explicit null/empty for batch_size → reset to default 8192
            elif field == "batch_size":
                if value is None or (isinstance(value, str) and not value.strip()):
                    value = _DEFAULT_BATCH_SIZE
                else:
                    v_valid, v_err = _validate_batch_size(value)
                    if v_err:
                        return {}, v_err
                    value = v_valid
            # PATCH explicit null/empty for ubatch_size → reset to default 2048
            elif field == "ubatch_size":
                if value is None or (isinstance(value, str) and not value.strip()):
                    value = _DEFAULT_UBATCH_SIZE
                else:
                    v_valid, v_err = _validate_ubatch_size(value)
                    if v_err:
                        return {}, v_err
                    value = v_valid
            slot[field] = value

    # Re-derive derived fields if host or host_port changed
    if "host" in payload or "host_port" in payload:
        host = slot.get("host", _QONDUIT_DEFAULT_HOST)
        port = slot.get("host_port", 8080)
        slot["endpoint_base"] = f"http://{host}:{port}"
        slot["openai_base"] = f"http://{host}:{port}/v1"

    slot["updated_at"] = datetime.now(timezone.utc).isoformat()

    # Validate updated slot
    validation_errors = validate_slot(slot)
    if validation_errors:
        return {}, f"validation_failed: {validation_errors}"

    slots[idx] = slot
    save_slots(slots)

    return slot_to_live_status(slot), None


def delete_slot(
    slot_id: str,
    remove_container: bool = False,
    force: bool = False,
) -> tuple[bool, Optional[str]]:
    """
    Delete a slot. Returns (success, error_string).
    """
    idx = _find_slot_index(slot_id)
    if idx == -1:
        return False, "slot_not_found"

    slots = load_slots()
    slot = slots[idx]

    # Cannot delete primary without force
    if slot_id == "primary" and not force:
        return False, "primary_slot_delete_requires_force"

    # Cannot delete running slot without force
    if slot.get("running", False) and not force:
        return False, "slot_running_requires_force"

    # Optionally remove container
    if remove_container:
        from qonduit_docker_helpers import remove_slot_container
        ok, err = remove_slot_container(slot)
        if not ok:
            return False, err or "docker_error"

    slots.pop(idx)
    save_slots(slots)
    return True, None


# ── Live status helpers ──────────────────────────────────────────────────────

def _normalize_slot_fields(slot: dict[str, Any]) -> dict[str, Any]:
    """Ensure new fields have defaults for slots created before this feature."""
    if "parallel_slots" not in slot:
        slot["parallel_slots"] = _DEFAULT_PARALLEL_SLOTS
    if "cache_type_k" not in slot:
        slot["cache_type_k"] = _DEFAULT_CACHE_TYPE_K
    if "cache_type_v" not in slot:
        slot["cache_type_v"] = _DEFAULT_CACHE_TYPE_V
    if "batch_size" not in slot:
        slot["batch_size"] = _DEFAULT_BATCH_SIZE
    if "ubatch_size" not in slot:
        slot["ubatch_size"] = _DEFAULT_UBATCH_SIZE
    return slot


def slot_to_live_status(slot: dict[str, Any]) -> dict[str, Any]:
    """
    Merge persisted slot config with live Docker/status information.
    Returns a new dict with updated running/exists/ready fields
    and effective_gpu_devices resolved from gpu_devices.

    New fields (parallel_slots, cache_type_k, cache_type_v) are
    normalized to their defaults when absent from persisted data.
    """
    result = _normalize_slot_fields(dict(slot))
    try:
        from qonduit_docker_helpers import (
            container_exists,
            container_running,
            check_slot_ready,
            resolve_gpu_devices,
        )
        result["exists"] = container_exists(result)
        result["running"] = container_running(result)
        if result["running"]:
            result["ready"] = check_slot_ready(result)
        # Resolve effective GPU devices
        original_gpu = result.get("gpu_devices", "all")
        result["effective_gpu_devices"] = resolve_gpu_devices(original_gpu)
    except ImportError:
        # GPU resolution not available; use raw value
        result["effective_gpu_devices"] = result.get("gpu_devices", "all")
    except Exception:
        result["effective_gpu_devices"] = result.get("gpu_devices", "all")
    return result


def collect_slot_errors(slots: list[dict[str, Any]]) -> None:
    """Update last_error for all slots by checking Docker status."""
    try:
        from qonduit_docker_helpers import container_status
        for i, slot in enumerate(slots):
            status = container_status(slot)
            if status.get("last_error"):
                slots[i]["last_error"] = status["last_error"]
        save_slots(slots)
    except (ImportError, Exception):
        pass


# ── Convenience: check uniqueness constraints ───────────────────────────────

def check_port_available(port: int) -> bool:
    """Check if a port is free across all configured slots."""
    slots = load_slots()
    return not any(s.get("host_port") == port for s in slots)


def check_container_name_available(name: str) -> bool:
    """Check if a container name is free across all configured slots."""
    slots = load_slots()
    return not any(s.get("container_name") == name for s in slots)


# ── Module-level initialization ─────────────────────────────────────────────

# Ensure default slot exists at module load time
_load_slots_initial = load_slots()
