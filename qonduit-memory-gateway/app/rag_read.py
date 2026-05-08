"""Read-only RAG API routes for Phase 1 - Qonduit Memory Gateway."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from .rag import (
    EMBEDDING_BASE,
    EMBEDDING_MODEL,
    QDRANT_API_KEY,
    QDRANT_URL,
    VECTOR_SIZE,
    qdrant,
    embedding_backend,
    project_collection_name,
    search_documents,
    list_collections as _rag_list_collections,
)

logger = logging.getLogger("qonduit.memory_gateway.rag_read")

_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_RAG_KNOWN_RAW = os.getenv(
    "RAG_KNOWN_PROJECTS",
    "default,android-qonduit,chatgpt,checkbook,work",
)
RAG_KNOWN_PROJECTS = [
    p.strip() for p in _RAG_KNOWN_RAW.split(",") if p.strip()
]


class RagSearchBody(BaseModel):
    query: str = Field(..., min_length=1, max_length=4096)
    collection: str | None = None
    limit: int = Field(default=4, ge=1, le=20)
    user_id: str | None = None


def _validate_project_id(project_id: str) -> str:
    if not project_id or not _PROJECT_ID_RE.match(project_id):
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "invalid_project_id",
                "detail": (
                    "project_id must contain only lowercase letters, digits, "
                    "hyphens, and underscores, and must start with an alphanumeric"
                ),
            },
        )
    return project_id


def _build_filter(project_id: str, collection: str | None = None) -> Filter | None:
    conditions: list[FieldCondition] = [
        FieldCondition(
            key="project_id",
            match=MatchValue(value=project_id),
        ),
    ]
    if collection:
        conditions.append(
            FieldCondition(
                key="collection",
                match=MatchValue(value=collection),
            ),
        )
    return Filter(must=conditions)


def _try_get_collection_info(collection_name: str) -> dict[str, Any]:
    try:
        info = qdrant.get_collection(collection_name)
        return {
            "exists": True,
            "points_count": info.points_count or 0,
            "vectors_count": info.vectors_count or info.points_count or 0,
            "indexed_vectors_count": info.points_count or 0,
            "status": getattr(info, "status", None),
            "optimizer_status": (
                getattr(info.optimizer_status, "status", None)
                if hasattr(info, "optimizer_status") and info.optimizer_status
                else None
            ),
        }
    except Exception as exc:
        logger.warning("get_collection_info_failed collection=%s error=%s", collection_name, exc)
        return {
            "exists": False,
            "points_count": 0,
            "vectors_count": 0,
            "indexed_vectors_count": None,
            "status": None,
            "optimizer_status": None,
            "error": str(exc),
        }


router = APIRouter(prefix="/v1/rag", tags=["rag-read"])


@router.get("/health")
async def rag_health() -> dict[str, Any]:
    qdrant_ok = False
    qdrant_err: str | None = None
    embedding_ok = False
    embedding_err: str | None = None
    embedding_dimension: int | None = None
    try:
        url = QDRANT_URL.rstrip("/")
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url}/health")
            resp.raise_for_status()
        qdrant_ok = True
    except Exception as exc:
        qdrant_err = str(exc)
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
                    embedding_dimension = len(vec)
        embedding_ok = True
    except Exception as exc:
        embedding_err = str(exc)
    overall_ok = qdrant_ok and embedding_ok
    return {
        "ok": overall_ok,
        "qdrant": {"ok": qdrant_ok, "url": QDRANT_URL, "error": qdrant_err},
        "embedding": {
            "ok": embedding_ok,
            "base": EMBEDDING_BASE,
            "model": EMBEDDING_MODEL if EMBEDDING_MODEL else None,
            "dimension": embedding_dimension,
            "error": embedding_err,
        },
    }


@router.get("/projects")
async def rag_projects() -> dict[str, Any]:
    projects: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for pid in RAG_KNOWN_PROJECTS:
        try:
            safe = _validate_project_id(pid)
        except HTTPException:
            safe = pid.strip()
        seen_ids.add(safe)
        coll = project_collection_name(safe)
        info = _try_get_collection_info(coll)
        projects.append({
            "project_id": safe,
            "qdrant_collection": coll,
            "exists": info.get("exists", False),
            "points_count": info.get("points_count", 0),
            "vectors_count": info.get("vectors_count", 0),
            "status": None,
            "error": None,
        })
    try:
        from .ingestion import IngestionStore
        from .main import GATEWAY_DATA_DIR
        store = IngestionStore(GATEWAY_DATA_DIR)
        status_data = asyncio.get_event_loop().run_until_complete(store.load_status())
        projects_data = status_data.get("projects", {}) if isinstance(status_data, dict) else {}
        for pid, proj_info in projects_data.items():
            safe = _validate_project_id(pid) if _PROJECT_ID_RE.match(pid) else pid.strip()
            if safe not in seen_ids:
                seen_ids.add(safe)
                coll = project_collection_name(safe)
                info = _try_get_collection_info(coll)
                state = str(proj_info.get("state", "idle")).lower() if isinstance(proj_info, dict) else "idle"
                projects.append({
                    "project_id": safe,
                    "qdrant_collection": coll,
                    "exists": info.get("exists", False),
                    "points_count": info.get("points_count", 0),
                    "vectors_count": info.get("vectors_count", 0),
                    "status": state,
                    "error": None,
                })
    except Exception as exc:
        logger.warning("discovered_projects_failed error=%s", exc)
    return {"ok": True, "projects": projects}


@router.get("/projects/{project_id}")
async def rag_project(project_id: str) -> dict[str, Any]:
    safe = _validate_project_id(project_id)
    coll = project_collection_name(safe)
    info = _try_get_collection_info(coll)
    ingestion_status: str | None = None
    try:
        from .ingestion import IngestionStore
        from .main import GATEWAY_DATA_DIR
        store = IngestionStore(GATEWAY_DATA_DIR)
        status = asyncio.get_event_loop().run_until_complete(store.get_status(safe))
        ingestion_status = str(status.get("state", "idle")).lower() if isinstance(status, dict) else None
    except Exception:
        pass
    logical_collections: list[str] = []
    try:
        names = _rag_list_collections(project_id=safe)
        logical_collections = names
    except Exception:
        pass
    return {
        "ok": True,
        "project_id": safe,
        "qdrant_collection": coll,
        "exists": info.get("exists", False),
        "points_count": info.get("points_count", 0),
        "vectors_count": info.get("vectors_count", 0),
        "ingestion_status": ingestion_status,
        "logical_collections": logical_collections,
        "error": None,
    }


@router.get("/projects/{project_id}/stats")
async def rag_project_stats(project_id: str) -> dict[str, Any]:
    safe = _validate_project_id(project_id)
    coll = project_collection_name(safe)
    info = _try_get_collection_info(coll)
    return {
        "ok": True,
        "project_id": safe,
        "qdrant_collection": coll,
        "exists": info.get("exists", False),
        "points_count": info.get("points_count", 0),
        "vectors_count": info.get("vectors_count", 0),
        "indexed_vectors_count": info.get("indexed_vectors_count"),
        "status": info.get("status"),
        "optimizer_status": info.get("optimizer_status"),
        "error": info.get("error"),
    }


@router.get("/projects/{project_id}/collections")
async def rag_project_collections(
    project_id: str,
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    safe = _validate_project_id(project_id)
    collections: list[dict[str, Any]] = []
    counts_are_estimated = True
    try:
        coll = project_collection_name(safe)
        names: set[str] = set()
        points, _ = qdrant.scroll(
            collection_name=coll,
            scroll_filter=None,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            ns = payload.get("namespace") or payload.get("collection")
            if isinstance(ns, str) and ns.strip():
                names.add(ns.strip())
        for name in sorted(names):
            collections.append({"name": name, "point_count": None})
        counts_are_estimated = len(collections) > 0
    except Exception as exc:
        logger.warning("collections_query_failed project=%s error=%s", safe, exc)
        return {
            "ok": True,
            "project_id": safe,
            "qdrant_collection": project_collection_name(safe),
            "collections": [],
            "counts_are_estimated": True,
            "error": None,
        }
    return {
        "ok": True,
        "project_id": safe,
        "qdrant_collection": project_collection_name(safe),
        "collections": collections,
        "counts_are_estimated": counts_are_estimated,
        "error": None,
    }


@router.get("/projects/{project_id}/documents")
async def rag_project_documents(
    project_id: str,
    collection: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    safe = _validate_project_id(project_id)
    try:
        coll = project_collection_name(safe)
        query_filter = _build_filter(safe, collection)
        points, _ = qdrant.scroll(
            collection_name=coll,
            scroll_filter=query_filter,
            limit=limit + offset,
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
                        k: v for k, v in payload.items()
                        if k not in ("text", "chunk_index", "namespace", "collection", "project_id", "user_id")
                    },
                }
            doc = docs[doc_key]
            doc["chunk_count"] = doc.get("chunk_count", 0) + 1
            if point.id and (not doc["first_chunk_id"] or doc["first_chunk_id"] == str(point.id)):
                doc["first_chunk_id"] = str(point.id)
        all_docs = sorted(docs.values(), key=lambda d: d["document_id"])
        page = all_docs[offset : offset + limit]
        next_offset = offset + limit if len(page) == limit else None
        return {
            "ok": True,
            "project_id": safe,
            "collection": collection,
            "documents": page,
            "count": len(page),
            "next_offset": next_offset,
            "error": None,
        }
    except Exception as exc:
        logger.warning("documents_query_failed project=%s error=%s", safe, exc)
        return {
            "ok": True,
            "project_id": safe,
            "collection": collection,
            "documents": [],
            "count": 0,
            "next_offset": None,
            "error": None,
        }


@router.get("/projects/{project_id}/documents/{document_id}/chunks")
async def rag_document_chunks(
    project_id: str,
    document_id: str,
) -> dict[str, Any]:
    safe = _validate_project_id(project_id)
    try:
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
        chunks: list[dict[str, Any]] = []
        for point in sorted(points, key=lambda p: (p.payload or {}).get("chunk_index", 0)):
            payload = point.payload or {}
            text = payload.get("text", "")
            preview = text[:200] + ("..." if len(text) > 200 else "")
            chunks.append({
                "id": str(point.id),
                "chunk_index": payload.get("chunk_index", 0),
                "text": text,
                "text_preview": preview,
                "payload": {k: v for k, v in payload.items() if k != "text"},
            })
        return {
            "ok": True,
            "project_id": safe,
            "document_id": document_id,
            "chunks": chunks,
            "count": len(chunks),
            "error": None,
        }
    except Exception as exc:
        logger.warning("chunks_query_failed project=%s doc=%s error=%s", safe, document_id, exc)
        return {
            "ok": True,
            "project_id": safe,
            "document_id": document_id,
            "chunks": [],
            "count": 0,
            "error": None,
        }


@router.post("/projects/{project_id}/search")
async def rag_project_search(
    project_id: str,
    body: RagSearchBody,
) -> dict[str, Any]:
    safe = _validate_project_id(project_id)
    try:
        results = await search_documents(
            query=body.query,
            limit=body.limit,
            collection=body.collection,
            user_id=body.user_id,
            project_id=safe,
        )
        formatted: list[dict[str, Any]] = []
        for r in results:
            payload = r.get("payload", {})
            text = r.get("text", "")
            formatted.append({
                "id": r.get("id", ""),
                "score": r.get("score", 0),
                "text": text,
                "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
                "payload": payload,
                "document_name": payload.get("document_name", ""),
                "file_path": payload.get("file_path", payload.get("source_file", "")),
                "chunk_index": payload.get("chunk_index", 0),
            })
        return {
            "ok": True,
            "project_id": safe,
            "collection": body.collection,
            "results": formatted,
            "error": None,
        }
    except Exception as exc:
        logger.warning("search_failed project=%s error=%s", safe, exc)
        return {
            "ok": False,
            "project_id": safe,
            "collection": body.collection,
            "results": [],
            "error": "search_error",
            "detail": str(exc),
        }
