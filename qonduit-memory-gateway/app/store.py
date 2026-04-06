from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_PROJECT_ID = "default"


def _data_dir() -> Path:
    root = os.getenv("GATEWAY_DATA_DIR", "/app/data").strip() or "/app/data"
    path = Path(root) / "conversations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sanitize(value: str | None, fallback: str) -> str:
    safe_value = "".join(c for c in (value or "") if c.isalnum() or c in ("-", "_"))
    if not safe_value:
        return fallback
    return safe_value


def _legacy_conv_path(conversation_id: str) -> Path:
    safe_id = _sanitize(conversation_id, "default")
    return _data_dir() / f"{safe_id}.json"


def _project_dir(project_id: str) -> Path:
    safe_project = _sanitize(project_id, DEFAULT_PROJECT_ID)
    path = _data_dir() / safe_project
    path.mkdir(parents=True, exist_ok=True)
    return path


def _conv_path(conversation_id: str, project_id: str) -> Path:
    safe_id = _sanitize(conversation_id, "default")
    return _project_dir(project_id) / f"{safe_id}.json"


def _base_conversation_state(conversation_id: str, project_id: str) -> dict[str, Any]:
    safe_project = _sanitize(project_id, DEFAULT_PROJECT_ID)
    safe_conversation = _sanitize(conversation_id, "default")
    return {
        "project_id": safe_project,
        "conversation_id": safe_conversation,
        "summary": "",
        "recent_messages": [],
        "last_model": None,
        "last_context_size": None,
        "last_mode": None,
        "metadata": {},
    }


def _ensure_state_shape(
    payload: dict[str, Any],
    conversation_id: str,
    project_id: str,
) -> dict[str, Any]:
    state = _base_conversation_state(conversation_id, project_id)
    state.update(payload)
    state["project_id"] = _sanitize(state.get("project_id"), DEFAULT_PROJECT_ID)
    state["conversation_id"] = _sanitize(state.get("conversation_id"), "default")
    if not isinstance(state.get("metadata"), dict):
        state["metadata"] = {}
    if not isinstance(state.get("recent_messages"), list):
        state["recent_messages"] = []
    if not isinstance(state.get("summary"), str):
        state["summary"] = str(state.get("summary", ""))
    return state


def load_conversation(
    conversation_id: str,
    project_id: str = DEFAULT_PROJECT_ID,
) -> dict[str, Any]:
    project_path = _conv_path(conversation_id, project_id)
    if project_path.exists():
        payload = json.loads(project_path.read_text(encoding="utf-8"))
        return _ensure_state_shape(payload, conversation_id, project_id)

    legacy_path = _legacy_conv_path(conversation_id)
    if legacy_path.exists():
        payload = json.loads(legacy_path.read_text(encoding="utf-8"))
        state = _ensure_state_shape(payload, conversation_id, project_id)
        save_conversation(conversation_id, state, project_id=project_id)
        return state

    return _base_conversation_state(conversation_id, project_id)


def save_conversation(
    conversation_id: str,
    payload: dict[str, Any],
    project_id: str = DEFAULT_PROJECT_ID,
) -> None:
    state = _ensure_state_shape(payload, conversation_id, project_id)
    path = _conv_path(state["conversation_id"], state["project_id"])
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
