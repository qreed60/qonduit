"""Tool registry API router.

Endpoints:
- GET  /v1/tools                      – list all known tools
- GET  /v1/tools/status               – tool status + dependency health
- GET  /v1/gateway/settings/tools     – current tool settings
- PATCH /v1/gateway/settings/tools    – update tool settings
- POST /v1/tools/execute              – execute a tool (body-based)
- POST /v1/tools/{tool_id}/execute    – execute a tool (path-based, Phase 1)
- GET  /v1/models/{model_id}/tools    – model-specific effective tools
- GET  /v1/tools/audit                – audit log (Phase 5)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .tools import (
    ALL_KNOWN_TOOLS,
    DESTRUCTIVE_TOOLS,
    SAFE_TOOLS,
    get_tool,
    list_tools,
    list_tools_with_metadata,
    is_safe_tool,
    is_destructive_tool,
    validate_tool_arguments,
)
from .tool_settings import (
    _data_dir,
    load_settings,
    save_settings,
    _validate_and_update_settings,
    get_effective_tools,
)
from .rag_read import router as rag_read_router  # noqa: F401 – used to access RAG helpers

logger = logging.getLogger("qonduit.memory_gateway.tools_router")

router = APIRouter(tags=["model-tools"])

# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------


class ToolExecuteRequest(BaseModel):
    name: str = Field(..., min_length=1)
    arguments: dict[str, Any] | None = None
    model: str | None = None
    conversation_id: str | None = None
    user: str | None = None

    model_config = {"extra": "allow"}


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str
    detail: str | None = None


# ---------------------------------------------------------------------------
# Audit log (Phase 5)
# ---------------------------------------------------------------------------

AUDIT_LOG_PATH = _data_dir() / "tool_execution_audit.jsonl"


def _audit_log(event: dict[str, Any]) -> None:
    """Append an audit event to the JSONL audit log."""
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
    except Exception:
        logger.exception("audit_log_write_failed")


# ---------------------------------------------------------------------------
# Tool execution handlers
# ---------------------------------------------------------------------------


async def _execute_rag_project_list(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Execute rag_project_list tool."""
    try:
        # Reuse the existing /v1/rag/projects endpoint logic
        from .rag_read import RAG_KNOWN_PROJECTS, _try_get_collection_info, project_collection_name
        import httpx
        from .rag import qdrant

        projects = []
        for pid in RAG_KNOWN_PROJECTS:
            pid = pid.strip()
            if not pid:
                continue
            try:
                coll = project_collection_name(pid)
                info = _try_get_collection_info(coll)
                projects.append({
                    "project_id": pid,
                    "qdrant_collection": coll,
                    "exists": info.get("exists", False),
                    "points_count": info.get("points_count", 0),
                    "vectors_count": info.get("vectors_count", 0),
                    "status": None,
                    "error": None,
                })
            except Exception as exc:
                logger.warning("project_list_project_failed project=%s error=%s", pid, exc)
                projects.append({
                    "project_id": pid,
                    "qdrant_collection": "",
                    "exists": False,
                    "points_count": 0,
                    "vectors_count": 0,
                    "status": None,
                    "error": str(exc),
                })

        return {"ok": True, "projects": projects}
    except Exception as exc:
        logger.exception("tool_rag_project_list_failed")
        return {"ok": False, "projects": [], "error": str(exc)}


