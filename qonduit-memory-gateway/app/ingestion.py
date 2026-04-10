from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ingest_repo import (
    DEFAULT_EXCLUDE_PATTERNS,
    DEFAULT_INCLUDE,
    DEFAULT_MAX_FILE_BYTES,
    IngestConfig,
    ingest_repository_with_progress,
)
from .projects import discover_git_projects

INGESTION_STATUS_FILE = "ingestion_status.json"
INGESTION_QUEUE_FILE = "ingestion_queue.json"
INGESTION_LOG_FILE = "ingestion.log"
STATUS_STATES = {"idle", "queued", "running", "success", "failed"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_project_id(value: str | None, fallback: str = "default") -> str:
    raw = (value or "").strip().lower()
    safe = "".join(c for c in raw if c.isalnum() or c in ("-", "_"))
    return safe or fallback


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, path)


def _read_json_or_default(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default)
    if not isinstance(loaded, dict):
        return dict(default)
    return loaded


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
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _resolve_commit_sha(repo_path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


@dataclass(frozen=True)
class IngestionJob:
    project_id: str
    repo_path: str
    branch: str
    enqueued_at: str


class IngestionStore:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.status_path = self.data_dir / INGESTION_STATUS_FILE
        self.queue_path = self.data_dir / INGESTION_QUEUE_FILE
        self._lock = asyncio.Lock()

    def _default_status_entry(self, project_id: str) -> dict[str, Any]:
        return {
            "project_id": project_id,
            "state": "idle",
            "repo_path": "",
            "branch": "",
            "last_started_at": None,
            "last_finished_at": None,
            "last_error": None,
            "files_scanned": 0,
            "chunks_embedded": 0,
            "chunks_written": 0,
            "skipped_files": 0,
            "current_step": "idle",
            "current_file": None,
            "last_progress_at": None,
        }

    async def load_status(self) -> dict[str, Any]:
        async with self._lock:
            return _read_json_or_default(self.status_path, {"projects": {}})

    async def save_status(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            _atomic_write_json(self.status_path, payload)

    async def load_queue(self) -> dict[str, Any]:
        async with self._lock:
            return _read_json_or_default(self.queue_path, {"jobs": []})

    async def save_queue(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            _atomic_write_json(self.queue_path, payload)

    async def get_status(self, project_id: str) -> dict[str, Any]:
        safe_project = _safe_project_id(project_id)
        data = await self.load_status()
        projects = data.setdefault("projects", {})
        entry = projects.get(safe_project)
        if not isinstance(entry, dict):
            entry = self._default_status_entry(safe_project)
            projects[safe_project] = entry
            await self.save_status(data)
        return entry

    async def update_status(self, project_id: str, **updates: Any) -> dict[str, Any]:
        safe_project = _safe_project_id(project_id)
        data = await self.load_status()
        projects = data.setdefault("projects", {})
        current = projects.get(safe_project)
        if not isinstance(current, dict):
            current = self._default_status_entry(safe_project)
        progress_keys = {
            "files_scanned",
            "chunks_embedded",
            "chunks_written",
            "current_file",
            "current_step",
            "skipped_files",
        }
        if any(key in updates for key in progress_keys):
            if any(updates.get(key) != current.get(key) for key in progress_keys if key in updates):
                updates["last_progress_at"] = _utc_now()
        merged = {**current, **updates, "project_id": safe_project}
        state = str(merged.get("state", "idle")).lower()
        if state not in STATUS_STATES:
            merged["state"] = "idle"
        projects[safe_project] = merged
        await self.save_status(data)
        return merged

    async def has_queued_or_running(self, project_id: str) -> bool:
        safe_project = _safe_project_id(project_id)
        queue_data = await self.load_queue()
        jobs = queue_data.get("jobs", [])
        if isinstance(jobs, list):
            for item in jobs:
                if isinstance(item, dict) and _safe_project_id(
                    item.get("project_id"),
                ) == safe_project:
                    return True
        status = await self.get_status(safe_project)
        return status.get("state") in {"queued", "running"}

    async def enqueue(self, job: IngestionJob) -> dict[str, Any]:
        if await self.has_queued_or_running(job.project_id):
            status = await self.get_status(job.project_id)
            return {
                "ok": True,
                "enqueued": False,
                "reason": "already_queued_or_running",
                "status": status,
            }
        queue_data = await self.load_queue()
        jobs = queue_data.setdefault("jobs", [])
        if not isinstance(jobs, list):
            jobs = []
            queue_data["jobs"] = jobs
        jobs.append(asdict(job))
        await self.save_queue(queue_data)
        status = await self.update_status(
            job.project_id,
            state="queued",
            repo_path=job.repo_path,
            branch=job.branch,
            last_error=None,
            skipped_files=0,
            current_step="queued",
            current_file=None,
        )
        return {"ok": True, "enqueued": True, "reason": "queued", "status": status}

    async def pop_next_job(self) -> IngestionJob | None:
        queue_data = await self.load_queue()
        jobs = queue_data.get("jobs", [])
        if not isinstance(jobs, list) or not jobs:
            return None
        raw = jobs.pop(0)
        await self.save_queue(queue_data)
        if not isinstance(raw, dict):
            return None
        return IngestionJob(
            project_id=_safe_project_id(raw.get("project_id")),
            repo_path=str(raw.get("repo_path", "")).strip(),
            branch=str(raw.get("branch", "")).strip(),
            enqueued_at=str(raw.get("enqueued_at", "")).strip() or _utc_now(),
        )


class IngestionManager:
    def __init__(
        self,
        *,
        data_dir: str,
        projects_root: str,
        logger: logging.Logger,
        poll_seconds: float = 2.0,
        stall_timeout_seconds: int = 600,
        file_timeout_seconds: int = 120,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        self.store = IngestionStore(data_dir)
        self.projects_root = projects_root
        self.poll_seconds = max(0.5, poll_seconds)
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[Any] | None = None
        self.logger = logger
        self.stall_timeout_seconds = max(30, stall_timeout_seconds)
        self.file_timeout_seconds = max(1, file_timeout_seconds)
        self.max_file_bytes = max(1_000, max_file_bytes)

    async def start(self) -> None:
        await self.store.save_status(await self.store.load_status())
        await self.store.save_queue(await self.store.load_queue())
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._worker_loop())
            self.logger.info("ingestion_worker_started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
        self.logger.info("ingestion_worker_stopped")

    def _discover_project_defaults(self, project_id: str) -> tuple[str | None, str | None]:
        safe_project = _safe_project_id(project_id)
        for project in discover_git_projects(self.projects_root):
            if _safe_project_id(project.project_id) == safe_project:
                return project.repo_path, project.branch
        return None, None

    async def enqueue(
        self,
        *,
        project_id: str,
        repo_path: str | None,
        branch: str | None,
    ) -> dict[str, Any]:
        safe_project = _safe_project_id(project_id)
        discovered_repo, discovered_branch = self._discover_project_defaults(
            safe_project,
        )
        final_repo = (repo_path or "").strip() or discovered_repo
        final_branch = (branch or "").strip() or discovered_branch

        if not final_repo:
            return {
                "ok": False,
                "enqueued": False,
                "reason": "repo_path_required",
                "status": await self.store.get_status(safe_project),
            }
        repo = Path(final_repo).resolve()
        if not repo.exists() or not repo.is_dir():
            return {
                "ok": False,
                "enqueued": False,
                "reason": "repo_path_not_found",
                "status": await self.store.get_status(safe_project),
            }
        resolved_branch = final_branch or _resolve_branch(repo, None)
        job = IngestionJob(
            project_id=safe_project,
            repo_path=str(repo),
            branch=resolved_branch,
            enqueued_at=_utc_now(),
        )
        result = await self.store.enqueue(job)
        self.logger.info(
            "ingestion_enqueue project_id=%s enqueued=%s reason=%s repo_path=%s branch=%s",
            safe_project,
            result.get("enqueued"),
            result.get("reason"),
            str(repo),
            resolved_branch,
        )
        return result

    async def status_all(self) -> dict[str, Any]:
        statuses = await self.store.load_status()
        queue = await self.store.load_queue()
        jobs = queue.get("jobs", [])
        queue_projects = []
        if isinstance(jobs, list):
            queue_projects = [
                _safe_project_id(item.get("project_id"))
                for item in jobs
                if isinstance(item, dict)
            ]
        return {
            "ok": True,
            "projects": statuses.get("projects", {}),
            "queue_length": len(queue_projects),
            "queue_project_ids": queue_projects,
        }

    async def status_project(self, project_id: str) -> dict[str, Any]:
        status = await self.store.get_status(project_id)
        queue = await self.store.load_queue()
        jobs = queue.get("jobs", [])
        position = None
        if isinstance(jobs, list):
            for idx, item in enumerate(jobs):
                if isinstance(item, dict) and _safe_project_id(
                    item.get("project_id"),
                ) == _safe_project_id(project_id):
                    position = idx
                    break
        return {
            "ok": True,
            "project_id": _safe_project_id(project_id),
            "status": status,
            "queued_position": position,
        }

    async def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            job = await self.store.pop_next_job()
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            await self._run_job(job)

    async def _run_job(self, job: IngestionJob) -> None:
        started_at = _utc_now()
        await self.store.update_status(
            job.project_id,
            state="running",
            repo_path=job.repo_path,
            branch=job.branch,
            last_started_at=started_at,
            last_finished_at=None,
            last_error=None,
            files_scanned=0,
            chunks_embedded=0,
            chunks_written=0,
            skipped_files=0,
            current_step="scanning_repo",
            current_file=None,
            last_progress_at=_utc_now(),
        )
        self.logger.info(
            "ingestion_running project_id=%s repo_path=%s branch=%s",
            job.project_id,
            job.repo_path,
            job.branch,
        )

        try:
            repo_path = Path(job.repo_path).resolve()
            config = IngestConfig(
                project_id=job.project_id,
                repo_path=repo_path,
                branch=job.branch or _resolve_branch(repo_path, None),
                include_patterns=list(DEFAULT_INCLUDE),
                exclude_patterns=list(DEFAULT_EXCLUDE_PATTERNS),
                chunk_size=1200,
                chunk_overlap=200,
                commit_sha=_resolve_commit_sha(repo_path),
                max_file_bytes=self.max_file_bytes,
                file_timeout_seconds=self.file_timeout_seconds,
            )

            async def on_progress(progress: dict[str, Any]) -> None:
                await self.store.update_status(
                    job.project_id,
                    state="running",
                    repo_path=job.repo_path,
                    branch=config.branch,
                    files_scanned=int(progress.get("files_scanned", 0)),
                    chunks_embedded=int(progress.get("chunks_embedded", 0)),
                    chunks_written=int(progress.get("chunks_written", 0)),
                    skipped_files=int(progress.get("skipped_files", 0)),
                    current_step=str(progress.get("current_step", "running")),
                    current_file=progress.get("current_file"),
                )

            ingest_task = asyncio.create_task(
                ingest_repository_with_progress(
                    config,
                    progress_callback=on_progress,
                )
            )
            while True:
                try:
                    stats = await asyncio.wait_for(ingest_task, timeout=1.0)
                    break
                except asyncio.TimeoutError:
                    status = await self.store.get_status(job.project_id)
                    progress_at = str(status.get("last_progress_at") or "").strip()
                    if not progress_at:
                        continue
                    try:
                        progress_time = datetime.fromisoformat(progress_at)
                    except ValueError:
                        continue
                    elapsed = (
                        datetime.now(timezone.utc) - progress_time
                    ).total_seconds()
                    if elapsed <= self.stall_timeout_seconds:
                        continue
                    ingest_task.cancel()
                    error_message = (
                        "Ingestion stalled: no progress heartbeat for "
                        f"{int(elapsed)}s (timeout={self.stall_timeout_seconds}s)."
                    )
                    await self.store.update_status(
                        job.project_id,
                        state="failed",
                        repo_path=job.repo_path,
                        branch=job.branch,
                        last_finished_at=_utc_now(),
                        last_error=error_message,
                        current_step="failed",
                    )
                    self.logger.error(
                        "ingestion_stalled_timeout project_id=%s elapsed=%s timeout=%s",
                        job.project_id,
                        int(elapsed),
                        self.stall_timeout_seconds,
                    )
                    return

            finished_at = _utc_now()
            await self.store.update_status(
                job.project_id,
                state="success",
                repo_path=job.repo_path,
                branch=config.branch,
                last_finished_at=finished_at,
                last_error=None,
                files_scanned=stats.scanned_files,
                chunks_embedded=stats.ingested_chunks,
                chunks_written=stats.ingested_chunks,
                skipped_files=stats.skipped_files,
                current_step="complete",
                current_file=None,
            )
            self.logger.info(
                "ingestion_success project_id=%s files_scanned=%s chunks_written=%s",
                job.project_id,
                stats.scanned_files,
                stats.ingested_chunks,
            )
        except Exception as error:
            finished_at = _utc_now()
            await self.store.update_status(
                job.project_id,
                state="failed",
                repo_path=job.repo_path,
                branch=job.branch,
                last_finished_at=finished_at,
                last_error=str(error),
                current_step="failed",
                current_file=None,
            )
            self.logger.exception(
                "ingestion_failed project_id=%s error=%s",
                job.project_id,
                str(error),
            )

    async def force_fail_project(
        self,
        project_id: str,
        reason: str = "Manually failed by operator",
    ) -> dict[str, Any]:
        return await self.store.update_status(
            project_id,
            state="failed",
            current_step="failed",
            last_error=reason,
            last_finished_at=_utc_now(),
        )
