from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qdrant_client.models import FieldCondition, Filter, MatchValue

from .rag import project_collection_name, qdrant, rag_service

DEFAULT_INCLUDE = [
    "*.py",
    "*.ts",
    "*.tsx",
    "*.js",
    "*.jsx",
    "*.java",
    "*.kt",
    "*.go",
    "*.rs",
    "*.c",
    "*.cpp",
    "*.h",
    "*.hpp",
    "*.dart",
    "*.md",
    "*.json",
    "*.yaml",
    "*.yml",
    "*.toml",
    "*.ini",
    "*.sh",
    "*.sql",
]

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    "node_modules",
    "build",
    "dist",
    "target",
    ".venv",
    "venv",
    "__pycache__",
}

POINT_ID_NAMESPACE = uuid.UUID("af66e5f9-14d0-44bb-9df0-9f6d567865da")


@dataclass
class IngestStats:
    scanned_files: int = 0
    ingested_files: int = 0
    ingested_chunks: int = 0
    deleted_chunks: int = 0


@dataclass
class IngestConfig:
    project_id: str
    repo_path: Path
    branch: str
    include_patterns: list[str]
    exclude_patterns: list[str]
    chunk_size: int
    chunk_overlap: int
    commit_sha: str



def _safe_id(value: str | None, fallback: str) -> str:
    raw = (value or "").strip().lower()
    safe = "".join(c for c in raw if c.isalnum() or c in ("-", "_"))
    return safe or fallback


def _resolve_commit_sha(repo_path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _resolve_branch(repo_path: Path, explicit_branch: str | None) -> str:
    if explicit_branch and explicit_branch.strip():
        return explicit_branch.strip()
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        value = result.stdout.strip()
        return value or "unknown"
    except Exception:
        return "unknown"


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _walk_files(config: IngestConfig) -> list[Path]:
    files: list[Path] = []
    for root, dir_names, file_names in os.walk(config.repo_path):
        rel_root = Path(root).relative_to(config.repo_path)

        kept_dirs = []
        for directory in dir_names:
            if directory in DEFAULT_EXCLUDE_DIRS:
                continue
            rel_dir = str((rel_root / directory).as_posix())
            if _matches_any(rel_dir, config.exclude_patterns):
                continue
            kept_dirs.append(directory)
        dir_names[:] = kept_dirs

        for filename in file_names:
            rel_file = (rel_root / filename).as_posix()
            if _matches_any(rel_file, config.exclude_patterns):
                continue
            if not _matches_any(rel_file, config.include_patterns):
                continue
            files.append(config.repo_path / rel_file)

    return sorted(files)


def _chunk_text(text: str, size: int, overlap: int) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + size, len(cleaned))
        block = cleaned[start:end].strip()
        if block:
            chunks.append(block)
        if end >= len(cleaned):
            break
        start = max(0, end - overlap)

    return chunks


def _point_id(
    project_id: str,
    branch: str,
    rel_path: str,
    chunk_index: int,
    repo_path: str,
) -> str:
    stable_key = "|".join(
        [
            _safe_id(project_id, "default"),
            branch.strip() or "unknown",
            rel_path.strip(),
            str(chunk_index),
            repo_path,
            "repo_ingest",
        ]
    )
    return str(uuid.uuid5(POINT_ID_NAMESPACE, stable_key))


def _load_text(file_path: Path) -> str:
    return file_path.read_text(encoding="utf-8", errors="ignore")


