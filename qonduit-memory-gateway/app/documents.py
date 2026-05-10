"""Persistent RAG document storage, metadata, chunk/embed/upsert, and REST endpoints.

This module provides:
- ``DocumentStore`` – persistent file-system storage for uploaded documents
- ``ingest_text_document()`` – chunk, embed, and upsert helper
- ``delete_document_points()`` – remove all Qdrant points for a document
- ``DocumentRouter`` – FastAPI routes for document CRUD
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, Request
from pydantic import BaseModel
from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
)

from .parser import ParsedDocument, parse_document_bytes, is_supported_extension
from .rag import (
    embedding_backend,
    project_collection_name,
    qdrant,
    rag_service,
    VECTOR_SIZE,
    RAG_ENABLED,
)

logger = logging.getLogger("qonduit.memory_gateway.documents")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


_GW_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DATA = _GW_ROOT / "data"

RAG_UPLOAD_DIR = Path(
    os.getenv("QONDUIT_RAG_UPLOAD_DIR", str(_DEFAULT_DATA / "rag_uploads"))
).resolve()
CHAT_UPLOAD_DIR = Path(
    os.getenv("QONDUIT_CHAT_UPLOAD_DIR", str(_DEFAULT_DATA / "chat_uploads"))
).resolve()
RAG_UPLOAD_MAX_BYTES = env_int("QONDUIT_RAG_UPLOAD_MAX_BYTES", 10_485_760)
CHAT_ATTACHMENT_MAX_BYTES = env_int("QONDUIT_CHAT_ATTACHMENT_MAX_BYTES", 10_485_760)
CHAT_ATTACHMENT_MAX_CHARS = env_int(
    "QONDUIT_CHAT_ATTACHMENT_CONTEXT_MAX_CHARS", 20_000
)
RAG_UPLOAD_CHUNK_SIZE = env_int("QONDUIT_RAG_UPLOAD_CHUNK_SIZE", 4_000)
RAG_UPLOAD_CHUNK_OVERLAP = env_int("QONDUIT_RAG_UPLOAD_CHUNK_OVERLAP", 500)

_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_document_id() -> str:
    return uuid.uuid4().hex


def _sanitize_filename(filename: str) -> str:
    cleaned = filename.strip()
    cleaned = re.sub(r"[^A-Za-z0-9._\-]+", "_", cleaned)
    cleaned = cleaned.strip("._")
    return cleaned or "unnamed_file"


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# DocumentStore – persistent file-system storage
# ---------------------------------------------------------------------------


class DocumentStore:
    """File-system store for uploaded RAG documents."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = (base_dir or RAG_UPLOAD_DIR).resolve()
        _ensure_dir(self.base_dir)

    def _doc_dir(self, project_id: str, document_id: str) -> Path:
        return _ensure_dir(self.base_dir / project_id / document_id)

    def save_source(
        self,
        project_id: str,
        document_id: str,
        source_data: bytes,
        original_filename: str,
    ) -> Path:
        """Save the original uploaded file to disk."""
        doc_dir = self._doc_dir(project_id, document_id)
        safe_name = _sanitize_filename(original_filename)
        target = doc_dir / f"source_{safe_name}"
        target.write_bytes(source_data)
        return target

    def save_metadata(
        self,
        project_id: str,
        document_id: str,
        metadata: dict[str, Any],
    ) -> Path:
        """Persist document metadata as JSON."""
        doc_dir = self._doc_dir(project_id, document_id)
        meta_path = doc_dir / "metadata.json"
        meta_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return meta_path

    def load_metadata(self, project_id: str, document_id: str) -> dict[str, Any] | None:
        """Load document metadata, returning ``None`` if not found."""
        meta_path = self.base_dir / project_id / document_id / "metadata.json"
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("metadata_load_failed path=%s", meta_path)
            return None

    def source_path(self, project_id: str, document_id: str) -> Path | None:
        """Return path to the source file if it exists."""
        doc_dir = self.base_dir / project_id / document_id
        for p in doc_dir.iterdir():
            if p.name.startswith("source_"):
                return p
        return None

    def delete_document(self, project_id: str, document_id: str) -> bool:
        """Delete the entire document directory. Returns ``True`` if deleted."""
        doc_dir = self.base_dir / project_id / document_id
        if not doc_dir.exists():
            return False
        import shutil

        shutil.rmtree(doc_dir, ignore_errors=True)
        return True

    def list_document_ids(self, project_id: str) -> list[str]:
        """Return document IDs for a project."""
        proj_dir = self.base_dir / project_id
        if not proj_dir.is_dir():
            return []
        return [d.name for d in proj_dir.iterdir() if d.is_dir()]


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk_text(text: str, chunk_size: int = 4000, overlap: int = 500) -> list[str]:
    """Split *text* into overlapping chunks.

    Strategy: split on sentence boundaries (``.`` followed by whitespace) when
    possible to avoid cutting mid-sentence.
    """
    cleaned = text.strip()
    if not cleaned:
        return []

    chunks: list[str] = []
    start = 0
    text_len = len(cleaned)

    while start < text_len:
        end = min(start + chunk_size, text_len)
        # Try to break at a sentence boundary
        if end < text_len:
            # Look back up to 200 chars for a good break point
            search_end = min(end + 200, text_len)
            # Try period-space, newline, or just take the char at end
            for sep in (". \n", ".\n", " \n", "\n", ". "):
                idx = cleaned.rfind(sep, end - 200, end)
                if idx > start:
                    end = idx + len(sep)
                    break

        chunk = cleaned[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_len:
            break
        start = max(end - overlap, 0)

    return chunks


# ---------------------------------------------------------------------------
# Ingest helper – chunk, embed, upsert
# ---------------------------------------------------------------------------


async def ingest_text_document(
    project_id: str,
    collection: str,
    document_name: str,
    text: str,
    metadata: dict[str, Any],
    source: str,
    saved_path: str,
    document_id: str | None = None,
    user_id: str | None = None,
    google_file_id: str | None = None,
) -> dict[str, Any]:
    """Chunk, embed, and upsert *text* into the project-scoped Qdrant collection.

    Returns a result dict with ``chunks_created``, ``chunks_written``,
    ``document_id``, and ``warnings``.
    """
    if not text.strip():
        return {
            "document_id": document_id or "none",
            "chunks_created": 0,
            "chunks_written": 0,
            "warnings": ["Document text is empty after parsing"],
        }

    doc_id = document_id or _safe_document_id()
    norm_collection = collection.strip() or "default"
    norm_project = project_id.strip().lower() or "default"

    # Chunk
    chunks = chunk_text(text, chunk_size=RAG_UPLOAD_CHUNK_SIZE, overlap=RAG_UPLOAD_CHUNK_OVERLAP)
    if not chunks:
        return {
            "document_id": doc_id,
            "chunks_created": 0,
            "chunks_written": 0,
            "warnings": ["No chunks generated from document text"],
        }

    # Ensure collection
    coll_name = project_collection_name(norm_project)
    try:
        rag_service.ensure_collection(norm_project)
    except Exception as exc:
        logger.warning("ensure_collection_failed collection=%s error=%s", coll_name, exc)

    # Embed and upsert each chunk
    points: list[PointStruct] = []
    for idx, chunk in enumerate(chunks):
        try:
            vector = await embedding_backend.embed_query(chunk)
        except Exception as exc:
            logger.warning("embedding_failed chunk=%d error=%s", idx, exc)
            continue

        payload: dict[str, Any] = {
            "project_id": norm_project,
            "collection": norm_collection,
            "namespace": norm_collection,
            "document_id": doc_id,
            "document_name": document_name,
            "source": source,
            "file_path": saved_path,
            "file_type": metadata.get("file_type", ""),
            "mime_type": metadata.get("mime_type", ""),
            "chunk_index": idx,
            "text": chunk,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        if user_id:
            payload["user_id"] = user_id
        if google_file_id:
            payload["google_file_id"] = google_file_id
        # Merge any extra metadata keys (skip reserved)
        reserved = {"file_type", "mime_type"}
        for k, v in metadata.items():
            if k not in reserved and k not in payload:
                payload[k] = v

        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload=payload,
            )
        )

    # Upsert
    written = 0
    if points:
        try:
            qdrant.upsert(collection_name=coll_name, points=points)
            written = len(points)
        except Exception as exc:
            logger.warning("upsert_failed collection=%s error=%s", coll_name, exc)

    return {
        "document_id": doc_id,
        "chunks_created": len(chunks),
        "chunks_written": written,
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Delete helpers
# ---------------------------------------------------------------------------


def delete_document_points(
    project_id: str,
    document_id: str,
) -> int:
    """Delete all Qdrant points matching *project_id* + *document_id*.

    Returns the number of points deleted.
    """
    norm_project = project_id.strip().lower() or "default"
    coll_name = project_collection_name(norm_project)

    must_conditions = [
        FieldCondition(
            key="project_id",
            match=MatchValue(value=norm_project),
        ),
        FieldCondition(
            key="document_id",
            match=MatchValue(value=document_id),
        ),
    ]
    query_filter = Filter(must=must_conditions)

    # Scroll all matching points to count
    points, _ = qdrant.scroll(
        collection_name=coll_name,
        scroll_filter=query_filter,
        limit=10_000,
        with_payload=False,
        with_vectors=False,
    )
    count = len(points)

    if count > 0:
        try:
            # Qdrant delete by filter (batch)
            qdrant.delete(collection_name=coll_name, points_selector=query_filter)
        except Exception as exc:
            logger.warning("qdrant_delete_failed error=%s", exc)

    return count


# ---------------------------------------------------------------------------
# Router – REST endpoints
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/v1/rag/projects", tags=["rag-documents"])
_store = DocumentStore()


@router.post("/{project_id}/documents/upload")
async def upload_document(
    project_id: str,
    file: UploadFile = File(...),
    collection: str | None = Form(None),
    user_id: str | None = Form(None),
    document_name: str | None = Form(None),
    metadata_str: str | None = Form(None),
    google_file_id: str | None = Form(None),
) -> dict[str, Any]:
    """Upload a file for RAG indexing.

    Expects ``multipart/form-data`` with fields:
    - ``file`` (required)
    - ``collection`` (optional)
    - ``user_id`` (optional)
    - ``document_name`` (optional override)
    - ``metadata`` (optional, JSON string)
    - ``google_file_id`` (optional)
    """
    _validate_project_id(project_id)

    # Validate filename
    original_filename = file.filename or "unnamed_file"
    if not is_supported_extension(original_filename):
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": "unsupported_file_type",
                "detail": f"File type '{original_filename}' is not supported",
            },
        )

    # Read and size-check
    file_data = await file.read()
    if len(file_data) > RAG_UPLOAD_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "ok": False,
                "error": "file_too_large",
                "detail": f"File exceeds {RAG_UPLOAD_MAX_BYTES} bytes",
            },
        )

    norm_collection = (collection or "").strip() or "default"
    doc_name = document_name or original_filename
    norm_user = (user_id or "").strip() or None

    # Parse
    parsed = parse_document_bytes(file_data, original_filename, file.content_type)

    # Generate IDs
    doc_id = _safe_document_id()
    safe_project = project_id.strip().lower() or "default"

    # Save source
    try:
        source_target = _store.save_source(safe_project, doc_id, file_data, original_filename)
        saved_path = str(source_target)
    except Exception as exc:
        logger.exception("save_source_failed")
        raise HTTPException(status_code=500, detail=f"Failed to save source file: {exc}")

    # Parse metadata
    extra_meta: dict[str, Any] = dict(parsed.metadata)
    if metadata_str:
        try:
            extra_meta.update(json.loads(metadata_str))
        except json.JSONDecodeError:
            pass  # Ignore malformed metadata JSON

    # Ingest
    result = await ingest_text_document(
        project_id=safe_project,
        collection=norm_collection,
        document_name=doc_name,
        text=parsed.text,
        metadata=extra_meta,
        source="file_upload",
        saved_path=saved_path,
        document_id=doc_id,
        user_id=norm_user,
        google_file_id=google_file_id,
    )

    # Persist metadata
    meta_doc: dict[str, Any] = {
        "document_id": doc_id,
        "project_id": safe_project,
        "collection": norm_collection,
        "document_name": doc_name,
        "original_filename": original_filename,
        "saved_path": saved_path,
        "source": "file_upload",
        "file_type": parsed.file_type,
        "mime_type": file.content_type or "",
        "parser": parsed.parser,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "user_id": norm_user,
        "chunk_count": result.get("chunks_written", 0),
        "qdrant_collection": project_collection_name(safe_project),
    }
    if google_file_id:
        meta_doc["google_file_id"] = google_file_id
    meta_doc.update(extra_meta)

    try:
        _store.save_metadata(safe_project, doc_id, meta_doc)
    except Exception:
        logger.warning("save_metadata_failed document_id=%s", doc_id)

    return {
        "ok": True,
        "project_id": safe_project,
        "collection": norm_collection,
        "document_id": doc_id,
        "document_name": doc_name,
        "saved_path": saved_path,
        "file_type": parsed.file_type,
        "chunks_created": result["chunks_created"],
        "chunks_written": result["chunks_written"],
        "parser": parsed.parser,
        "warnings": parsed.warnings + result.get("warnings", []),
        "error": None,
    }


