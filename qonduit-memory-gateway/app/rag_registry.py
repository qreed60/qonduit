"""Persistent registry for RAG projects and logical collections.

This module provides:
- ``RagRegistry`` – file-system backed JSON registry with atomic writes
- Validation/sanitization of project IDs and collection names
- CRUD operations for projects and logical collections
- Merging registry data with discovered Qdrant collections
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .rag import project_collection_name, qdrant

logger = logging.getLogger("qonduit.memory_gateway.registry")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_COLLECTION_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_MAX_NAME_LENGTH = 128
_DEFAULT_DATA_DIR = Path(os.getenv("GATEWAY_DATA_DIR", "/app/data").strip() or "/app/data")
_REGISTRY_FILE = _DEFAULT_DATA_DIR / "rag_registry.json"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_project_id(project_id: str) -> str:
    """Validate and normalize a project_id.

    Returns the normalized project_id or raises ValueError.
    """
    if not project_id or not isinstance(project_id, str):
        raise ValueError("project_id is required")
    raw = project_id.strip().lower()
    raw = raw.replace(" ", "-")
    safe = "".join(c for c in raw if c.isalnum() or c in ("-", "_"))
    if not safe:
        raise ValueError("project_id must contain at least one valid character")
    if len(safe) > _MAX_NAME_LENGTH:
        raise ValueError(f"project_id must be at most {_MAX_NAME_LENGTH} characters")
    if not _PROJECT_ID_RE.match(safe):
        raise ValueError(
            "project_id must start with a lowercase letter or digit, "
            "and contain only lowercase letters, digits, hyphens, and underscores"
        )
    return safe


def validate_collection_name(name: str) -> str:
    """Validate and normalize a logical collection name.

    Returns the normalized name or raises ValueError.
    """
    if not name or not isinstance(name, str):
        raise ValueError("collection name is required")
    raw = name.strip().lower()
    raw = raw.replace(" ", "-")
    safe = "".join(c for c in raw if c.isalnum() or c in ("-", "_"))
    if not safe:
        raise ValueError("collection name must contain at least one valid character")
    if len(safe) > _MAX_NAME_LENGTH:
        raise ValueError(f"collection name must be at most {_MAX_NAME_LENGTH} characters")
    if not _COLLECTION_NAME_RE.match(safe):
        raise ValueError(
            "collection name must start with a lowercase letter or digit, "
            "and contain only lowercase letters, digits, hyphens, and underscores"
        )
    return safe


def _normalize_display_name(name: str | None, fallback: str) -> str:
    if not name or not isinstance(name, str):
        return fallback
    result = name.strip()
    if not result:
        return fallback
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Registry storage
# ---------------------------------------------------------------------------


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically using temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix=".rag_registry_"
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
    except Exception as exc:
        logger.error("atomic_write_json_failed path=%s error=%s", path, exc)
        raise


def _load_registry(path: Path | None = None) -> dict[str, Any]:
    """Load the registry from disk, returning a fresh structure if missing/corrupt."""
    target = path or _REGISTRY_FILE
    try:
        if target.exists():
            raw = target.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict) and "projects" in data:
                return data
            logger.warning("registry_corrupt path=%s reinitializing", target)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("registry_load_error path=%s error=%s", target, exc)
    return {"projects": {}, "metadata": {"version": 1, "updated_at": _now_iso()}}


def _save_registry(data: dict[str, Any], path: Path | None = None) -> None:
    """Save the registry to disk."""
    data["metadata"]["updated_at"] = _now_iso()
    _atomic_write_json(path or _REGISTRY_FILE, data)


# ---------------------------------------------------------------------------
# Qdrant helper
# ---------------------------------------------------------------------------


def _try_get_collection_info(collection_name: str) -> dict[str, Any]:
    """Get Qdrant collection info, returning safe defaults on failure."""
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
        logger.warning(
            "get_collection_info_failed collection=%s error=%s", collection_name, exc
        )
        return {
            "exists": False,
            "points_count": 0,
            "vectors_count": 0,
            "indexed_vectors_count": None,
            "status": None,
            "optimizer_status": None,
            "error": str(exc),
        }


def _discover_qdrant_collections(prefix: str = "qonduit_rag__") -> list[str]:
    """Discover physical Qdrant collections matching the project prefix."""
    try:
        all_collections = qdrant.get_collections().collections
        return [c.name for c in all_collections if c.name.startswith(prefix)]
    except Exception as exc:
        logger.warning("discover_qdrant_collections_failed error=%s", exc)
        return []


def _discover_logical_collections(project_id: str) -> list[str]:
    """Discover logical collection names from Qdrant payload metadata."""
    collection_name = project_collection_name(project_id)
    try:
        points, _ = qdrant.scroll(
            collection_name=collection_name,
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
        return sorted(names)
    except Exception as exc:
        logger.warning(
            "discover_logical_collections_failed project=%s error=%s", project_id, exc
        )
        return []


def _count_points_for_collection(
    project_id: str, logical_collection: str
) -> int:
    """Count Qdrant points for a project + logical collection."""
    collection_name = project_collection_name(project_id)
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    try:
        points, _ = qdrant.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="project_id",
                        match=MatchValue(value=project_id),
                    ),
                    FieldCondition(
                        key="collection",
                        match=MatchValue(value=logical_collection),
                    ),
                ]
            ),
            limit=1,
            with_payload=False,
            with_vectors=False,
        )
        return len(points)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Registry class
# ---------------------------------------------------------------------------


class RagRegistry:
    """In-memory + file-backed registry for RAG projects and collections."""

    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            self._path = _REGISTRY_FILE
        elif isinstance(path, str):
            self._path = Path(path)
        else:
            self._path = path
        self._data: dict[str, Any] = _load_registry(self._path)

    def reload(self) -> None:
        """Reload registry from disk."""
        self._data = _load_registry(self._path)

    def _ensure_project_in_data(self, project_id: str) -> dict[str, Any]:
        """Ensure a project entry exists in the in-memory data dict."""
        if project_id not in self._data["projects"]:
            self._data["projects"][project_id] = {
                "project_id": project_id,
                "display_name": project_id.replace("-", " ").title(),
                "description": "",
                "qdrant_collection": project_collection_name(project_id),
                "default_collection": "default",
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "metadata": {},
                "collections": {"default": {"name": "default"}},
            }
        return self._data["projects"][project_id]

    def ensure_project(
        self,
        project_id: str | None = None,
        display_name: str | None = None,
        description: str | None = None,
        default_collection: str | None = None,
        metadata: dict[str, Any] | None = None,
        ensure_qdrant: bool = False,
    ) -> tuple[dict[str, Any], bool, list[str]]:
        """Ensure a project exists in the registry.

        Returns (project_dict, created, warnings).
        """
        warnings: list[str] = []
        safe_id = validate_project_id(project_id) if project_id else "default"

        # Try to derive project_id from display_name if not provided
        if not project_id:
            safe_id = validate_project_id(display_name) if display_name else "default"

        created = safe_id not in self._data["projects"]
        project = self._ensure_project_in_data(safe_id)

        if created:
            project["display_name"] = _normalize_display_name(
                display_name, safe_id.replace("-", " ").title()
            )
            project["description"] = description or ""
            project["default_collection"] = validate_collection_name(
                default_collection or "default"
            )
        else:
            # Update fields if provided
            if display_name:
                project["display_name"] = _normalize_display_name(
                    display_name, project["display_name"]
                )
            if description is not None:
                project["description"] = description
            if default_collection:
                project["default_collection"] = validate_collection_name(
                    default_collection
                )
            if metadata:
                existing_meta = project.get("metadata", {})
                if isinstance(existing_meta, dict):
                    existing_meta.update(metadata)
                project["metadata"] = existing_meta

        project["updated_at"] = _now_iso()

        # Ensure default collection exists
        dc = project.get("default_collection", "default")
        self._ensure_collection_in_project(safe_id, dc)

        # Attempt Qdrant collection creation if requested
        if ensure_qdrant:
            try:
                from .rag import VECTOR_SIZE, embedding_backend
                from qdrant_client.models import Distance, VectorParams

                qdrant_collection = project_collection_name(safe_id)
                try:
                    qdrant.get_collection(qdrant_collection)
                except Exception:
                    qdrant.create_collection(
                        collection_name=qdrant_collection,
                        vectors_config=VectorParams(
                            size=VECTOR_SIZE, distance=Distance.COSINE
                        ),
                    )
            except Exception as exc:
                warning_msg = f"qdrant_collection_create_failed: {exc}"
                warnings.append(warning_msg)
                logger.warning(
                    "ensure_project_qdrant_failed project=%s error=%s", safe_id, exc
                )

        self._persist()
        return project, created, warnings

    def _ensure_collection_in_project(
        self, project_id: str, collection_name: str
    ) -> dict[str, Any]:
        """Ensure a logical collection exists within a project."""
        safe_proj = validate_project_id(project_id)
        safe_coll = validate_collection_name(collection_name)

        project = self._ensure_project_in_data(safe_proj)
        if "collections" not in project or not isinstance(project["collections"], dict):
            project["collections"] = {}

        if safe_coll not in project["collections"]:
            project["collections"][safe_coll] = {
                "name": safe_coll,
                "display_name": safe_coll.replace("-", " ").title(),
                "description": "",
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "metadata": {},
                "document_count": None,
                "chunk_count": None,
            }

        return project["collections"][safe_coll]

    def _persist(self) -> None:
        """Save current in-memory state to disk."""
        _save_registry(self._data, self._path)

    # -----------------------------------------------------------------------
    # List projects
    # -----------------------------------------------------------------------

    def list_projects(self) -> list[dict[str, Any]]:
        """List all projects with Qdrant stats merged in."""
        results: list[dict[str, Any]] = []

        # Registry projects
        for pid, proj in self._data.get("projects", {}).items():
            coll = project_collection_name(pid)
            info = _try_get_collection_info(coll)
            collections = proj.get("collections", {})
            results.append(
                {
                    "project_id": pid,
                    "display_name": proj.get("display_name", pid),
                    "description": proj.get("description", ""),
                    "qdrant_collection": coll,
                    "default_collection": proj.get("default_collection", "default"),
                    "created_at": proj.get("created_at"),
                    "updated_at": proj.get("updated_at"),
                    "collections_count": len(collections) if isinstance(collections, dict) else 0,
                    "exists_in_qdrant": info.get("exists", False),
                    "points_count": info.get("points_count", 0),
                    "error": None,
                }
            )

        # Discover Qdrant collections not in registry
        qdrant_collections = _discover_qdrant_collections()
        registered_ids = set(self._data.get("projects", {}).keys())
        for qcoll in qdrant_collections:
            if qcoll.startswith("qonduit_rag__"):
                pid = qcoll[len("qonduit_rag__"):]
                if pid and pid not in registered_ids:
                    info = _try_get_collection_info(qcoll)
                    results.append(
                        {
                            "project_id": pid,
                            "display_name": pid.replace("-", " ").title(),
                            "description": "",
                            "qdrant_collection": qcoll,
                            "default_collection": "default",
                            "created_at": None,
                            "updated_at": None,
                            "collections_count": 0,
                            "exists_in_qdrant": info.get("exists", False),
                            "points_count": info.get("points_count", 0),
                            "discovered": True,
                            "error": None,
                        }
                    )

        return results

    # -----------------------------------------------------------------------
    # Get project
    # -----------------------------------------------------------------------

    def get_project(
        self, project_id: str, include_qdrant: bool = True
    ) -> dict[str, Any]:
        """Get a single project with optional Qdrant stats."""
        safe_id = validate_project_id(project_id)

        if safe_id not in self._data.get("projects", {}):
            # Check if it exists in Qdrant but not in registry
            qcoll = project_collection_name(safe_id)
            info = _try_get_collection_info(qcoll)
            if info.get("exists", False) and info.get("points_count", 0) > 0:
                return {
                    "ok": True,
                    "project": {
                        "project_id": safe_id,
                        "display_name": safe_id.replace("-", " ").title(),
                        "description": "",
                        "qdrant_collection": qcoll,
                        "default_collection": "default",
                        "created_at": None,
                        "updated_at": None,
                        "collections": {},
                        "metadata": {},
                        "discovered": True,
                    },
                    "qdrant": info,
                    "ingestion_status": None,
                }
            raise ValueError(f"Project not found: {safe_id}")

        proj = self._data["projects"][safe_id]
        coll = project_collection_name(safe_id)
        info = _try_get_collection_info(coll) if include_qdrant else {}

        # Build collections list
        raw_collections = proj.get("collections", {})
        collections_list = []
        if isinstance(raw_collections, dict):
            for cname, cdata in raw_collections.items():
                if not isinstance(cdata, dict):
                    continue
                qdrant_chunk_count = _count_points_for_collection(safe_id, cname)
                collections_list.append({
                    "name": cdata.get("name", cname),
                    "display_name": cdata.get(
                        "display_name", cname.replace("-", " ").title()
                    ),
                    "description": cdata.get("description", ""),
                    "created_at": cdata.get("created_at"),
                    "updated_at": cdata.get("updated_at"),
                    "metadata": cdata.get("metadata", {}),
                    "document_count": cdata.get("document_count"),
                    "chunk_count": qdrant_chunk_count if qdrant_chunk_count > 0 else cdata.get("chunk_count"),
                    "counts_are_estimated": qdrant_chunk_count > 0,
                })

        return {
            "ok": True,
            "project": {
                "project_id": safe_id,
                "display_name": proj.get("display_name", safe_id),
                "description": proj.get("description", ""),
                "qdrant_collection": coll,
                "default_collection": proj.get("default_collection", "default"),
                "created_at": proj.get("created_at"),
                "updated_at": proj.get("updated_at"),
                "collections_count": len(collections_list),
                "collections": collections_list,
                "metadata": proj.get("metadata", {}),
            },
            "qdrant": info if include_qdrant else {
                "exists": True,
                "points_count": 0,
                "vectors_count": 0,
                "error": None,
            },
            "ingestion_status": None,
        }

    # -----------------------------------------------------------------------
    # Update project
    # -----------------------------------------------------------------------

    def update_project(
        self,
        project_id: str,
        display_name: str | None = None,
        description: str | None = None,
        default_collection: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update project metadata fields."""
        safe_id = validate_project_id(project_id)

        if safe_id not in self._data.get("projects", {}):
            raise ValueError(f"Project not found: {safe_id}")

        project = self._data["projects"][safe_id]

        if display_name:
            project["display_name"] = _normalize_display_name(
                display_name, project["display_name"]
            )
        if description is not None:
            project["description"] = description
        if default_collection:
            project["default_collection"] = validate_collection_name(default_collection)
            # Ensure the new default collection exists
            self._ensure_collection_in_project(safe_id, project["default_collection"])
        if metadata:
            existing_meta = project.get("metadata", {})
            if isinstance(existing_meta, dict):
                existing_meta.update(metadata)
            project["metadata"] = existing_meta

        project["updated_at"] = _now_iso()
        self._persist()

        return self.get_project(safe_id)

    # -----------------------------------------------------------------------
    # Delete project
    # -----------------------------------------------------------------------

    def delete_project(
        self,
        project_id: str,
        delete_qdrant: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        """Delete a project from the registry.

        Safety:
        - Cannot delete 'default' unless force=True
        - Cannot delete if Qdrant has points unless force=True
        - delete_qdrant=False only removes registry entry
        """
        safe_id = validate_project_id(project_id)

        if safe_id == "default" and not force:
            raise PermissionError(
                "Cannot delete the default project. Use force=true to override."
            )

        if safe_id not in self._data.get("projects", {}):
            raise ValueError(f"Project not found: {safe_id}")

        # Check Qdrant points unless delete_qdrant
        if not delete_qdrant and not force:
            coll = project_collection_name(safe_id)
            info = _try_get_collection_info(coll)
            if info.get("points_count", 0) > 0:
                raise PermissionError(
                    f"Project has {info['points_count']} chunks in Qdrant. "
                    "Use force=true to delete regardless, or delete_qdrant=true to remove Qdrant data."
                )

        # Delete Qdrant collection if requested
        qdrant_deleted = False
        if delete_qdrant:
            coll = project_collection_name(safe_id)
            try:
                qdrant.delete_collection(coll)
                qdrant_deleted = True
            except Exception as exc:
                logger.warning(
                    "delete_qdrant_collection_failed project=%s error=%s", safe_id, exc
                )

        # Remove from registry
        del self._data["projects"][safe_id]
        self._persist()

        return {
            "ok": True,
            "deleted": True,
            "project_id": safe_id,
            "qdrant_deleted": qdrant_deleted,
        }

    # -----------------------------------------------------------------------
    # Collections CRUD
    # -----------------------------------------------------------------------

    def list_collections(self, project_id: str) -> list[dict[str, Any]]:
        """List logical collections for a project, merging registry + Qdrant."""
        safe_id = validate_project_id(project_id)

        if safe_id not in self._data.get("projects", {}):
            raise ValueError(f"Project not found: {safe_id}")

        project = self._data["projects"][safe_id]
        raw_collections = project.get("collections", {})
        registry_names: set[str] = set()

        results: list[dict[str, Any]] = []
        if isinstance(raw_collections, dict):
            for cname, cdata in raw_collections.items():
                if not isinstance(cdata, dict):
                    continue
                registry_names.add(cname)
                qdrant_chunk_count = _count_points_for_collection(safe_id, cname)
                results.append({
                    "name": cdata.get("name", cname),
                    "display_name": cdata.get(
                        "display_name", cname.replace("-", " ").title()
                    ),
                    "description": cdata.get("description", ""),
                    "created_at": cdata.get("created_at"),
                    "updated_at": cdata.get("updated_at"),
                    "metadata": cdata.get("metadata", {}),
                    "document_count": cdata.get("document_count"),
                    "chunk_count": qdrant_chunk_count if qdrant_chunk_count > 0 else cdata.get("chunk_count"),
                    "counts_are_estimated": qdrant_chunk_count > 0,
                })

        # Discover logical collections from Qdrant not in registry
        discovered = _discover_logical_collections(safe_id)
        for dname in discovered:
            if dname not in registry_names:
                qdrant_chunk_count = _count_points_for_collection(safe_id, dname)
                results.append({
                    "name": dname,
                    "display_name": dname.replace("-", " ").title(),
                    "description": "",
                    "created_at": None,
                    "updated_at": None,
                    "metadata": {},
                    "document_count": None,
                    "chunk_count": qdrant_chunk_count if qdrant_chunk_count > 0 else None,
                    "counts_are_estimated": qdrant_chunk_count > 0,
                    "discovered": True,
                })

        return sorted(results, key=lambda c: c["name"])

    def get_collection(
        self, project_id: str, collection_name: str
    ) -> dict[str, Any]:
        """Get a single logical collection for a project."""
        safe_id = validate_project_id(project_id)
        safe_coll = validate_collection_name(collection_name)

        if safe_id not in self._data.get("projects", {}):
            raise ValueError(f"Project not found: {safe_id}")

        project = self._data["projects"][safe_id]
        raw_collections = project.get("collections", {})

        if safe_coll in raw_collections and isinstance(raw_collections.get(safe_coll), dict):
            cdata = raw_collections[safe_coll]
            qdrant_chunk_count = _count_points_for_collection(safe_id, safe_coll)
            return {
                "ok": True,
                "project_id": safe_id,
                "collection": {
                    "name": cdata.get("name", safe_coll),
                    "display_name": cdata.get(
                        "display_name", safe_coll.replace("-", " ").title()
                    ),
                    "description": cdata.get("description", ""),
                    "created_at": cdata.get("created_at"),
                    "updated_at": cdata.get("updated_at"),
                    "metadata": cdata.get("metadata", {}),
                    "document_count": cdata.get("document_count"),
                    "chunk_count": qdrant_chunk_count if qdrant_chunk_count > 0 else cdata.get("chunk_count"),
                    "counts_are_estimated": qdrant_chunk_count > 0,
                },
                "qdrant": {
                    "chunk_count": qdrant_chunk_count,
                    "document_count": None,
                    "counts_are_estimated": qdrant_chunk_count > 0,
                    "error": None,
                },
            }

        # Check if discovered from Qdrant
        qdrant_chunk_count = _count_points_for_collection(safe_id, safe_coll)
        if qdrant_chunk_count > 0:
            return {
                "ok": True,
                "project_id": safe_id,
                "collection": {
                    "name": safe_coll,
                    "display_name": safe_coll.replace("-", " ").title(),
                    "description": "",
                    "created_at": None,
                    "updated_at": None,
                    "metadata": {},
                    "document_count": None,
                    "chunk_count": qdrant_chunk_count,
                    "counts_are_estimated": True,
                    "discovered": True,
                },
                "qdrant": {
                    "chunk_count": qdrant_chunk_count,
                    "document_count": None,
                    "counts_are_estimated": True,
                    "error": None,
                },
            }

        raise ValueError(f"Collection not found: {safe_coll}")

    def create_collection(
        self,
        project_id: str,
        collection_name: str,
        display_name: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Create a logical collection within a project."""
        safe_id = validate_project_id(project_id)
        safe_coll = validate_collection_name(collection_name)

        if safe_id not in self._data.get("projects", {}):
            raise ValueError(f"Project not found: {safe_id}")

        created = safe_coll not in self._data["projects"][safe_id].get("collections", {})
        cdata = self._ensure_collection_in_project(safe_id, safe_coll)

        if created:
            cdata["display_name"] = _normalize_display_name(
                display_name, safe_coll.replace("-", " ").title()
            )
            cdata["description"] = description or ""
            if metadata:
                cdata["metadata"] = metadata

        self._persist()

        return self.get_collection(safe_id, safe_coll)["collection"], created

    def update_collection(
        self,
        project_id: str,
        collection_name: str,
        display_name: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update a logical collection's metadata."""
        safe_id = validate_project_id(project_id)
        safe_coll = validate_collection_name(collection_name)

        if safe_id not in self._data.get("projects", {}):
            raise ValueError(f"Project not found: {safe_id}")

        project = self._data["projects"][safe_id]
        raw_collections = project.get("collections", {})

        if safe_coll not in raw_collections or not isinstance(
            raw_collections.get(safe_coll), dict
        ):
            raise ValueError(f"Collection not found: {safe_coll}")

        cdata = raw_collections[safe_coll]
        if display_name:
            cdata["display_name"] = _normalize_display_name(
                display_name, cdata.get("display_name", safe_coll)
            )
        if description is not None:
            cdata["description"] = description
        if metadata:
            existing_meta = cdata.get("metadata", {})
            if isinstance(existing_meta, dict):
                existing_meta.update(metadata)
            cdata["metadata"] = existing_meta
        cdata["updated_at"] = _now_iso()

        self._persist()

        return self.get_collection(safe_id, safe_coll)

    def delete_collection(
        self,
        project_id: str,
        collection_name: str,
        delete_chunks: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        """Delete a logical collection from the registry.

        Safety:
        - Cannot delete 'default' collection unless force=true
        - Cannot delete if chunks exist unless force=true
        - delete_chunks=False only removes registry entry
        """
        safe_id = validate_project_id(project_id)
        safe_coll = validate_collection_name(collection_name)

        if safe_coll == "default" and not force:
            raise PermissionError(
                "Cannot delete the default collection. Use force=true to override."
            )

        if safe_id not in self._data.get("projects", {}):
            raise ValueError(f"Project not found: {safe_id}")

        project = self._data["projects"][safe_id]
        raw_collections = project.get("collections", {})

        if safe_coll not in raw_collections or not isinstance(
            raw_collections.get(safe_coll), dict
        ):
            raise ValueError(f"Collection not found: {safe_coll}")

        # Check Qdrant points unless delete_chunks
        if not delete_chunks and not force:
            qdrant_chunk_count = _count_points_for_collection(safe_id, safe_coll)
            if qdrant_chunk_count > 0:
                raise PermissionError(
                    f"Collection has {qdrant_chunk_count} chunks in Qdrant. "
                    "Use force=true to delete regardless, or delete_chunks=true to remove Qdrant data."
                )

        # Delete Qdrant points if requested
        chunks_deleted = False
        if delete_chunks:
            from qdrant_client.models import FieldCondition, Filter, MatchValue

            coll = project_collection_name(safe_id)
            try:
                qdrant.delete(
                    collection_name=coll,
                    points_selector=Filter(
                        must=[
                            FieldCondition(
                                key="project_id",
                                match=MatchValue(value=safe_id),
                            ),
                            FieldCondition(
                                key="collection",
                                match=MatchValue(value=safe_coll),
                            ),
                        ]
                    ),
                )
                chunks_deleted = True
            except Exception as exc:
                logger.warning(
                    "delete_collection_qdrant_failed project=%s collection=%s error=%s",
                    safe_id,
                    safe_coll,
                    exc,
                )

        # Remove from registry
        del project["collections"][safe_coll]
        self._persist()

        return {
            "ok": True,
            "deleted": True,
            "project_id": safe_id,
            "collection": safe_coll,
            "chunks_deleted": chunks_deleted,
        }

    # -----------------------------------------------------------------------
    # Ensure helpers (for ingestion integration)
    # -----------------------------------------------------------------------

    def ensure_collection_exists(
        self, project_id: str, collection_name: str
    ) -> dict[str, Any]:
        """Ensure a logical collection exists, creating if needed."""
        safe_id = validate_project_id(project_id)
        safe_coll = validate_collection_name(collection_name)

        # Create project if it doesn't exist
        if safe_id not in self._data.get("projects", {}):
            self._ensure_project_in_data(safe_id)
            self._persist()

        return self._ensure_collection_in_project(safe_id, safe_coll)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_registry_instance: RagRegistry | None = None


def get_registry(path: Path | None = None) -> RagRegistry:
    """Get or create the singleton registry instance."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = RagRegistry(path)
    return _registry_instance


def reset_registry() -> None:
    """Reset the singleton (useful for testing)."""
    global _registry_instance
    _registry_instance = None
