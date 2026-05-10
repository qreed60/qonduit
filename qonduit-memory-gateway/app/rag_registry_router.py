"""RAG project and collection management API routes.

This module provides CRUD endpoints for durable RAG projects and logical collections.
Routes are prefixed under /v1/rag.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .rag_registry import (
    RagRegistry,
    get_registry,
    validate_project_id,
    validate_collection_name,
)

logger = logging.getLogger("qonduit.memory_gateway.registry_router")

router = APIRouter(prefix="/v1/rag", tags=["rag-registry"])


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    project_id: str | None = None
    display_name: str | None = None
    description: str | None = ""
    default_collection: str | None = "default"
    metadata: dict[str, Any] | None = None
    ensure_qdrant: bool = True


class UpdateProjectRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    default_collection: str | None = None
    metadata: dict[str, Any] | None = None


class CreateCollectionRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    display_name: str | None = None
    description: str | None = ""
    metadata: dict[str, Any] | None = None


class UpdateCollectionRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    metadata: dict[str, Any] | None = None


def _error_response(error: str, detail: str) -> dict[str, Any]:
    return {"ok": False, "error": error, "detail": detail}


# ---------------------------------------------------------------------------
# Health / Registry status
# ---------------------------------------------------------------------------


@router.get("/registry/health")
async def registry_health() -> dict[str, Any]:
    """Check if the registry is healthy and persisted."""
    try:
        reg = get_registry()
        reg.reload()
        return {
            "ok": True,
            "project_count": len(reg._data.get("projects", {})),
            "registry_file": str(reg._path),
            "error": None,
        }
    except Exception as exc:
        logger.error("registry_health_error=%s", exc)
        return {
            "ok": False,
            "project_count": 0,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Project endpoints
# ---------------------------------------------------------------------------


@router.get("/projects")
async def list_projects() -> dict[str, Any]:
    """List all RAG projects (registry + discovered from Qdrant)."""
    try:
        reg = get_registry()
        projects = reg.list_projects()
        return {"ok": True, "projects": projects}
    except Exception as exc:
        logger.error("list_projects_error=%s", exc)
        return {"ok": False, "projects": [], "error": str(exc)}


@router.post("/projects")
async def create_project(req: CreateProjectRequest) -> dict[str, Any]:
    """Create a new RAG project or return existing if idempotent."""
    try:
        reg = get_registry()
        project, created, warnings = reg.ensure_project(
            project_id=req.project_id,
            display_name=req.display_name,
            description=req.description,
            default_collection=req.default_collection,
            metadata=req.metadata,
            ensure_qdrant=req.ensure_qdrant,
        )
        return {
            "ok": True,
            "created": created,
            "project": project,
            "warnings": warnings or [],
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_error_response("invalid_input", str(exc)))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=_error_response("forbidden", str(exc)))
    except Exception as exc:
        logger.error("create_project_error=%s", exc)
        raise HTTPException(status_code=500, detail=_error_response("internal_error", str(exc)))


@router.get("/projects/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    """Get a single RAG project with Qdrant stats."""
    try:
        reg = get_registry()
        result = reg.get_project(project_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=_error_response("not_found", str(exc)))
    except Exception as exc:
        logger.error("get_project_error project=%s error=%s", project_id, exc)
        raise HTTPException(status_code=500, detail=_error_response("internal_error", str(exc)))


@router.patch("/projects/{project_id}")
async def update_project(project_id: str, req: UpdateProjectRequest) -> dict[str, Any]:
    """Update project metadata fields (display_name, description, default_collection, metadata)."""
    try:
        reg = get_registry()
        result = reg.update_project(
            project_id=project_id,
            display_name=req.display_name,
            description=req.description,
            default_collection=req.default_collection,
            metadata=req.metadata,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400 if "must" in str(exc).lower() else 404, detail=_error_response("invalid_input", str(exc)))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=_error_response("forbidden", str(exc)))
    except Exception as exc:
        logger.error("update_project_error project=%s error=%s", project_id, exc)
        raise HTTPException(status_code=500, detail=_error_response("internal_error", str(exc)))


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str,
    delete_qdrant: bool = Query(default=False),
    force: bool = Query(default=False),
) -> dict[str, Any]:
    """Delete a project from the registry.

    Safety:
    - Cannot delete 'default' unless force=true
    - Cannot delete if Qdrant has points unless force=true
    - delete_qdrant=false only removes registry entry
    """
    try:
        reg = get_registry()
        result = reg.delete_project(
            project_id=project_id,
            delete_qdrant=delete_qdrant,
            force=force,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=_error_response("not_found", str(exc)))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=_error_response("forbidden", str(exc)))
    except Exception as exc:
        logger.error("delete_project_error project=%s error=%s", project_id, exc)
        raise HTTPException(status_code=500, detail=_error_response("internal_error", str(exc)))


# ---------------------------------------------------------------------------
# Collection endpoints
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/collections")
async def list_collections(project_id: str) -> dict[str, Any]:
    """List logical collections for a project (registry + discovered from Qdrant)."""
    try:
        reg = get_registry()
        collections = reg.list_collections(project_id)
        return {
            "ok": True,
            "project_id": project_id,
            "collections": collections,
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=_error_response("not_found", str(exc)))
    except Exception as exc:
        logger.error("list_collections_error project=%s error=%s", project_id, exc)
        raise HTTPException(status_code=500, detail=_error_response("internal_error", str(exc)))


@router.post("/projects/{project_id}/collections")
async def create_collection(project_id: str, req: CreateCollectionRequest) -> dict[str, Any]:
    """Create a logical collection within a project.

    Does not require documents/chunks to exist.
    Uses the existing project physical Qdrant collection.
    """
    try:
        reg = get_registry()
        collection, created = reg.create_collection(
            project_id=project_id,
            collection_name=req.name,
            display_name=req.display_name,
            description=req.description,
            metadata=req.metadata,
        )
        return {
            "ok": True,
            "created": created,
            "collection": collection,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400 if "must" in str(exc).lower() else 404, detail=_error_response("invalid_input", str(exc)))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=_error_response("forbidden", str(exc)))
    except Exception as exc:
        logger.error("create_collection_error project=%s error=%s", project_id, exc)
        raise HTTPException(status_code=500, detail=_error_response("internal_error", str(exc)))


@router.get("/projects/{project_id}/collections/{collection_name}")
async def get_collection(project_id: str, collection_name: str) -> dict[str, Any]:
    """Get a single logical collection for a project."""
    try:
        reg = get_registry()
        result = reg.get_collection(project_id, collection_name)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400 if "must" in str(exc).lower() else 404, detail=_error_response("invalid_input", str(exc)))
    except Exception as exc:
        logger.error("get_collection_error project=%s collection=%s error=%s", project_id, collection_name, exc)
        raise HTTPException(status_code=500, detail=_error_response("internal_error", str(exc)))


@router.patch("/projects/{project_id}/collections/{collection_name}")
async def update_collection(
    project_id: str,
    collection_name: str,
    req: UpdateCollectionRequest,
) -> dict[str, Any]:
    """Update a logical collection's metadata."""
    try:
        reg = get_registry()
        result = reg.update_collection(
            project_id=project_id,
            collection_name=collection_name,
            display_name=req.display_name,
            description=req.description,
            metadata=req.metadata,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400 if "must" in str(exc).lower() else 404, detail=_error_response("invalid_input", str(exc)))
    except Exception as exc:
        logger.error("update_collection_error project=%s collection=%s error=%s", project_id, collection_name, exc)
        raise HTTPException(status_code=500, detail=_error_response("internal_error", str(exc)))


@router.delete("/projects/{project_id}/collections/{collection_name}")
async def delete_collection(
    project_id: str,
    collection_name: str,
    delete_chunks: bool = Query(default=False),
    force: bool = Query(default=False),
) -> dict[str, Any]:
    """Delete a logical collection from the registry.

    Safety:
    - Cannot delete 'default' collection unless force=true
    - Cannot delete if chunks exist unless force=true
    - delete_chunks=false only removes registry entry
    """
    try:
        reg = get_registry()
        result = reg.delete_collection(
            project_id=project_id,
            collection_name=collection_name,
            delete_chunks=delete_chunks,
            force=force,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400 if "must" in str(exc).lower() else 404, detail=_error_response("invalid_input", str(exc)))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=_error_response("forbidden", str(exc)))
    except Exception as exc:
        logger.error("delete_collection_error project=%s collection=%s error=%s", project_id, collection_name, exc)
        raise HTTPException(status_code=500, detail=_error_response("internal_error", str(exc)))
