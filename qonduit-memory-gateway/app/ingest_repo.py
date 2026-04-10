from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import fnmatch
import inspect
import json
import logging
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Callable
from collections.abc import Awaitable

import httpx
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from .rag import EMBEDDING_BASE, EMBEDDING_MODEL, VECTOR_SIZE, project_collection_name, qdrant

logger = logging.getLogger("qonduit.memory_gateway")

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

# Exclude generated/minified/vendor-heavy paths by default to avoid wasting
# embedding budget and to prevent ingestion stalls on very large artifacts.
# Operators can override via --exclude when needed.
DEFAULT_EXCLUDE_PATTERNS = [
    "**/*.min.js",
    "**/*.min.css",
    "**/node_modules/**",
    "**/build/**",
    "**/dist/**",
    "**/.gradle/**",
    "**/.dart_tool/**",
    "**/coverage/**",
    "**/.git/**",
    "**/*.map",
    "**/vendor/**",
    "**/third_party/**",
]

DEFAULT_MAX_FILE_BYTES = 1_500_000

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
    skipped_files: int = 0


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
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    file_timeout_seconds: int = 120
    embed_timeout_seconds: int = 30
    qdrant_timeout_seconds: int = 20


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]



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
    posix_path = PurePosixPath(path)
    return any(
        fnmatch.fnmatch(path, pattern) or posix_path.match(pattern)
        for pattern in patterns
    )


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
            full_path = config.repo_path / rel_file
            try:
                size = full_path.stat().st_size
            except OSError:
                continue
            if size > config.max_file_bytes:
                logger.info(
                    "file_processing_skipped reason=max_file_bytes path=%s size=%s limit=%s",
                    rel_file,
                    size,
                    config.max_file_bytes,
                )
                continue
            files.append(full_path)

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