async def _execute_rag_collection_list(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Execute rag_collection_list tool."""
    try:
        project_id = (arguments or {}).get("project_id", "default")
        from .rag_read import _validate_project_id, project_collection_name
        from .rag import qdrant
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        safe = _validate_project_id(project_id)
        coll = project_collection_name(safe)

        try:
            points, _ = qdrant.scroll(
                collection_name=coll,
                limit=1000,
                with_payload=True,
                with_vectors=False,
            )
            names: set[str] = set()
            for point in points:
                payload = point.payload or {}
                for key in ("collection", "namespace"):
                    val = payload.get(key)
                    if isinstance(val, str) and val.strip():
                        names.add(val.strip())
            collections = sorted(names)
        except Exception as exc:
            logger.warning("collection_list_scroll_failed project=%s error=%s", safe, exc)
            collections = []

        return {"ok": True, "project_id": safe, "collections": collections}
    except Exception as exc:
        logger.exception("tool_rag_collection_list_failed")
        return {"ok": False, "collections": [], "error": str(exc)}


async def _execute_rag_search(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Execute rag_search tool."""
    try:
        if not arguments:
            return {"ok": False, "results": [], "error": "missing_arguments"}

        query = arguments.get("query")
        if not query or not isinstance(query, str):
            return {"ok": False, "results": [], "error": "invalid_arguments: 'query' is required"}

        project_id = arguments.get("project_id", "default")
        collection = arguments.get("collection")
        limit = arguments.get("limit", 5)

        if not isinstance(limit, int) or limit < 1:
            limit = 5

        from .rag import search_documents

        results = await search_documents(
            query=query,
            limit=limit,
            collection=collection,
            project_id=project_id,
        )

        formatted = []
        for r in results:
            payload = r.get("payload", {})
            text = r.get("text", "")
            formatted.append({
                "id": r.get("id", ""),
                "score": r.get("score", 0),
                "text": text,
                "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
                "project_id": payload.get("project_id", project_id),
                "collection": payload.get("collection", collection),
                "document_name": payload.get("document_name", ""),
                "file_path": payload.get("file_path", payload.get("source_file", "")),
                "chunk_index": payload.get("chunk_index", 0),
            })

        return {"ok": True, "results": formatted, "count": len(formatted)}
    except Exception as exc:
        logger.exception("tool_rag_search_failed")
        return {"ok": False, "results": [], "error": str(exc)}


async def _execute_gateway_health(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Execute gateway_health tool."""
    try:
        from .rag_read import rag_health
        return await rag_health()
    except Exception as exc:
        logger.exception("tool_gateway_health_failed")
        return {"ok": False, "error": str(exc)}


async def _execute_model_list(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Execute model_list tool."""
    try:
        import httpx
        from .main import LLAMA_BASE

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{LLAMA_BASE}/v1/models")

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict):
                return {"ok": True, "models": data.get("data", []), "raw": data}
        return {"ok": False, "models": [], "error": "upstream_returned_invalid_json"}
    except Exception as exc:
        logger.warning("tool_model_list_failed error=%s", exc)
        return {"ok": False, "models": [], "error": str(exc)}


async def _execute_rag_document_list(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Execute rag_document_list tool."""
    try:
        from .rag_read import (
            _validate_project_id,
            project_collection_name,
            _build_filter,
            qdrant,
        )

        project_id = (arguments or {}).get("project_id", "default")
        collection = arguments.get("collection")
        limit = min(max(1, (arguments or {}).get("limit", 100)), 500)

        safe = _validate_project_id(project_id)
        coll = project_collection_name(safe)
        query_filter = _build_filter(safe, collection)

        points, _ = qdrant.scroll(
            collection_name=coll,
            scroll_filter=query_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )

        docs: dict[str, dict[str, Any]] = {}
        for point in points:
            payload = point.payload or {}
            file_path = (
                payload.get("file_path")
                or payload.get("document_name")
                or payload.get("source_file")
                or ""
            )
            if not isinstance(file_path, str) or not file_path.strip():
                continue
            file_path = file_path.strip()
            source = str(payload.get("source", "unknown")).strip() or "unknown"
            doc_key = f"{source}::{file_path}"
            if doc_key not in docs:
                file_name = file_path.rsplit("/", 1)[-1]
                file_type = ""
                if "." in file_name:
                    file_type = file_name.rsplit(".", 1)[-1].lower()
                docs[doc_key] = {
                    "document_id": doc_key,
                    "document_name": file_name,
                    "source": source,
                    "file_path": file_path,
                    "file_type": file_type,
                    "chunk_count": 0,
                    "first_chunk_id": str(point.id),
                    "metadata": {
                        k: v
                        for k, v in payload.items()
                        if k
                        not in (
                            "text",
                            "chunk_index",
                            "namespace",
                            "collection",
                            "project_id",
                            "user_id",
                        )
                    },
                }
            doc = docs[doc_key]
            doc["chunk_count"] = doc.get("chunk_count", 0) + 1

        document_list = sorted(docs.values(), key=lambda d: d["document_id"])
        return {
            "ok": True,
            "project_id": safe,
            "collection": collection,
            "documents": document_list,
            "count": len(document_list),
        }
    except Exception as exc:
        logger.exception("tool_rag_document_list_failed")
        return {"ok": False, "documents": [], "error": str(exc)}


async def _execute_rag_document_chunks(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Execute rag_document_chunks tool."""
    try:
        from .rag_read import (
            _validate_project_id,
            project_collection_name,
            qdrant,
        )
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        if not arguments:
            return {"ok": False, "chunks": [], "error": "missing_arguments: 'document_id' required"}

        project_id = arguments.get("project_id", "default")
        document_id = arguments.get("document_id")

        if not document_id or not isinstance(document_id, str):
            return {"ok": False, "chunks": [], "error": "invalid_arguments: 'document_id' is required"}

        safe = _validate_project_id(project_id)
        coll = project_collection_name(safe)

        parts = document_id.split("::", 1)
        doc_source = parts[0] if len(parts) > 1 else ""
        doc_file_path = parts[1] if len(parts) > 1 else parts[0]

        conditions: list[FieldCondition] = [
            FieldCondition(
                key="project_id",
                match=MatchValue(value=safe),
            ),
        ]
        if doc_file_path:
            conditions.append(
                FieldCondition(
                    key="file_path",
                    match=MatchValue(value=doc_file_path),
                ),
            )
        elif doc_source:
            conditions.append(
                FieldCondition(
                    key="source",
                    match=MatchValue(value=doc_source),
                ),
            )

        query_filter = Filter(must=conditions) if conditions else None
        points, _ = qdrant.scroll(
            collection_name=coll,
            scroll_filter=query_filter,
            limit=500,
            with_payload=True,
            with_vectors=False,
        )

        chunks = []
        for point in sorted(
            points, key=lambda p: (p.payload or {}).get("chunk_index", 0)
        ):
            payload = point.payload or {}
            text = payload.get("text", "")
            preview = text[:200] + ("..." if len(text) > 200 else "")
            chunks.append(
                {
                    "id": str(point.id),
                    "chunk_index": payload.get("chunk_index", 0),
                    "text": text,
                    "text_preview": preview,
                    "payload": {k: v for k, v in payload.items() if k != "text"},
                }
            )

        return {
            "ok": True,
            "project_id": safe,
            "document_id": document_id,
            "chunks": chunks,
            "count": len(chunks),
        }
    except Exception as exc:
        logger.exception("tool_rag_document_chunks_failed")
        return {"ok": False, "chunks": [], "error": str(exc)}


# Map tool names to execution functions
_TOOL_EXECUTORS: dict[str, Any] = {
    "rag_project_list": _execute_rag_project_list,
    "rag_collection_list": _execute_rag_collection_list,
    "rag_search": _execute_rag_search,
    "gateway_health": _execute_gateway_health,
    "model_list": _execute_model_list,
    "rag_document_list": _execute_rag_document_list,
    "rag_document_chunks": _execute_rag_document_chunks,
}


# ---------------------------------------------------------------------------
# Helper: check dependency health
# ---------------------------------------------------------------------------


async def _check_dependency_health() -> dict[str, Any]:
    """Check health of Gateway dependencies."""
    deps: dict[str, Any] = {}

    # Qdrant
    try:
        from .rag_read import _try_get_collection_info
        import httpx
        from .rag import QDRANT_URL

        qdrant_ok = False
        qdrant_err: str | None = None
        try:
            url = QDRANT_URL.rstrip("/")
            async with httpx.AsyncClient(timeout=5.0) as client:
                for ep in ["/readyz", "/collections", "/"]:
                    try:
                        resp = await client.get(f"{url}{ep}")
                        if resp.status_code == 200:
                            qdrant_ok = True
                            break
                    except Exception:
                        continue
            if not qdrant_ok:
                raise Exception("All Qdrant health endpoints failed")
        except Exception as exc:
            qdrant_err = str(exc)
        deps["qdrant"] = {"ok": qdrant_ok, "error": qdrant_err}
    except Exception as exc:
        logger.warning("dep_check_qdrant_failed error=%s", exc)
        deps["qdrant"] = {"ok": False, "error": str(exc)}

    # Embedding
    try:
        from .rag import EMBEDDING_BASE, EMBEDDING_MODEL

        embedding_ok = False
        embedding_err: str | None = None
        embedding_dim: int | None = None
        try:
            base = EMBEDDING_BASE.rstrip("/")
            embed_model = EMBEDDING_MODEL or "default"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{base}/v1/embeddings",
                    json={"input": "health-check", "model": embed_model},
                )
                resp.raise_for_status()
                data = resp.json()
                vectors = data.get("data", [])
                if vectors:
                    vec = vectors[0].get("embedding", [])
                    if isinstance(vec, list):
                        embedding_dim = len(vec)
            embedding_ok = True
        except Exception as exc:
            embedding_err = str(exc)
        deps["embedding"] = {
            "ok": embedding_ok,
            "base": EMBEDDING_BASE,
            "model": EMBEDDING_MODEL if EMBEDDING_MODEL else None,
            "dimension": embedding_dim,
            "error": embedding_err,
        }
    except Exception as exc:
        logger.warning("dep_check_embedding_failed error=%s", exc)
        deps["embedding"] = {"ok": False, "error": str(exc)}

    return deps


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/v1/tools")
async def list_tools_endpoint() -> dict[str, Any]:
    """Return all known tool definitions (frontend-friendly, with metadata)."""
    return {"ok": True, "tools": list_tools_with_metadata()}


@router.get("/v1/tools/status")
async def tools_status_endpoint() -> dict[str, Any]:
    """Return status/dependency health for all tools."""
    settings = load_settings()
    effective = get_effective_tools(settings)

    tools_status: dict[str, Any] = {}
    for tool_def in list_tools():
        name = tool_def["name"]
        enabled = effective.get(name, tool_def.get("enabled", False))
        tools_status[name] = {
            "ok": tool_def.get("backendAvailable", False),
            "enabled": enabled,
            "backendAvailable": tool_def.get("backendAvailable", False),
            "lastError": tool_def.get("lastError"),
        }

    dependencies = await _check_dependency_health()

    return {"ok": True, "tools": tools_status, "dependencies": dependencies}


@router.get("/v1/gateway/settings/tools")
async def get_tool_settings() -> dict[str, Any]:
    """Return current persisted tool settings."""
    settings = load_settings()
    return {
        "ok": True,
        "settings": {
            "global": settings.get("global", {}),
            "perModel": settings.get("perModel", {}),
            "confirmationMode": settings.get("confirmationMode", "risky-only"),
        },
    }


@router.patch("/v1/gateway/settings/tools")
async def update_tool_settings(body: dict[str, Any]) -> dict[str, Any]:
    """Update tool settings with validation."""
    settings = load_settings()
    settings, errors = _validate_and_update_settings(settings, body)

    if errors:
        save_settings(settings)
        return {
            "ok": False,
            "error": "validation_failed",
            "detail": "; ".join(errors),
            "settings": {
                "global": settings.get("global", {}),
                "perModel": settings.get("perModel", {}),
                "confirmationMode": settings.get("confirmationMode", "risky-only"),
            },
        }

    try:
        save_settings(settings)
    except Exception as exc:
        logger.exception("tool_settings_save_failed")
        return {
            "ok": False,
            "error": "settings_write_failed",
            "detail": str(exc),
        }

    return {
        "ok": True,
        "settings": {
            "global": settings.get("global", {}),
            "perModel": settings.get("perModel", {}),
            "confirmationMode": settings.get("confirmationMode", "risky-only"),
        },
    }


@router.post("/v1/tools/execute")
async def execute_tool_endpoint(req: ToolExecuteRequest) -> dict[str, Any]:
    """Execute a tool with safety checks."""
    start_ms = time.perf_counter_ns()
    tool_call_id = f"call_{uuid.uuid4().hex[:8]}"

    # Validate tool exists
    tool_def = get_tool(req.name)
    if not tool_def:
        audit_event = {
            "timestamp": _now_iso(),
            "tool": req.name,
            "readOnly": None,
            "destructive": None,
            "model": req.model,
            "conversation_id": req.conversation_id,
            "user": req.user,
            "ok": False,
            "duration_ms": 0,
            "error": "tool_not_found",
        }
        _audit_log(audit_event)
        return {
            "ok": False,
            "tool_call_id": tool_call_id,
            "name": req.name,
            "result": None,
            "duration_ms": 0,
            "error": "tool_not_found",
            "detail": f"Unknown tool: {req.name}",
        }

    # Load settings
    settings = load_settings()
    effective = get_effective_tools(settings, req.model)

    # Check if tool is enabled
    tool_enabled = effective.get(req.name, tool_def.get("enabled", False))
    if not tool_enabled:
        audit_event = {
            "timestamp": _now_iso(),
            "tool": req.name,
            "readOnly": tool_def.get("readOnly"),
            "destructive": tool_def.get("destructive"),
            "model": req.model,
            "conversation_id": req.conversation_id,
            "user": req.user,
            "ok": False,
            "duration_ms": 0,
            "error": "tool_disabled",
        }
        _audit_log(audit_event)
        return {
            "ok": False,
            "tool_call_id": tool_call_id,
            "name": req.name,
            "result": None,
            "duration_ms": 0,
            "error": "tool_disabled",
            "detail": f"Tool '{req.name}' is not enabled.",
        }

    # Check if backend is available
    if not tool_def.get("backendAvailable", False):
        audit_event = {
            "timestamp": _now_iso(),
            "tool": req.name,
            "readOnly": tool_def.get("readOnly"),
            "destructive": tool_def.get("destructive"),
            "model": req.model,
            "conversation_id": req.conversation_id,
            "user": req.user,
            "ok": False,
            "duration_ms": 0,
            "error": "tool_unavailable",
        }
        _audit_log(audit_event)
        return {
            "ok": False,
            "tool_call_id": tool_call_id,
            "name": req.name,
            "result": None,
            "duration_ms": 0,
            "error": "tool_unavailable",
            "detail": tool_def.get("lastError", "Backend not available."),
        }

    # Refuse destructive tools
    if tool_def.get("destructive", False):
        audit_event = {
            "timestamp": _now_iso(),
            "tool": req.name,
            "readOnly": False,
            "destructive": True,
            "model": req.model,
            "conversation_id": req.conversation_id,
            "user": req.user,
            "ok": False,
            "duration_ms": 0,
            "error": "tool_not_read_only",
        }
        _audit_log(audit_event)
        return {
            "ok": False,
            "tool_call_id": tool_call_id,
            "name": req.name,
            "result": None,
            "duration_ms": 0,
            "error": "tool_not_read_only",
            "detail": "Destructive tools are not executable in read-only mode.",
        }

    # Validate arguments
    args_valid, arg_error = validate_tool_arguments(req.name, req.arguments)
    if not args_valid:
        audit_event = {
            "timestamp": _now_iso(),
            "tool": req.name,
            "readOnly": tool_def.get("readOnly"),
            "destructive": False,
            "model": req.model,
            "conversation_id": req.conversation_id,
            "user": req.user,
            "ok": False,
            "duration_ms": 0,
            "error": "invalid_arguments",
        }
        _audit_log(audit_event)
        return {
            "ok": False,
            "tool_call_id": tool_call_id,
            "name": req.name,
            "result": None,
            "duration_ms": 0,
            "error": "invalid_arguments",
            "detail": arg_error,
        }

    # Execute
    executor = _TOOL_EXECUTORS.get(req.name)
    if not executor:
        audit_event = {
            "timestamp": _now_iso(),
            "tool": req.name,
            "readOnly": tool_def.get("readOnly"),
            "destructive": False,
            "model": req.model,
            "conversation_id": req.conversation_id,
            "user": req.user,
            "ok": False,
            "duration_ms": 0,
            "error": "tool_not_found",
        }
        _audit_log(audit_event)
        return {
            "ok": False,
            "tool_call_id": tool_call_id,
            "name": req.name,
            "result": None,
            "duration_ms": 0,
            "error": "tool_not_found",
            "detail": f"No executor for tool: {req.name}",
        }

    try:
        result = await executor(req.arguments)
        duration_ns = time.perf_counter_ns() - start_ms
        duration_ms = round(duration_ns / 1_000_000, 2)

        ok = result.get("ok", False)
        error = None if ok else result.get("error")

        audit_event = {
            "timestamp": _now_iso(),
            "tool": req.name,
            "readOnly": tool_def.get("readOnly"),
            "destructive": False,
            "model": req.model,
            "conversation_id": req.conversation_id,
            "user": req.user,
            "ok": ok,
            "duration_ms": duration_ms,
            "error": error,
        }
        _audit_log(audit_event)

        return {
            "ok": ok,
            "tool_call_id": tool_call_id,
            "name": req.name,
            "result": result if ok else None,
            "duration_ms": duration_ms,
            "error": error,
        }
    except Exception as exc:
        duration_ns = time.perf_counter_ns() - start_ms
        duration_ms = round(duration_ns / 1_000_000, 2)
        logger.exception("tool_execute_exception tool=%s", req.name)

        audit_event = {
            "timestamp": _now_iso(),
            "tool": req.name,
            "readOnly": tool_def.get("readOnly"),
            "destructive": False,
            "model": req.model,
            "conversation_id": req.conversation_id,
            "user": req.user,
            "ok": False,
            "duration_ms": duration_ms,
            "error": "execution_error",
        }
        _audit_log(audit_event)

        return {
            "ok": False,
            "tool_call_id": tool_call_id,
            "name": req.name,
            "result": None,
            "duration_ms": duration_ms,
            "error": "execution_error",
            "detail": str(exc),
        }


# ---------------------------------------------------------------------------
# Phase 1: Path-parameter execute endpoint (simplified API)
# ---------------------------------------------------------------------------

# Phase 1: only these safe tools are executable via the path-based endpoint
_PHASE1_EXECUTABLE_TOOLS: set[str] = {"gateway_health", "model_list"}


@router.post("/v1/tools/{tool_id}/execute")
async def path_execute_tool_endpoint(
    tool_id: str,
    request: Request,
) -> dict[str, Any]:
    """Execute a tool via path parameter with simplified request/response.

    Phase 1 supports only safe, read-only tools: gateway_health and model_list.
    """
    # Capture request metadata for audit logging
    client_host: str | None = request.client.host if request.client else None
    path: str = request.url.path

    # Parse request body safely — no Pydantic 422 on malformed JSON
    raw_body = await request.body()

    if not raw_body or not raw_body.strip():
        # No body at all → treat as empty input
        input_data: dict[str, Any] = {}
    else:
        try:
            parsed = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError):
            # Malformed JSON → clean error response
            response = {
                "ok": False,
                "tool_id": tool_id,
                "danger_level": None,
                "requires_confirmation": None,
                "input": {},
                "result": None,
                "error": {
                    "code": "invalid_tool_input",
                    "message": "Request body is not valid JSON",
                },
            }
            _log_audit(response, {}, client_host, path, tool_id)
            return response

        if not isinstance(parsed, dict):
            # Top-level body is not an object (e.g. "[1,2]" or "true")
            response = {
                "ok": False,
                "tool_id": tool_id,
                "danger_level": None,
                "requires_confirmation": None,
                "input": {},
                "result": None,
                "error": {
                    "code": "invalid_tool_input",
                    "message": "Request body must be a JSON object",
                },
            }
            _log_audit(response, {}, client_host, path, tool_id)
            return response

        raw_input = parsed.get("input")

        if raw_input is None:
            # {"input": null} or missing "input" key → treat as empty
            input_data = {}
        elif isinstance(raw_input, dict):
            # {"input": {...}} → use as-is
            input_data = raw_input
        else:
            # {"input": "bad"}, {"input": []}, {"input": 123}, {"input": true}
            response = {
                "ok": False,
                "tool_id": tool_id,
                "danger_level": None,
                "requires_confirmation": None,
                "input": {},
                "result": None,
                "error": {
                    "code": "invalid_tool_input",
                    "message": "input must be an object or null",
                },
            }
            _log_audit(response, {}, client_host, path, tool_id)
            return response

    start_ms = time.perf_counter_ns()

    # 1. Look up tool definition
    tool_def = get_tool(tool_id)
    if not tool_def:
        response = {
            "ok": False,
            "tool_id": tool_id,
            "danger_level": None,
            "requires_confirmation": None,
            "input": input_data,
            "result": None,
            "error": {"code": "tool_not_found", "message": "Tool not found or not executable"},
        }
        duration_ms = _calc_duration_ms(start_ms)
        response["duration_ms"] = duration_ms
        _log_audit(response, input_data, client_host, path, tool_id)
        return response

    # 2. Determine danger level and confirmation requirement
    is_safe = tool_id in SAFE_TOOLS
    is_destructive = tool_def.get("destructive", False)

    if is_destructive:
        response = {
            "ok": False,
            "tool_id": tool_id,
            "danger_level": "destructive",
            "requires_confirmation": True,
            "input": input_data,
            "result": None,
            "error": {"code": "tool_destructive", "message": "Destructive tools are not executable in read-only mode"},
        }
        duration_ms = _calc_duration_ms(start_ms)
        response["duration_ms"] = duration_ms
        _log_audit(response, input_data, client_host, path, tool_id)
        return response

    danger_level: str | None = "read_only" if is_safe else "write"
    requires_confirmation: bool = False

    # 3. Phase 1: only allow safe tools with an executor
    if not is_safe:
        response = {
            "ok": False,
            "tool_id": tool_id,
            "danger_level": danger_level,
            "requires_confirmation": False,
            "input": input_data,
            "result": None,
            "error": {"code": "tool_not_found", "message": "Tool not found or not executable"},
        }
        duration_ms = _calc_duration_ms(start_ms)
        response["duration_ms"] = duration_ms
        _log_audit(response, input_data, client_host, path, tool_id)
        return response

    executor = _TOOL_EXECUTORS.get(tool_id)
    if not executor:
        response = {
            "ok": False,
            "tool_id": tool_id,
            "danger_level": danger_level,
            "requires_confirmation": requires_confirmation,
            "input": input_data,
            "result": None,
            "error": {"code": "tool_not_found", "message": f"No executor for tool: {tool_id}"},
        }
        duration_ms = _calc_duration_ms(start_ms)
        response["duration_ms"] = duration_ms
        _log_audit(response, input_data, client_host, path, tool_id)
        return response

    # 4. Execute
    try:
        result = await executor(input_data)
        duration_ns = time.perf_counter_ns() - start_ms
        duration_ms = round(duration_ns / 1_000_000, 2)

        ok = result.get("ok", False)
        error = None if ok else {"code": "execution_failed", "message": result.get("error", str(result))}

        response = {
            "ok": ok,
            "tool_id": tool_id,
            "danger_level": danger_level,
            "requires_confirmation": requires_confirmation,
            "input": input_data,
            "result": result if ok else None,
            "error": error,
            "duration_ms": duration_ms,
        }
        _log_audit(response, input_data, client_host, path, tool_id)
        return response
    except Exception as exc:
        duration_ns = time.perf_counter_ns() - start_ms
        duration_ms = round(duration_ns / 1_000_000, 2)
        logger.exception("path_execute_tool_exception tool=%s", tool_id)

        response = {
            "ok": False,
            "tool_id": tool_id,
            "danger_level": danger_level,
            "requires_confirmation": requires_confirmation,
            "input": input_data,
            "result": None,
            "error": {"code": "execution_error", "message": str(exc)},
            "duration_ms": duration_ms,
        }
        _log_audit(response, input_data, client_host, path, tool_id)
        return response


