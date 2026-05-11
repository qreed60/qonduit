"""Tool settings: persistent, validated storage for tool enable/disable state.

Data file: ``<GATEWAY_DATA_DIR>/tool_settings.json``

Settings shape::

    {
        "global": {
            "rag_search": true,
            "rag_project_list": true,
            ...
        },
        "per_model": {
            "model-id": {
                "rag_search": true
            }
        },
        "confirmation_mode": "risky-only"
    }

Aliases accepted on input: ``per_model`` → ``perModel``, ``confirmation_mode`` → ``confirmationMode``.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .tools import ALL_KNOWN_TOOLS, SAFE_TOOLS, get_tool

logger = logging.getLogger("qonduit.memory_gateway.tool_settings")

_SETTINGS_FILE_NAME = "tool_settings.json"
_DEFAULT_CONFIRMATION_MODE = "risky-only"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _data_dir() -> Path:
    root = Path(os.getenv("GATEWAY_DATA_DIR", "").strip())
    if not root:
        # Try /app/data first (production path), fall back to ./data
        root = Path("/app/data")
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Permission denied or other OS error – fall back to relative path
        root = Path(__file__).resolve().parent.parent / "data"
        root.mkdir(parents=True, exist_ok=True)
    return root


def _settings_path() -> Path:
    return _data_dir() / _SETTINGS_FILE_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically using temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=".tool_settings_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Default shape
# ---------------------------------------------------------------------------


def _default_settings() -> dict[str, Any]:
    """Create default settings with safe tools enabled, everything else disabled."""
    global_enabled: dict[str, bool] = {}
    for name in sorted(ALL_KNOWN_TOOLS):
        global_enabled[name] = name in SAFE_TOOLS

    return {
        "version": 1,
        "global": global_enabled,
        "perModel": {},
        "confirmationMode": _DEFAULT_CONFIRMATION_MODE,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def _ensure_settings_shape(data: dict) -> dict[str, Any]:
    """Backfill missing keys without removing unknown ones."""
    data.setdefault("version", 1)
    data.setdefault("global", {})
    data.setdefault("perModel", {})
    data.setdefault("confirmationMode", _DEFAULT_CONFIRMATION_MODE)
    data.setdefault("created_at", _now_iso())
    data.setdefault("updated_at", _now_iso())

    # Ensure all known tools have an entry in global
    for name in sorted(ALL_KNOWN_TOOLS):
        data["global"].setdefault(name, name in SAFE_TOOLS)

    return data


def load_settings() -> dict[str, Any]:
    """Load tool settings from disk, creating defaults if missing."""
    path = _settings_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _ensure_settings_shape(data)
                logger.info("tool_settings_loaded path=%s", path)
                return data
        except Exception:
            logger.exception("tool_settings_load_failed_reading_file")

    settings = _default_settings()
    save_settings(settings)
    logger.info("tool_settings_created_default path=%s", path)
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    """Persist settings to disk atomically."""
    settings["updated_at"] = _now_iso()
    _atomic_write_json(_settings_path(), settings)
    logger.info("tool_settings_saved path=%s", _settings_path())


# ---------------------------------------------------------------------------
# Validation helpers used by the PATCH endpoint
# ---------------------------------------------------------------------------


def _validate_tool_name(name: str) -> tuple[bool, str | None]:
    """Validate a tool name against known tools."""
    if not name or not isinstance(name, str):
        return False, "invalid_tool_name"
    if name not in ALL_KNOWN_TOOLS:
        return False, "unknown_tool"
    tool = get_tool(name)
    if tool and tool.get("destructive", False):
        return False, "destructive_tool"
    if tool and not tool.get("backendAvailable", False):
        return False, "tool_unavailable"
    return True, None


def _validate_and_update_settings(
    settings: dict[str, Any],
    patches: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Apply patches to settings with validation.

    Returns (updated_settings, errors).
    """
    errors: list[str] = []

    # Normalise snake_case aliases
    per_model_key = "per_model" if "per_model" in patches else "perModel"
    confirmation_key = (
        "confirmation_mode" if "confirmation_mode" in patches else "confirmationMode"
    )

    global_patch = patches.get("global", {})
    if not isinstance(global_patch, dict):
        errors.append("global must be an object")
        global_patch = {}

    per_model_patch = patches.get(per_model_key, {})
    if not isinstance(per_model_patch, dict):
        errors.append("perModel must be an object")
        per_model_patch = {}

    # Update global
    for tool_name, enabled in global_patch.items():
        if not isinstance(enabled, bool):
            errors.append(f"invalid_boolean for tool '{tool_name}'")
            continue
        ok, reason = _validate_tool_name(tool_name)
        if not ok and enabled:
            # Only reject enabling; rejecting disable is always ok
            errors.append(f"{reason}:{tool_name}")
            continue
        settings["global"][tool_name] = enabled

    # Update per-model
    for model_id, model_tools in per_model_patch.items():
        if not isinstance(model_tools, dict):
            errors.append(f"perModel.{model_id} must be an object")
            continue
        if model_id not in settings["perModel"]:
            settings["perModel"][model_id] = {}
        for tool_name, enabled in model_tools.items():
            if not isinstance(enabled, bool):
                errors.append(f"invalid_boolean for perModel.{model_id}.{tool_name}")
                continue
            ok, reason = _validate_tool_name(tool_name)
            if not ok and enabled:
                errors.append(f"{reason}:{tool_name}")
                continue
            settings["perModel"][model_id][tool_name] = enabled

    # Update confirmation mode
    new_cm = patches.get(confirmation_key, patches.get("confirmationMode", ""))
    if new_cm:
        if new_cm in ("risky-only", "all", "none"):
            settings["confirmationMode"] = new_cm
        else:
            errors.append(f"invalid_confirmation_mode: {new_cm}")

    return settings, errors


def get_effective_tools(
    settings: dict[str, Any],
    model_id: str | None = None,
) -> dict[str, bool]:
    """Return effective global tool enabled map, optionally merged with per-model."""
    result = dict(settings.get("global", {}))
    if model_id and model_id in settings.get("perModel", {}):
        result.update(settings["perModel"][model_id])
    return result
