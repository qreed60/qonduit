"""
Gateway Settings: persistent JSON-backed store for gateway configuration and
prompt templates.

Data file: <GATEWAY_DATA_DIR>/gateway_settings.json
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("qonduit.memory_gateway.settings")

SETTINGS_FILE_NAME = "gateway_settings.json"

# ---------------------------------------------------------------------------
# Built-in prompt templates (immutable, protected from deletion)
# ---------------------------------------------------------------------------

_BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "general",
        "name": "General",
        "description": "Balanced assistant behavior for normal chat.",
        "system_prompt": (
            "You are a helpful, accurate, and concise assistant. "
            "Answer questions directly and truthfully. "
            "If you do not know the answer, say so."
        ),
        "instruction_prompt": (
            "Be helpful, accurate, and concise. "
            "Prioritize correctness over verbosity."
        ),
        "built_in": True,
    },
    {
        "id": "coding",
        "name": "Coding",
        "description": (
            "Precise code and debugging behavior. "
            "Asks for logs/errors only when needed. Prefers concrete commands."
        ),
        "system_prompt": (
            "You are an expert software engineer. "
            "Focus on precise, actionable code advice. "
            "Ask for logs or error output only when necessary. "
            "Prefer concrete commands over guessing. "
            "Do not hallucinate APIs or libraries."
        ),
        "instruction_prompt": (
            "Provide precise code solutions. Ask for logs/errors only when needed. "
            "Prefer concrete commands and minimal guessing."
        ),
        "built_in": True,
    },
    {
        "id": "thinking",
        "name": "Thinking",
        "description": (
            "Deliberate, analytical reasoning. "
            "Useful for planning and troubleshooting."
        ),
        "system_prompt": (
            "You are a thoughtful analytical assistant. "
            "Reason step by step before answering. "
            "Explain your reasoning clearly. "
            "Be thorough when planning or troubleshooting."
        ),
        "instruction_prompt": (
            "Think deliberately and analytically. "
            "Show your reasoning. Useful for planning and troubleshooting."
        ),
        "built_in": True,
    },
    {
        "id": "creative",
        "name": "Creative",
        "description": "Brainstorming, writing, and ideation.",
        "system_prompt": (
            "You are a creative assistant. "
            "Help with brainstorming, writing, and ideation. "
            "Be imaginative and encouraging. "
            "Explore multiple angles and possibilities."
        ),
        "instruction_prompt": (
            "Be creative and imaginative. Help with brainstorming, writing, and ideation. "
            "Explore multiple angles and possibilities."
        ),
        "built_in": True,
    },
    {
        "id": "concise",
        "name": "Concise",
        "description": "Short, direct answers without elaboration.",
        "system_prompt": (
            "You are a concise assistant. "
            "Give short, direct answers. "
            "Do not elaborate unless asked. "
            "Prioritize brevity."
        ),
        "instruction_prompt": (
            "Be short and direct. Provide minimal elaboration unless explicitly asked."
        ),
        "built_in": True,
    },
    {
        "id": "rag_factual_lookup",
        "name": "RAG Factual Lookup",
        "description": (
            "Prioritize retrieved context. "
            "Answer directly when retrieved context contains the answer. "
            "Do not use public/general knowledge over retrieved context."
        ),
        "system_prompt": (
            "You are a factual lookup assistant. "
            "Prioritize the retrieved context above all else. "
            "If the retrieved context contains the answer, answer directly from it. "
            "If the retrieved context does not contain the answer, say so. "
            "Do not use general or public knowledge to answer when retrieved context is provided."
        ),
        "instruction_prompt": (
            "Prioritize retrieved context. Answer directly from context. "
            "Do not use general knowledge over retrieved context."
        ),
        "built_in": True,
    },
]

# ---------------------------------------------------------------------------
# Default settings shape
# ---------------------------------------------------------------------------

def _default_settings() -> dict[str, Any]:
    return {
        "version": 1,
        "active_prompt_template_id": "general",
        "defaults": {
            "model": None,
            "max_tokens": 2048,
            "temperature": 0.7,
            "stream": True,
            "rag_enabled": False,
            "rag_project_id": "default",
            "rag_collection": None,
            "rag_search_limit": 4,
        },
        "prompt_templates": [dict(t) for t in _BUILTIN_TEMPLATES],
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _data_dir() -> Path:
    root = Path(__file__).resolve().parent.parent.parent / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _settings_path() -> Path:
    return _data_dir() / SETTINGS_FILE_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str) -> str:
    """Turn a name into a URL-safe slug for template IDs."""
    slug = value.lower().strip()
    slug = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in slug)
    slug = slug.strip("-_")
    return slug or uuid.uuid4().hex[:8]


def _find_template(templates: list[dict], template_id: str) -> dict | None:
    for t in templates:
        if t.get("id") == template_id:
            return t
    return None


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load_settings() -> dict[str, Any]:
    """Load settings from disk, creating defaults if the file is missing."""
    path = _settings_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _ensure_settings_shape(data)
            logger.info("settings_loaded path=%s", path)
            return data
        except Exception:
            logger.exception("settings_load_failed_reading_file")

    settings = _default_settings()
    save_settings(settings)
    logger.info("settings_created_default path=%s", path)
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    """Persist settings to disk."""
    settings["updated_at"] = _now_iso()
    path = _settings_path()
    path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("settings_saved path=%s", path)


# ---------------------------------------------------------------------------
# Shape validation (defensive: tolerate new fields)
# ---------------------------------------------------------------------------

def _ensure_settings_shape(data: dict) -> dict[str, Any]:
    """Backfill missing top-level keys; do NOT remove unknown keys."""
    data.setdefault("version", 1)
    data.setdefault("active_prompt_template_id", "general")
    data.setdefault("defaults", {})
    data["defaults"].setdefault("model", None)
    data["defaults"].setdefault("max_tokens", 2048)
    data["defaults"].setdefault("temperature", 0.7)
    data["defaults"].setdefault("stream", True)
    data["defaults"].setdefault("rag_enabled", False)
    data["defaults"].setdefault("rag_project_id", "default")
    data["defaults"].setdefault("rag_collection", None)
    data["defaults"].setdefault("rag_search_limit", 4)
    data.setdefault("prompt_templates", [])
    data.setdefault("created_at", _now_iso())
    data.setdefault("updated_at", _now_iso())

    # Ensure every template has timestamps
    for tmpl in data["prompt_templates"]:
        tmpl.setdefault("built_in", False)
        if not tmpl.get("created_at"):
            tmpl["created_at"] = _now_iso()
        if not tmpl.get("updated_at"):
            tmpl["updated_at"] = _now_iso()

    return data


# ---------------------------------------------------------------------------
# Public helper API (used by router and chat integration)
# ---------------------------------------------------------------------------

def list_builtin_templates() -> list[dict[str, Any]]:
    """Return only built-in templates."""
    return [dict(t) for t in _BUILTIN_TEMPLATES]


def list_all_templates(settings: dict) -> list[dict[str, Any]]:
    """Return all templates (built-in + custom)."""
    return [dict(t) for t in settings.get("prompt_templates", [])]


def get_template(settings: dict, template_id: str) -> dict | None:
    """Get a single template by ID."""
    templates = settings.get("prompt_templates", [])
    return _find_template(templates, template_id)


def create_template(settings: dict, name: str, description: str,
                    system_prompt: str, instruction_prompt: str) -> dict:
    """Create a new custom template and append to settings."""
    template_id = _slugify(name)
    templates = settings.get("prompt_templates", [])

    # If slug collides with a built-in, append a UUID suffix
    if _find_template(templates, template_id):
        template_id = f"{template_id}-{uuid.uuid4().hex[:6]}"

    new_tmpl: dict[str, Any] = {
        "id": template_id,
        "name": name,
        "description": description,
        "system_prompt": system_prompt,
        "instruction_prompt": instruction_prompt,
        "built_in": False,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    templates.append(new_tmpl)
    settings["prompt_templates"] = templates
    settings["updated_at"] = _now_iso()
    logger.info("template_created id=%s name=%s", template_id, name)
    return new_tmpl


def update_template(settings: dict, template_id: str,
                    name: str | None = None,
                    description: str | None = None,
                    system_prompt: str | None = None,
                    instruction_prompt: str | None = None) -> dict:
    """Update a custom template. Rejects built-in edits."""
    templates = settings.get("prompt_templates", [])
    target = _find_template(templates, template_id)
    if target is None:
        raise KeyError(f"template_not_found: {template_id}")
    if target.get("built_in"):
        raise ValueError("built_in_templates_cannot_be_edited")

    if name is not None:
        target["name"] = name
    if description is not None:
        target["description"] = description
    if system_prompt is not None:
        target["system_prompt"] = system_prompt
    if instruction_prompt is not None:
        target["instruction_prompt"] = instruction_prompt

    target["updated_at"] = _now_iso()
    settings["prompt_templates"] = templates
    settings["updated_at"] = _now_iso()
    logger.info("template_updated id=%s", template_id)
    return target


def delete_template(settings: dict, template_id: str) -> None:
    """Delete a custom template. Rejects built-in deletion."""
    templates = settings.get("prompt_templates", [])
    target = _find_template(templates, template_id)
    if target is None:
        raise KeyError(f"template_not_found: {template_id}")
    if target.get("built_in"):
        raise ValueError("built_in_templates_cannot_be_deleted")

    templates = [t for t in templates if t.get("id") != template_id]
    settings["prompt_templates"] = templates
    settings["updated_at"] = _now_iso()
    logger.info("template_deleted id=%s", template_id)

    # If deleted template was active, fall back to general
    if settings.get("active_prompt_template_id") == template_id:
        settings["active_prompt_template_id"] = "general"
        logger.info("active_fallback_to_general previous=%s", template_id)


def activate_template(settings: dict, template_id: str) -> dict:
    """Set the active prompt template."""
    templates = settings.get("prompt_templates", [])
    target = _find_template(templates, template_id)
    if target is None:
        raise KeyError(f"template_not_found: {template_id}")

    settings["active_prompt_template_id"] = template_id
    settings["updated_at"] = _now_iso()
    logger.info("template_activated id=%s name=%s", template_id, target.get("name", ""))
    return target


def duplicate_template(settings: dict, template_id: str) -> dict:
    """Duplicate a template into a new custom template."""
    templates = settings.get("prompt_templates", [])
    source = _find_template(templates, template_id)
    if source is None:
        raise KeyError(f"template_not_found: {template_id}")

    new_tmpl: dict[str, Any] = dict(source)
    new_tmpl["id"] = f"{source['id']}-copy-{uuid.uuid4().hex[:6]}"
    new_tmpl["name"] = f"{source['name']} (Copy)"
    new_tmpl["built_in"] = False
    new_tmpl["created_at"] = _now_iso()
    new_tmpl["updated_at"] = _now_iso()

    templates.append(new_tmpl)
    settings["prompt_templates"] = templates
    settings["updated_at"] = _now_iso()
    logger.info("template_duplicated id=%s new_id=%s", template_id, new_tmpl["id"])
    return new_tmpl


def reset_settings() -> dict[str, Any]:
    """Reset to defaults but preserve custom templates."""
    path = _settings_path()
    existing = load_settings()

    defaults = _default_settings()

    # Preserve custom templates (non-built-in)
    custom_templates = [
        t for t in existing.get("prompt_templates", [])
        if not t.get("built_in", False)
    ]

    # Restore defaults
    settings: dict[str, Any] = _default_settings()
    settings["prompt_templates"] = custom_templates
    settings["version"] = 1

    save_settings(settings)
    logger.info("settings_reset_preserving_custom_templates count=%d", len(custom_templates))
    return settings


def get_active_template(settings: dict) -> dict | None:
    """Return the currently active prompt template."""
    active_id = settings.get("active_prompt_template_id", "general")
    return get_template(settings, active_id)
