from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI(title="Qonduit GitHub Webhook Receiver")
logger = logging.getLogger("qonduit.webhook_receiver")

GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
PROJECTS_ROOT = os.getenv("PROJECTS_ROOT", "/opt/projects").strip() or "/opt/projects"
GATEWAY_ENQUEUE_URL = (
    os.getenv("GATEWAY_ENQUEUE_URL", "http://127.0.0.1:8090/v1/ingestion/enqueue").strip()
    or "http://127.0.0.1:8090/v1/ingestion/enqueue"
)
SYNC_ENQUEUE_SCRIPT = (
    os.getenv("SYNC_ENQUEUE_SCRIPT", "")
    .strip()
    or str(Path(__file__).resolve().parents[1] / "scripts" / "sync_and_enqueue.sh")
)
REPO_PATH_OVERRIDES = os.getenv("GITHUB_REPO_PATH_OVERRIDES", "{}").strip() or "{}"
PROJECT_ID_OVERRIDES = os.getenv("GITHUB_PROJECT_ID_OVERRIDES", "{}").strip() or "{}"


def _safe_project_id(value: str | None, fallback: str = "default") -> str:
    raw = (value or "").strip().lower()
    safe = "".join(char for char in raw if char.isalnum() or char in ("-", "_"))
    return safe or fallback


def _env_json(raw: str) -> dict[str, str]:
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(key): str(value) for key, value in loaded.items()}


def _verify_signature(secret: str, payload: bytes, provided_signature: str) -> bool:
    if not secret:
        return False
    if not provided_signature.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(expected, provided_signature)


def _resolve_repo_path(repository_full_name: str, repository_name: str) -> str:
    overrides = _env_json(REPO_PATH_OVERRIDES)
    if repository_full_name in overrides:
        return overrides[repository_full_name]
    if repository_name in overrides:
        return overrides[repository_name]
    return str(Path(PROJECTS_ROOT) / repository_name)


def _resolve_project_id(repository_full_name: str, repository_name: str) -> str:
    overrides = _env_json(PROJECT_ID_OVERRIDES)
    if repository_full_name in overrides:
        return _safe_project_id(overrides[repository_full_name], repository_name)
    if repository_name in overrides:
        return _safe_project_id(overrides[repository_name], repository_name)
    return _safe_project_id(repository_name, repository_name)


def _branch_from_ref(ref: str) -> str:
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/") :]
    return ref.strip() or "main"


def _spawn_sync_enqueue(repo_path: str, branch: str, project_id: str) -> int:
    if not Path(SYNC_ENQUEUE_SCRIPT).exists():
        raise FileNotFoundError(f"sync script not found: {SYNC_ENQUEUE_SCRIPT}")
    process = subprocess.Popen(
        [
            SYNC_ENQUEUE_SCRIPT,
            "--repo-path",
            repo_path,
            "--branch",
            branch,
            "--project-id",
            project_id,
            "--gateway-url",
            GATEWAY_ENQUEUE_URL,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process.pid


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "qonduit-github-webhook"}


@app.post("/github/webhook")
async def github_webhook(
    request: Request,
    x_github_event: str = Header(default="", alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(default="", alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    payload_bytes = await request.body()
    if not _verify_signature(GITHUB_WEBHOOK_SECRET, payload_bytes, x_hub_signature_256):
        raise HTTPException(status_code=401, detail={"message": "Invalid webhook signature"})

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail={"message": "Invalid JSON payload"}) from error

    event = (x_github_event or "").strip().lower()
    if event != "push":
        return {
            "ok": True,
            "ignored": True,
            "reason": "unsupported_event",
            "event": event or "(missing)",
        }

    repository = payload.get("repository", {})
    if not isinstance(repository, dict):
        raise HTTPException(status_code=400, detail={"message": "Missing repository payload"})
    repository_name = str(repository.get("name", "")).strip()
    repository_full_name = str(repository.get("full_name", "")).strip()
    if not repository_name:
        raise HTTPException(status_code=400, detail={"message": "Missing repository.name"})

    ref = str(payload.get("ref", "")).strip()
    branch = _branch_from_ref(ref)
    repo_path = _resolve_repo_path(repository_full_name, repository_name)
    project_id = _resolve_project_id(repository_full_name, repository_name)

    try:
        pid = _spawn_sync_enqueue(repo_path, branch, project_id)
    except Exception as error:
        logger.exception(
            "webhook_sync_enqueue_spawn_failed repo=%s branch=%s project_id=%s error=%s",
            repository_full_name or repository_name,
            branch,
            project_id,
            str(error),
        )
        raise HTTPException(
            status_code=500,
            detail={"message": "Failed to launch sync-and-enqueue", "error": str(error)},
        ) from error

    logger.info(
        "webhook_push_accepted repo=%s branch=%s project_id=%s repo_path=%s pid=%s",
        repository_full_name or repository_name,
        branch,
        project_id,
        repo_path,
        pid,
    )
    return {
        "ok": True,
        "accepted": True,
        "event": "push",
        "repository": repository_full_name or repository_name,
        "project_id": project_id,
        "repo_path": repo_path,
        "branch": branch,
        "spawned_pid": pid,
    }
