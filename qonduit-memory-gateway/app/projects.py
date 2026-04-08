from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DiscoveredProject:
    project_id: str
    repo_path: str
    branch: str


def _safe_id(value: str | None, fallback: str) -> str:
    raw = (value or "").strip().lower()
    safe = "".join(c for c in raw if c.isalnum() or c in ("-", "_"))
    return safe or fallback


def _resolve_branch(path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        branch = result.stdout.strip()
        return branch or "unknown"
    except Exception:
        return "unknown"


def discover_git_projects(projects_root: str) -> list[DiscoveredProject]:
    root = Path(projects_root).resolve()
    if not root.exists() or not root.is_dir():
        return []

    discovered: list[DiscoveredProject] = []
    for current_root, dirs, _files in os.walk(root):
        current_path = Path(current_root)
        if ".git" in dirs:
            project_id = _safe_id(current_path.name, "default")
            discovered.append(
                DiscoveredProject(
                    project_id=project_id,
                    repo_path=str(current_path),
                    branch=_resolve_branch(current_path),
                )
            )
            dirs[:] = []
            continue

        dirs[:] = [d for d in dirs if d not in {"node_modules", ".venv", "venv", "__pycache__"}]

    discovered.sort(key=lambda item: item.project_id)
    return discovered


def build_discovered_alias_map(
    projects_root: str,
    target_model: str,
) -> dict[str, dict[str, Any]]:
    aliases: dict[str, dict[str, Any]] = {}
    for project in discover_git_projects(projects_root):
        aliases[project.project_id] = {
            "model": target_model,
            "project_id": project.project_id,
            "default_mode": "coding",
            "rag_enabled": True,
            "source": "auto_discovered_repo",
            "repo_path": project.repo_path,
            "branch": project.branch,
        }
    return aliases


class ProjectAliasCache:
    def __init__(self) -> None:
        self._expires_at = 0.0
        self._aliases: dict[str, dict[str, Any]] = {}

    def get(
        self,
        *,
        projects_root: str,
        target_model: str,
        ttl_seconds: int,
        force_refresh: bool = False,
    ) -> dict[str, dict[str, Any]]:
        now = time.time()
        if force_refresh or now >= self._expires_at:
            self._aliases = build_discovered_alias_map(projects_root, target_model)
            self._expires_at = now + max(1, ttl_seconds)
        return dict(self._aliases)


project_alias_cache = ProjectAliasCache()
