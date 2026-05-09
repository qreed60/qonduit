"""
Gateway Settings Router: HTTP endpoints for gateway configuration and
prompt template management.

Prefix: /v1/gateway
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .settings import (
    activate_template,
    create_template,
    delete_template,
    duplicate_template,
    get_active_template,
    get_template,
    list_all_templates,
    list_builtin_templates,
    load_settings,
    reset_settings,
    save_settings,
    update_template,
)

router = APIRouter(prefix="/v1/gateway", tags=["gateway-settings"])
logger = logging.getLogger("qonduit.memory_gateway.settings")

# ---------------------------------------------------------------------------
# Request/Response schemas
# ---------------------------------------------------------------------------


class SettingsDefaultsUpdate(BaseModel):
    """Subset of defaults that can be updated via PUT."""
    model: str | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=131072)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    stream: bool | None = None
    rag_enabled: bool | None = None
    rag_project_id: str | None = None
    rag_collection: str | None = None
    rag_search_limit: int | None = Field(default=None, ge=1, le=64)


class SettingsUpdateRequest(BaseModel):
    """Full settings update request."""
    defaults: SettingsDefaultsUpdate | None = None
    active_prompt_template_id: str | None = None


class TemplateCreateRequest(BaseModel):
    """Create a new custom template."""
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(..., max_length=512)
    system_prompt: str = Field(..., min_length=1)
    instruction_prompt: str = Field(..., min_length=1)


class TemplateUpdateRequest(BaseModel):
    """Update an existing custom template."""
    name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    system_prompt: str | None = None
    instruction_prompt: str | None = None


class SettingsResetRequest(BaseModel):
    """Reset settings request."""
    delete_custom_templates: bool = False


# ---------------------------------------------------------------------------
# Settings endpoints
# ---------------------------------------------------------------------------


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    """Return full current gateway settings."""
    settings = load_settings()
    return settings


@router.put("/settings")
def update_settings(req: SettingsUpdateRequest) -> dict[str, Any]:
    """Update gateway defaults and/or active template."""
    settings = load_settings()

    if req.defaults is not None:
        defaults = settings.get("defaults", {})
        for key, value in req.defaults.model_dump(exclude_none=True).items():
            defaults[key] = value
        settings["defaults"] = defaults

    if req.active_prompt_template_id is not None:
        active_id = req.active_prompt_template_id.strip()
        templates = settings.get("prompt_templates", [])
        if not any(t.get("id") == active_id for t in templates):
            raise HTTPException(
                status_code=404,
                detail=f"template_not_found: {active_id}",
            )
        settings["active_prompt_template_id"] = active_id

    save_settings(settings)
    logger.info("settings_updated")
    return settings


@router.post("/settings/reset")
def reset_settings_endpoint(req: SettingsResetRequest | None = None) -> dict[str, Any]:
    """Reset settings to safe defaults. Preserve custom templates unless flag set."""
    settings = load_settings()

    if req and req.delete_custom_templates:
        # Full reset: discard custom templates
        settings = reset_settings()
    else:
        # Preserve custom templates
        custom_templates = [
            t for t in settings.get("prompt_templates", [])
            if not t.get("built_in", False)
        ]
        defaults = reset_settings()
        defaults["prompt_templates"] = custom_templates
        defaults["version"] = 1
        save_settings(defaults)
        settings = defaults

    logger.info("settings_reset_completed")
    return settings


# ---------------------------------------------------------------------------
# Prompt template endpoints
# ---------------------------------------------------------------------------


@router.get("/prompt-templates")
def list_prompt_templates() -> dict[str, Any]:
    """Return all prompt templates (built-in + custom)."""
    settings = load_settings()
    templates = list_all_templates(settings)
    active_id = settings.get("active_prompt_template_id", "general")
    return {
        "active_prompt_template_id": active_id,
        "templates": templates,
        "total": len(templates),
    }


@router.post("/prompt-templates")
def create_prompt_template(req: TemplateCreateRequest) -> dict[str, Any]:
    """Create a new custom prompt template."""
    settings = load_settings()

    try:
        tmpl = create_template(
            settings=settings,
            name=req.name,
            description=req.description,
            system_prompt=req.system_prompt,
            instruction_prompt=req.instruction_prompt,
        )
    except Exception:
        save_settings(settings)
        raise

    save_settings(settings)
    return tmpl


@router.get("/prompt-templates/{template_id}")
def get_prompt_template(template_id: str) -> dict[str, Any]:
    """Get a single prompt template by ID."""
    settings = load_settings()
    tmpl = get_template(settings, template_id)
    if tmpl is None:
        raise HTTPException(status_code=404, detail=f"template_not_found: {template_id}")
    return tmpl


@router.put("/prompt-templates/{template_id}")
def update_prompt_template(template_id: str, req: TemplateUpdateRequest) -> dict[str, Any]:
    """Update an existing custom prompt template. Built-ins cannot be edited."""
    settings = load_settings()

    try:
        tmpl = update_template(
            settings=settings,
            template_id=template_id,
            name=req.name,
            description=req.description,
            system_prompt=req.system_prompt,
            instruction_prompt=req.instruction_prompt,
        )
    except ValueError as exc:
        save_settings(settings)
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        save_settings(settings)
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    save_settings(settings)
    return tmpl


@router.delete("/prompt-templates/{template_id}")
def delete_prompt_template(template_id: str) -> dict[str, Any]:
    """Delete a custom prompt template. Built-ins cannot be deleted."""
    settings = load_settings()

    try:
        delete_template(settings, template_id)
    except ValueError as exc:
        save_settings(settings)
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        save_settings(settings)
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    save_settings(settings)
    return {"deleted": template_id}


@router.post("/prompt-templates/{template_id}/activate")
def activate_prompt_template(template_id: str) -> dict[str, Any]:
    """Set the active prompt template by ID."""
    settings = load_settings()

    try:
        tmpl = activate_template(settings, template_id)
    except KeyError as exc:
        save_settings(settings)
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    save_settings(settings)
    return {
        "active_prompt_template_id": template_id,
        "template": tmpl,
    }


@router.post("/prompt-templates/{template_id}/duplicate")
def duplicate_prompt_template(template_id: str) -> dict[str, Any]:
    """Duplicate a template into a new custom template."""
    settings = load_settings()

    try:
        tmpl = duplicate_template(settings, template_id)
    except KeyError as exc:
        save_settings(settings)
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    save_settings(settings)
    return tmpl
