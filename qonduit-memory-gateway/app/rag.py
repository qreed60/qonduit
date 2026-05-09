from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import httpx
import os
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

logger = logging.getLogger("qonduit.memory_gateway")

QDRANT_URL = os.getenv("QDRANT_URL", "http://192.168.5.5:6333").strip() or "http://192.168.5.5:6333"
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()
EMBEDDING_BASE = os.getenv("EMBEDDING_BASE", "http://192.168.5.5:8082").strip() or "http://192.168.5.5:8082"
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2").strip() or "all-MiniLM-L6-v2"
VECTOR_SIZE = int(os.getenv("EMBEDDING_VECTOR_SIZE", "384"))
RAG_TOP_K = max(1, int(os.getenv("RAG_TOP_K", "4")))
RAG_ENABLED = os.getenv("RAG_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

COLLECTION_PREFIX = "qonduit_rag"
COLLECTION_NAME = COLLECTION_PREFIX


def _normalize_identifier(value: str | None, fallback: str) -> str:
    raw = (value or "").strip().lower()
    safe = "".join(c for c in raw if c.isalnum() or c in ("-", "_"))
    return safe or fallback


def project_collection_name(project_id: str | None) -> str:
    project = _normalize_identifier(project_id, "default")
    return f"{COLLECTION_PREFIX}__{project}"


def _build_client() -> QdrantClient:
    if QDRANT_API_KEY:
        return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return QdrantClient(url=QDRANT_URL)


qdrant = _build_client()


class EmbeddingBackend:
    """Adapter for OpenAI-compatible embedding backends."""

    def __init__(self, base_url: str, default_model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model

    async def create_embeddings(
        self,
        input_data: str | list[str],
        model: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "input": input_data,
            "model": model or self.default_model,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{self.base_url}/v1/embeddings", json=payload)
            response.raise_for_status()
            data = response.json()

        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise ValueError("Embedding backend returned invalid response shape")
        return data

    async def embed_query(self, text: str, model: str | None = None) -> list[float]:
        response = await self.create_embeddings(text, model=model)
        vectors = response.get("data", [])
        if not vectors:
            raise ValueError("Embedding backend returned an empty vector list")
        embedding = vectors[0].get("embedding")
        if not isinstance(embedding, list):
            raise ValueError("Embedding backend returned invalid vector")
        return [float(v) for v in embedding]


embedding_backend = EmbeddingBackend(EMBEDDING_BASE, EMBEDDING_MODEL)


class ProjectScopedRagService:
    """Project-scoped retrieval service backed by Qdrant."""

    def __init__(self, client: QdrantClient, embedding: EmbeddingBackend) -> None:
        self.client = client
        self.embedding = embedding

    def ensure_collection(self, project_id: str | None = None) -> str:
        collection_name = project_collection_name(project_id)
        try:
            self.client.get_collection(collection_name)
        except Exception:
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
        return collection_name

    async def add_document(
        self,
        *,
        project_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        point_id: str | None = None,
        user_id: str | None = None,
        namespace: str | None = None,
    ) -> str:
        payload = dict(metadata or {})
        payload["project_id"] = _normalize_identifier(project_id, "default")
        payload["user_id"] = _normalize_identifier(user_id, "default")
        payload["namespace"] = _normalize_identifier(namespace, "default")

        vector = await self.embedding.embed_query(text)
        doc_id = point_id or str(uuid.uuid4())
        collection_name = self.ensure_collection(project_id)

        self.client.upsert(
            collection_name=collection_name,
            points=[
                PointStruct(
                    id=doc_id,
                    vector=vector,
                    payload={"text": text, **payload},
                )
            ],
        )
        return doc_id

    async def search(
        self,
        *,
        project_id: str,
        query: str,
        top_k: int,
        user_id: str | None,
        namespace: str | None,
        perf: Any | None = None,
        collection_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        if not RAG_ENABLED:
            return []

        if perf is None:
            vector = await self.embedding.embed_query(query)
        else:
            with perf.step("query_embedding"):
                vector = await self.embedding.embed_query(query)
        collection_name = self.ensure_collection(project_id)

        must_conditions = [
            FieldCondition(
                key="project_id",
                match=MatchValue(value=_normalize_identifier(project_id, "default")),
            )
        ]

        if user_id:
            must_conditions.append(
                FieldCondition(
                    key="user_id",
                    match=MatchValue(value=_normalize_identifier(user_id, "default")),
                )
            )

        if namespace:
            must_conditions.append(
                FieldCondition(
                    key="namespace",
                    match=MatchValue(value=_normalize_identifier(namespace, "default")),
                )
            )

        if collection_filter:
            must_conditions.append(
                FieldCondition(
                    key="collection",
                    match=MatchValue(value=_normalize_identifier(collection_filter, "default")),
                )
            )

        if perf is None:
            hits = self.client.search(
                collection_name=collection_name,
                query_vector=vector,
                query_filter=Filter(must=must_conditions),
                limit=max(1, top_k),
            )
        else:
            with perf.step("qdrant_search"):
                hits = self.client.search(
                    collection_name=collection_name,
                    query_vector=vector,
                    query_filter=Filter(must=must_conditions),
                    limit=max(1, top_k),
                )

        results: list[dict[str, Any]] = []
        for hit in hits:
            payload = hit.payload or {}
            results.append(
                {
                    "id": str(hit.id),
                    "score": hit.score,
                    "text": payload.get("text", ""),
                    "payload": payload,
                }
            )
        return results


rag_service = ProjectScopedRagService(qdrant, embedding_backend)


def ensure_collection(project_id: str | None = None) -> None:
    rag_service.ensure_collection(project_id)


async def embed_text(text: str, model: str | None = None) -> list[float]:
    return await embedding_backend.embed_query(text, model=model)


async def create_embeddings_response(
    input_data: str | list[str],
    model: str | None = None,
) -> dict[str, Any]:
    return await embedding_backend.create_embeddings(input_data, model=model)


async def add_document(
    text: str,
    metadata: dict | None = None,
    point_id: str | None = None,
    user_id: str | None = None,
    collection: str | None = None,
    project_id: str | None = None,
) -> str:
    return await rag_service.add_document(
        project_id=project_id or "default",
        text=text,
        metadata=metadata or {},
        point_id=point_id,
        user_id=user_id,
        namespace=collection,
    )


async def search_documents(
    query: str,
    limit: int = 4,
    collection: str | None = None,
    user_id: str | None = None,
    project_id: str | None = None,
    perf: Any | None = None,
    collection_filter: str | None = None,
) -> list[dict]:
    return await rag_service.search(
        project_id=project_id or "default",
        query=query,
        top_k=limit,
        user_id=user_id,
        namespace=collection,
        perf=perf,
        collection_filter=collection_filter,
    )


def list_collections(
    user_id: str | None = None,
    project_id: str | None = None,
) -> list[str]:
    collection_name = project_collection_name(project_id)
    try:
        hits, _ = qdrant.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="source",
                        match=MatchValue(value="collection_marker"),
                    ),
                    FieldCondition(
                        key="project_id",
                        match=MatchValue(
                            value=_normalize_identifier(project_id, "default"),
                        ),
                    ),
                ]
            ),
            limit=1000,
            with_payload=True,
            with_vectors=False,
        )
    except Exception as error:
        logger.warning("rag_list_collections_failed error=%s", str(error))
        return []

    names = []
    for hit in hits:
        payload = hit.payload or {}
        if user_id and payload.get("user_id") != _normalize_identifier(user_id, "default"):
            continue
        name = payload.get("namespace") or payload.get("collection")
        if isinstance(name, str) and name.strip():
            names.append(name)

    return sorted(set(names))


async def create_collection_marker(
    name: str,
    user_id: str | None = None,
    project_id: str | None = None,
) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Collection name cannot be empty")

    existing = list_collections(user_id=user_id, project_id=project_id)
    if normalized in existing:
        return normalized

    marker_text = f"Collection marker for {normalized}"
    await add_document(
        text=marker_text,
        metadata={
            "source": "collection_marker",
            "collection": normalized,
            "document_name": "__collection_marker__",
        },
        user_id=user_id,
        collection=normalized,
        project_id=project_id,
    )
    return normalized