async def _delete_stale_chunks(
    *,
    config: IngestConfig,
    existing_rel_paths: set[str],
) -> int:
    collection_name = project_collection_name(config.project_id)
    try:
        hits, _ = qdrant.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="source",
                        match=MatchValue(value="repo_ingest"),
                    ),
                    FieldCondition(
                        key="project_id",
                        match=MatchValue(value=_safe_id(config.project_id, "default")),
                    ),
                    FieldCondition(
                        key="repo_path",
                        match=MatchValue(value=str(config.repo_path.resolve())),
                    ),
                    FieldCondition(
                        key="branch",
                        match=MatchValue(value=config.branch),
                    ),
                ]
            ),
            limit=100000,
            with_payload=True,
            with_vectors=False,
        )
    except Exception:
        return 0

    stale_ids = []
    for hit in hits:
        payload = hit.payload or {}
        rel_path = str(payload.get("file_path", ""))
        if rel_path and rel_path not in existing_rel_paths and hit.id is not None:
            stale_ids.append(hit.id)

    if stale_ids:
        qdrant.delete(collection_name=collection_name, points_selector=stale_ids)
    return len(stale_ids)


async def ingest_repository(config: IngestConfig) -> IngestStats:
    stats = IngestStats()
    files = _walk_files(config)
    stats.scanned_files = len(files)

    rel_paths_seen: set[str] = set()

    for path in files:
        rel_path = path.relative_to(config.repo_path).as_posix()
        rel_paths_seen.add(rel_path)
        text = _load_text(path)
        chunks = _chunk_text(text, config.chunk_size, config.chunk_overlap)
        if not chunks:
            continue

        stats.ingested_files += 1

        for index, chunk in enumerate(chunks):
            metadata = {
                "source": "repo_ingest",
                "project_id": _safe_id(config.project_id, "default"),
                "repo_path": str(config.repo_path.resolve()),
                "branch": config.branch,
                "file_path": rel_path,
                "chunk_index": index,
                "commit_sha": config.commit_sha,
            }
            await rag_service.add_document(
                project_id=config.project_id,
                text=chunk,
                metadata=metadata,
                point_id=_point_id(
                    config.project_id,
                    config.branch,
                    rel_path,
                    index,
                    str(config.repo_path.resolve()),
                ),
                namespace=config.branch,
                user_id="repo_ingest",
            )
            stats.ingested_chunks += 1

    stats.deleted_chunks = await _delete_stale_chunks(
        config=config,
        existing_rel_paths=rel_paths_seen,
    )
    return stats


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest a repo into project-scoped RAG")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--branch", default=None)
    parser.add_argument("--include", default=",".join(DEFAULT_INCLUDE))
    parser.add_argument("--exclude", default="")
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--chunk-overlap", type=int, default=200)
    return parser.parse_args()


async def _run_cli() -> int:
    args = _parse_args()
    repo_path = Path(args.repo_path).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise SystemExit(f"Repo path does not exist: {repo_path}")

    include_patterns = [p.strip() for p in args.include.split(",") if p.strip()]
    exclude_patterns = [p.strip() for p in args.exclude.split(",") if p.strip()]

    branch = _resolve_branch(repo_path, args.branch)
    commit_sha = _resolve_commit_sha(repo_path)

    config = IngestConfig(
        project_id=args.project_id,
        repo_path=repo_path,
        branch=branch,
        include_patterns=include_patterns or DEFAULT_INCLUDE,
        exclude_patterns=exclude_patterns,
        chunk_size=max(200, args.chunk_size),
        chunk_overlap=max(0, min(args.chunk_overlap, args.chunk_size // 2)),
        commit_sha=commit_sha,
    )

    stats = await ingest_repository(config)
    print(
        json.dumps(
            {
                "ok": True,
                "project_id": config.project_id,
                "repo_path": str(config.repo_path),
                "branch": config.branch,
                "commit_sha": config.commit_sha,
                "scanned_files": stats.scanned_files,
                "ingested_files": stats.ingested_files,
                "ingested_chunks": stats.ingested_chunks,
                "deleted_stale_chunks": stats.deleted_chunks,
            },
            indent=2,
        )
    )
    return 0


def main() -> int:
    import asyncio

    return asyncio.run(_run_cli())


if __name__ == "__main__":
    raise SystemExit(main())