def _qdrant_call_with_timeout(func: Callable[..., Any], timeout_seconds: int, **kwargs: Any) -> Any:
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func, **kwargs)
    try:
        return future.result(timeout=max(1, timeout_seconds))
    except concurrent.futures.TimeoutError as error:
        raise TimeoutError(f"Qdrant call timed out after {timeout_seconds}s") from error
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _ensure_project_collection(project_id: str, timeout_seconds: int) -> str:
    collection_name = project_collection_name(project_id)
    try:
        _qdrant_call_with_timeout(
            qdrant.get_collection,
            timeout_seconds,
            collection_name=collection_name,
        )
    except Exception:
        _qdrant_call_with_timeout(
            qdrant.create_collection,
            timeout_seconds,
            collection_name=collection_name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
    return collection_name


def _embed_text_sync(text: str, timeout_seconds: int) -> list[float]:
    payload = {"input": text, "model": EMBEDDING_MODEL}
    with httpx.Client(timeout=max(1, timeout_seconds)) as client:
        response = client.post(f"{EMBEDDING_BASE.rstrip('/')}/v1/embeddings", json=payload)
        response.raise_for_status()
        data = response.json()
    items = data.get("data", []) if isinstance(data, dict) else []
    if not items:
        raise ValueError("Embedding backend returned empty data")
    vector = items[0].get("embedding")
    if not isinstance(vector, list):
        raise ValueError("Embedding backend returned invalid embedding payload")
    return [float(value) for value in vector]


async def _delete_stale_chunks(
    *,
    config: IngestConfig,
    existing_rel_paths: set[str],
) -> int:
    collection_name = project_collection_name(config.project_id)
    try:
        hits, _ = _qdrant_call_with_timeout(
            qdrant.scroll,
            config.qdrant_timeout_seconds,
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
        _qdrant_call_with_timeout(
            qdrant.delete,
            config.qdrant_timeout_seconds,
            collection_name=collection_name,
            points_selector=stale_ids,
        )
    return len(stale_ids)


async def ingest_repository(config: IngestConfig) -> IngestStats:
    return await ingest_repository_with_progress(config, progress_callback=None)


async def ingest_repository_with_progress(
    config: IngestConfig,
    progress_callback: ProgressCallback | None,
) -> IngestStats:
    stats = IngestStats()
    files = _walk_files(config)
    stats.scanned_files = len(files)
    if progress_callback is not None:
        result = progress_callback(
            {
                "current_step": "scanning_repo",
                "files_scanned": stats.scanned_files,
                "current_file": None,
            }
        )
        if inspect.isawaitable(result):
            await result

    rel_paths_seen: set[str] = set()

    async def _notify_progress(
        *,
        current_step: str,
        current_file: str | None,
    ) -> None:
        if progress_callback is None:
            return
        result = progress_callback(
            {
                "current_step": current_step,
                "files_scanned": stats.scanned_files,
                "current_file": current_file,
                "chunks_embedded": stats.ingested_chunks,
                "chunks_written": stats.ingested_chunks,
                "skipped_files": stats.skipped_files,
            }
        )
        if inspect.isawaitable(result):
            await result

    for path in files:
        rel_path = path.relative_to(config.repo_path).as_posix()
        rel_paths_seen.add(rel_path)
        logger.info("file_processing_started project_id=%s file=%s", config.project_id, rel_path)
        logger.info(
            "file_processing_timeout_wrapper_started project_id=%s file=%s timeout_seconds=%s",
            config.project_id,
            rel_path,
            config.file_timeout_seconds,
        )
        try:
            await _notify_progress(current_step="reading_file", current_file=rel_path)
            text = await asyncio.wait_for(
                asyncio.to_thread(_load_text, path),
                timeout=max(1, config.file_timeout_seconds),
            )
            await _notify_progress(current_step="chunking_file", current_file=rel_path)
            chunks = await asyncio.wait_for(
                asyncio.to_thread(_chunk_text, text, config.chunk_size, config.chunk_overlap),
                timeout=max(1, config.file_timeout_seconds),
            )
            if not chunks:
                continue

            collection_name = await asyncio.wait_for(
                asyncio.to_thread(
                    _ensure_project_collection,
                    config.project_id,
                    config.qdrant_timeout_seconds,
                ),
                timeout=max(1, config.file_timeout_seconds),
            )
            repo_path_value = str(config.repo_path.resolve())
            file_was_skipped = False
            for index, chunk in enumerate(chunks):
                logger.info(
                    "chunk_processing_started project_id=%s file=%s chunk_index=%s",
                    config.project_id,
                    rel_path,
                    index,
                )
                await _notify_progress(current_step="embedding_chunk", current_file=rel_path)
                try:
                    vector = await asyncio.wait_for(
                        asyncio.to_thread(
                            _embed_text_sync,
                            chunk,
                            config.embed_timeout_seconds,
                        ),
                        timeout=max(1, config.embed_timeout_seconds + 1),
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "chunk_processing_timed_out project_id=%s file=%s chunk_index=%s phase=embedding",
                        config.project_id,
                        rel_path,
                        index,
                    )
                    file_was_skipped = True
                    break
                except Exception as error:
                    logger.exception(
                        "chunk_processing_failed project_id=%s file=%s chunk_index=%s phase=embedding error=%s",
                        config.project_id,
                        rel_path,
                        index,
                        str(error),
                    )
                    file_was_skipped = True
                    break

                await _notify_progress(current_step="writing_chunk", current_file=rel_path)
                metadata = {
                    "source": "repo_ingest",
                    "project_id": _safe_id(config.project_id, "default"),
                    "repo_path": repo_path_value,
                    "branch": config.branch,
                    "file_path": rel_path,
                    "chunk_index": index,
                    "commit_sha": config.commit_sha,
                }
                point_id = _point_id(
                    config.project_id,
                    config.branch,
                    rel_path,
                    index,
                    repo_path_value,
                )
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            _qdrant_call_with_timeout,
                            qdrant.upsert,
                            config.qdrant_timeout_seconds,
                            collection_name=collection_name,
                            points=[
                                PointStruct(
                                    id=point_id,
                                    vector=vector,
                                    payload={"text": chunk, **metadata},
                                )
                            ],
                        ),
                        timeout=max(1, config.qdrant_timeout_seconds + 1),
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "chunk_processing_timed_out project_id=%s file=%s chunk_index=%s phase=upsert",
                        config.project_id,
                        rel_path,
                        index,
                    )
                    file_was_skipped = True
                    break
                except Exception as error:
                    logger.exception(
                        "chunk_processing_failed project_id=%s file=%s chunk_index=%s phase=upsert error=%s",
                        config.project_id,
                        rel_path,
                        index,
                        str(error),
                    )
                    file_was_skipped = True
                    break

                stats.ingested_chunks += 1
                await _notify_progress(current_step="writing_chunk", current_file=rel_path)

            if file_was_skipped:
                stats.skipped_files += 1
                logger.info(
                    "file_processing_skipped project_id=%s file=%s reason=chunk_failure_or_timeout",
                    config.project_id,
                    rel_path,
                )
                await _notify_progress(current_step="reading_file", current_file=rel_path)
                logger.info(
                    "status_updated_skipped_file project_id=%s file=%s skipped_files=%s",
                    config.project_id,
                    rel_path,
                    stats.skipped_files,
                )
                continue

            stats.ingested_files += 1
        except asyncio.TimeoutError:
            stats.skipped_files += 1
            logger.warning(
                "file_processing_timed_out project_id=%s file=%s timeout_seconds=%s",
                config.project_id,
                rel_path,
                config.file_timeout_seconds,
            )
            logger.info(
                "file_processing_skipped project_id=%s file=%s reason=timeout",
                config.project_id,
                rel_path,
            )
            await _notify_progress(current_step="reading_file", current_file=rel_path)
            logger.info(
                "status_updated_skipped_file project_id=%s file=%s skipped_files=%s",
                config.project_id,
                rel_path,
                stats.skipped_files,
            )
            continue
        except Exception as error:
            stats.skipped_files += 1
            logger.exception(
                "file_processing_exception project_id=%s file=%s error=%s",
                config.project_id,
                rel_path,
                str(error),
            )
            logger.info(
                "file_processing_skipped project_id=%s file=%s reason=exception",
                config.project_id,
                rel_path,
            )
            await _notify_progress(current_step="reading_file", current_file=rel_path)
            logger.info(
                "status_updated_skipped_file project_id=%s file=%s skipped_files=%s",
                config.project_id,
                rel_path,
                stats.skipped_files,
            )
            continue

    if progress_callback is not None:
        result = progress_callback(
            {
                "current_step": "deleting_stale_chunks",
                "files_scanned": stats.scanned_files,
                "current_file": None,
                "chunks_embedded": stats.ingested_chunks,
                "chunks_written": stats.ingested_chunks,
                "skipped_files": stats.skipped_files,
            }
        )
        if inspect.isawaitable(result):
            await result
    stats.deleted_chunks = await _delete_stale_chunks(
        config=config,
        existing_rel_paths=rel_paths_seen,
    )
    if progress_callback is not None:
        result = progress_callback(
            {
                "current_step": "complete",
                "files_scanned": stats.scanned_files,
                "current_file": None,
                "chunks_embedded": stats.ingested_chunks,
                "chunks_written": stats.ingested_chunks,
                "skipped_files": stats.skipped_files,
            }
        )
        if inspect.isawaitable(result):
            await result
    return stats


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest a repo into project-scoped RAG")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--branch", default=None)
    parser.add_argument("--include", default=",".join(DEFAULT_INCLUDE))
    parser.add_argument("--exclude", default=",".join(DEFAULT_EXCLUDE_PATTERNS))
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--chunk-overlap", type=int, default=200)
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES)
    parser.add_argument("--file-timeout-seconds", type=int, default=120)
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
        exclude_patterns=exclude_patterns or DEFAULT_EXCLUDE_PATTERNS,
        chunk_size=max(200, args.chunk_size),
        chunk_overlap=max(0, min(args.chunk_overlap, args.chunk_size // 2)),
        commit_sha=commit_sha,
        max_file_bytes=max(1_000, args.max_file_bytes),
        file_timeout_seconds=max(1, args.file_timeout_seconds),
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
                "skipped_files": stats.skipped_files,
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