@router.post("/{project_id}/documents/text")
async def upload_text_document(
    project_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Upload plain text for RAG indexing.

    JSON body fields:
    - ``text`` (required)
    - ``collection`` (optional)
    - ``document_name`` (optional)
    - ``metadata`` (optional dict)
    - ``user_id`` (optional)
    - ``source`` (optional, default ``text_upload``)
    """
    _validate_project_id(project_id)

    text = body.get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(
            status_code=400,
            detail={"ok": False, "error": "missing_text", "detail": "text field is required"},
        )

    norm_collection = (body.get("collection") or "").strip() or "default"
    doc_name = body.get("document_name") or "text_upload"
    norm_user = (body.get("user_id") or "").strip() or None
    source = body.get("source") or "text_upload"
    extra_meta = body.get("metadata") or {}
    if not isinstance(extra_meta, dict):
        extra_meta = {}

    safe_project = project_id.strip().lower() or "default"
    doc_id = _safe_document_id()

    parsed_text = text
    parsed_warnings: list[str] = []
    parsed_parser = "plain_text"
    parsed_file_type = "txt"

    # Save placeholder source (no actual file, just metadata)
    doc_dir = _store._doc_dir(safe_project, doc_id)
    saved_path = str(doc_dir / "source_text")
    _ensure_dir(doc_dir)

    result = await ingest_text_document(
        project_id=safe_project,
        collection=norm_collection,
        document_name=doc_name,
        text=parsed_text,
        metadata={
            "file_type": parsed_file_type,
            **extra_meta,
        },
        source=source,
        saved_path=saved_path,
        document_id=doc_id,
        user_id=norm_user,
    )

    meta_doc: dict[str, Any] = {
        "document_id": doc_id,
        "project_id": safe_project,
        "collection": norm_collection,
        "document_name": doc_name,
        "original_filename": f"{doc_name}.txt",
        "saved_path": saved_path,
        "source": source,
        "file_type": parsed_file_type,
        "mime_type": "text/plain",
        "parser": parsed_parser,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "user_id": norm_user,
        "chunk_count": result.get("chunks_written", 0),
        "qdrant_collection": project_collection_name(safe_project),
        "_reingest_text": parsed_text,
    }
    meta_doc.update(extra_meta)

    try:
        _store.save_metadata(safe_project, doc_id, meta_doc)
    except Exception:
        logger.warning("save_metadata_failed document_id=%s", doc_id)

    return {
        "ok": True,
        "project_id": safe_project,
        "collection": norm_collection,
        "document_id": doc_id,
        "document_name": doc_name,
        "saved_path": saved_path,
        "file_type": parsed_file_type,
        "chunks_created": result["chunks_created"],
        "chunks_written": result["chunks_written"],
        "parser": parsed_parser,
        "warnings": parsed_warnings + result.get("warnings", []),
        "error": None,
    }


class DeleteDocumentResponse(BaseModel):
    ok: bool
    project_id: str
    document_id: str
    points_deleted: int
    source_deleted: bool
    error: str | None = None


class ReingestRequest(BaseModel):
    pass


class SourceResponse(BaseModel):
    ok: bool
    project_id: str
    document_id: str
    metadata: dict[str, Any]
    text_preview: str
    full_text: str | None = None
    error: str | None = None


@router.delete("/{project_id}/documents/{document_id}")
async def delete_document(
    project_id: str,
    document_id: str,
) -> dict[str, Any]:
    """Delete a document and all its Qdrant chunks."""
    _validate_project_id(project_id)

    safe_project = project_id.strip().lower() or "default"

    # Delete Qdrant points
    points_deleted = delete_document_points(safe_project, document_id)

    # Delete source files and metadata
    source_deleted = _store.delete_document(safe_project, document_id)

    return {
        "ok": True,
        "project_id": safe_project,
        "document_id": document_id,
        "points_deleted": points_deleted,
        "source_deleted": source_deleted,
        "error": None,
    }


@router.post("/{project_id}/documents/{document_id}/reingest")
async def reingest_document(
    project_id: str,
    document_id: str,
) -> dict[str, Any]:
    """Re-parse, re-chunk, re-embed, and re-upsert a document."""
    _validate_project_id(project_id)

    safe_project = project_id.strip().lower() or "default"

    # Load metadata
    meta = _store.load_metadata(safe_project, document_id)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": "document_not_found",
                "detail": f"Document {document_id} not found",
            },
        )

    # Delete old chunks
    points_deleted = delete_document_points(safe_project, document_id)
    logger.info(
        "reingest_deleted_old_chunks document_id=%s points=%s",
        document_id,
        points_deleted,
    )

    # Find source file
    src = _store.source_path(safe_project, document_id)
    has_source_file = src is not None and src.exists()

    # For text-only documents, fall back to text stored in metadata
    stored_text = meta.get("_reingest_text")
    if not has_source_file and not stored_text:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": "source_not_found",
                "detail": f"Source file for document {document_id} not found",
            },
        )

    # Read source and re-parse
    if has_source_file:
        raw_data = src.read_bytes()
        original_filename = meta.get("original_filename", src.name)
        parsed = parse_document_bytes(raw_data, original_filename, meta.get("mime_type"))
    else:
        # Text-only document: use stored text directly
        original_filename = meta.get("original_filename", "text_upload.txt")
        parsed = ParsedDocument(
            text=stored_text,
            parser="plain_text",
            file_type="txt",
            metadata={},
            warnings=["reingest_from_stored_text"],
        )

    # Re-ingest
    result = await ingest_text_document(
        project_id=safe_project,
        collection=meta.get("collection", "default"),
        document_name=meta.get("document_name", original_filename),
        text=parsed.text,
        metadata={
            k: v
            for k, v in meta.items()
            if k not in ("document_id", "created_at", "updated_at", "chunk_count")
        },
        source=meta.get("source", "reingest"),
        saved_path=str(src),
        document_id=document_id,
        user_id=meta.get("user_id"),
        google_file_id=meta.get("google_file_id"),
    )

    # Update metadata timestamps
    meta["updated_at"] = _now_iso()
    meta["chunk_count"] = result.get("chunks_written", 0)
    try:
        _store.save_metadata(safe_project, document_id, meta)
    except Exception:
        logger.warning("save_metadata_failed document_id=%s", document_id)

    return {
        "ok": True,
        "project_id": safe_project,
        "document_id": document_id,
        "points_deleted": points_deleted,
        "chunks_created": result["chunks_created"],
        "chunks_written": result["chunks_written"],
        "warnings": parsed.warnings + result.get("warnings", []),
        "error": None,
    }


@router.get("/{project_id}/documents/{document_id}/source")
async def get_document_source(
    project_id: str,
    document_id: str,
    include_text: bool = Query(default=False),
    max_chars: int = Query(default=5000, ge=0),
) -> dict[str, Any]:
    """Fetch document metadata and an optional text preview."""
    _validate_project_id(project_id)

    safe_project = project_id.strip().lower() or "default"

    meta = _store.load_metadata(safe_project, document_id)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": "document_not_found",
                "detail": f"Document {document_id} not found",
            },
        )

    # Build response metadata (exclude internal fields)
    response_meta = {
        k: v for k, v in meta.items()
        if k not in ("saved_path",)
    }

    text_preview = ""
    full_text: str | None = None

    if include_text:
        src = _store.source_path(safe_project, document_id)
        if src and src.exists():
            try:
                raw = src.read_bytes()
                full_text = raw.decode("utf-8", errors="replace")
                text_preview = full_text[:max_chars]
                if len(full_text) > max_chars:
                    text_preview += f"\n\n... (truncated, {len(full_text) - max_chars} more chars)"
            except Exception as exc:
                logger.warning("read_source_failed document_id=%s error=%s", document_id, exc)
                text_preview = f"Error reading source: {exc}"
        else:
            text_preview = "Source file not found on disk"

    return {
        "ok": True,
        "project_id": safe_project,
        "document_id": document_id,
        "metadata": response_meta,
        "text_preview": text_preview,
        "full_text": full_text if include_text else None,
        "error": None,
    }