@router.get("/v1/models/{model_id}/tools")
async def model_tools_endpoint(
    model_id: str,
) -> dict[str, Any]:
    """Return model-specific effective tools."""
    settings = load_settings()
    effective = get_effective_tools(settings, model_id)

    tools = []
    for tool_def in list_tools():
        name = tool_def["name"]
        if effective.get(name, tool_def.get("enabled", False)):
            tools.append({
                "name": name,
                "displayName": tool_def.get("displayName", name),
                "enabled": True,
                "readOnly": tool_def.get("readOnly", False),
                "category": tool_def.get("category", "utility"),
            })

    return {
        "ok": True,
        "model": model_id,
        "modelToolCallingSupport": "unknown",
        "tools": tools,
    }


@router.get("/v1/tools/audit")
async def audit_log_endpoint(
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    """Return recent audit log entries."""
    try:
        if not AUDIT_LOG_PATH.exists():
            return {"ok": True, "audit": [], "count": 0}

        lines = AUDIT_LOG_PATH.read_text(encoding="utf-8").strip().split("\n")
        lines = [l for l in lines if l.strip()]

        # Return the last `limit` entries
        entries = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        return {"ok": True, "audit": entries, "count": len(entries)}
    except Exception as exc:
        logger.exception("audit_log_read_failed")
        return {
            "ok": False,
            "audit": [],
            "count": 0,
            "error": str(exc),
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _calc_duration_ms(start_ns: float) -> float:
    """Calculate duration in milliseconds from a start timestamp (perf_counter_ns)."""
    duration_ns = time.perf_counter_ns() - start_ns
    return round(duration_ns / 1_000_000, 2)


def _log_audit(response: dict[str, Any], input_data: dict[str, Any],
               client_host: str | None, path: str, tool_id: str) -> None:
    """Record a tool execution audit event to the JSONL audit log."""
    event_id = str(uuid.uuid4())
    event: dict[str, Any] = {
        "event_id": event_id,
        "timestamp": _now_iso(),
        "event_type": "tool_execution",
        "tool_id": tool_id,
        "path": path,
        "client_host": client_host,
        "input": input_data,
        "output": {
            "ok": response.get("ok", False),
            "tool_id": response.get("tool_id"),
            "danger_level": response.get("danger_level"),
            "requires_confirmation": response.get("requires_confirmation"),
            "result": response.get("result"),
            "error": response.get("error"),
        },
        "duration_ms": response.get("duration_ms"),
    }
    _audit_log(event)
