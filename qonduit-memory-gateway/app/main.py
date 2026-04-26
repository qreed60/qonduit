from __future__ import annotations

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import httpx
import json
import ast
import csv
import os
import logging
import time
import asyncio
from pathlib import Path
import re
import uuid
from typing import Any, Literal
from hashlib import sha256

from pypdf import PdfReader
from docx import Document
from openpyxl import load_workbook

from .budget import build_budget, estimate_tokens, trim_recent_messages
from .perf_logs import PerfTracker
from .store import DEFAULT_PROJECT_ID, load_conversation, save_conversation
from .summarizer import summarize_messages
from .rag import (
    ensure_collection,
    add_document,
    search_documents,
    list_collections,
    create_collection_marker,
    create_embeddings_response,
    qdrant,
    COLLECTION_NAME,
    EMBEDDING_MODEL,
    RAG_ENABLED,
    RAG_TOP_K,
)
from .projects import project_alias_cache
from .ingestion import IngestionManager
from qdrant_client.models import Filter, FieldCondition, MatchValue
import glob
import shutil

app = FastAPI(title="Qonduit Memory Gateway")
logger = logging.getLogger("qonduit.memory_gateway")
ingestion_logger = logging.getLogger("qonduit.memory_gateway.ingestion")


class ToolResult(BaseModel):
    """Result from executing a tool."""
    tool_call_id: str
    name: str
    content: str
    is_error: bool = False


async def execute_retrieve_project_context(
    project_id: str,
    query: str,
    user_id: str | None = None,
    top_k: int = 4,
) -> str:
    """Retrieve relevant context from project RAG."""
    try:
        results = await search_documents(
            query=query,
            limit=top_k,
            collection=None,
            user_id=user_id,
            project_id=project_id,
        )
        if not results:
            return "No relevant context found for this query."
        
        chunks = []
        for i, r in enumerate(results, 1):
            text = r.get("text", "")
            score = r.get("score", 0)
            chunks.append(f"[{i}] (score: {score:.3f})\\n{text}")
        
        return "\\n\\n".join(chunks)
    except Exception as e:
        logger.exception("execute_retrieve_project_context_failed")
        return f"Error retrieving context: {str(e)}"


async def execute_search_project_files(
    project_id: str,
    pattern: str,
    max_results: int = 20,
) -> str:
    """Search for files matching a pattern in the project."""
    try:
        project_root = resolve_project_root(project_id)
        if not project_root.is_dir():
            return (
                f"Project directory not found for "
                f"'{sanitize_identifier(project_id, 'default')}'."
            )

        matches = []
        search_pattern = str(project_root / "**" / pattern)
        for filepath in glob.glob(search_pattern, recursive=True)[:max_results]:
            rel_path = os.path.relpath(filepath, project_root)
            matches.append(rel_path)

        if not matches:
            return (
                f"No files matching '{pattern}' found in project "
                f"'{sanitize_identifier(project_id, 'default')}'."
            )

        return "Found files:\\n" + "\\n".join(f"- {m}" for m in matches)
    except Exception as e:
        logger.exception("execute_search_project_files_failed")
        return f"Error searching files: {str(e)}"


async def execute_list_project_files(
    project_id: str,
    directory: str | None = None,
    extensions: list[str] | None = None,
    max_results: int = 50,
) -> str:
    """List files in a project directory."""
    try:
        target_dir = resolve_project_relative_directory(project_id, directory)
        if target_dir is None:
            return (
                "Blocked path escape attempt. Directory must stay inside "
                "the project root."
            )
        if not target_dir.is_dir():
            safe_project = sanitize_identifier(project_id, "default")
            return f"Directory not found: {directory or safe_project}"

        files = []
        for root, dirs, filenames in os.walk(target_dir):
            # Skip hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.')]

            for fname in filenames:
                if fname.startswith('.'):
                    continue
                if extensions:
                    _, ext = os.path.splitext(fname)
                    if ext.lower() not in [e.lower() for e in extensions]:
                        continue

                rel_path = os.path.relpath(
                    os.path.join(root, fname), target_dir
                )
                files.append(rel_path)

                if len(files) >= max_results:
                    break

            if len(files) >= max_results:
                break

        if not files:
            suffix = f" in {directory}" if directory else ""
            return f"No files found{suffix}."

        return f"Files{f' in {directory}' if directory else ''}:\\n" + "\\n".join(f"- {f}" for f in files[:max_results])
    except Exception as e:
        logger.exception("execute_list_project_files_failed")
        return f"Error listing files: {str(e)}"


async def execute_read_file(
    project_id: str,
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> str:
    """Read exact file content from a project with optional line range."""
    file_path = resolve_project_relative_file(project_id, path)
    if file_path is None:
        return "Blocked path escape attempt. Path must stay inside project root."
    if not file_path.is_file():
        return f"File not found: {path}"

    if start_line < 1:
        start_line = 1
    if end_line is not None and end_line < start_line:
        return "Invalid line range: end_line must be >= start_line."

    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        last_line = len(lines) if end_line is None else min(end_line, len(lines))
        selected = lines[start_line - 1:last_line]
        body = "".join(selected)
        return (
            f"File: {path}\n"
            f"Line range: {start_line}-{last_line}\n\n"
            f"{body}"
        )
    except Exception as e:
        logger.exception("execute_read_file_failed")
        return f"Error reading file: {str(e)}"


async def execute_get_project_file(
    project_id: str,
    path: str,
    max_bytes: int = 120_000,
) -> str:
    """Get file content and metadata for a project file."""
    file_path = resolve_project_relative_file(project_id, path)
    if file_path is None:
        return "Blocked path escape attempt. Path must stay inside project root."
    if not file_path.is_file():
        return f"File not found: {path}"

    max_bytes = max(2048, min(max_bytes, 500_000))
    try:
        with open(file_path, "rb") as f:
            raw = f.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        text = raw[:max_bytes].decode("utf-8", errors="ignore")
        size_bytes = file_path.stat().st_size
        return (
            f"File: {path}\n"
            f"Size bytes: {size_bytes}\n"
            f"Truncated: {'yes' if truncated else 'no'}\n\n"
            f"{text}"
        )
    except Exception as e:
        logger.exception("execute_get_project_file_failed")
        return f"Error reading file: {str(e)}"


async def execute_detect_project_type(project_id: str) -> str:
    """Detect project type using confidence-ranked repo markers."""
    project_root = resolve_project_root(project_id)
    if not project_root.is_dir():
        return json.dumps(
            {
                "project_id": sanitize_identifier(project_id, "default"),
                "error": "project_not_found",
            },
            indent=2,
        )

    evidence = collect_project_markers(project_root)
    ranking = rank_project_types(evidence)
    best = ranking[0] if ranking else {"type": "unknown", "score": 0.0}

    return json.dumps(
        {
            "project_id": sanitize_identifier(project_id, "default"),
            "project_root": str(project_root),
            "primary_type": best["type"],
            "confidence": round(float(best["score"]), 3),
            "ranked_types": ranking,
            "recommendation": (
                "Call get_project_entry_points next, then read_file on top "
                "entry-point files before making framework assumptions."
            ),
        },
        indent=2,
    )


async def execute_get_project_entry_points(
    project_id: str,
    max_results: int = 12,
) -> str:
    """Discover likely startup files for the current project."""
    project_root = resolve_project_root(project_id)
    if not project_root.is_dir():
        return json.dumps(
            {
                "project_id": sanitize_identifier(project_id, "default"),
                "error": "project_not_found",
            },
            indent=2,
        )

    evidence = collect_project_markers(project_root)
    ranking = rank_project_types(evidence)
    primary = ranking[0]["type"] if ranking else "unknown"
    entry_points = discover_entry_points(project_root, primary)
    top_entries = entry_points[:max(1, min(max_results, 50))]

    return json.dumps(
        {
            "project_id": sanitize_identifier(project_id, "default"),
            "detected_type": primary,
            "entry_points": top_entries,
            "next_step": (
                "Use read_file with exact paths above before proposing edits "
                "or assuming a framework layout."
            ),
        },
        indent=2,
    )


MAX_EXECUTION_OUTPUT_BYTES = 120_000
MAX_LOG_BYTES = 40_000
MAX_LOG_LINES = 300


def project_execution_log_dir(project_id: str) -> Path:
    safe_project = sanitize_identifier(project_id, "default")
    base = Path(GATEWAY_DATA_DIR) / "execution_logs" / safe_project
    base.mkdir(parents=True, exist_ok=True)
    return base


def ensure_writable_file_path(project_id: str, path: str) -> tuple[Path | None, str | None]:
    raw = (path or "").strip()
    if not raw:
        return None, "invalid_path"
    if raw.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", raw):
        return None, "absolute_path_blocked"

    target = resolve_project_relative_file(project_id, raw)
    if target is None:
        return None, "path_escape_blocked"

    project_root = resolve_project_root(project_id)
    parent = target.parent
    if not _is_within_dir(parent.resolve(), project_root):
        return None, "path_escape_blocked"

    for candidate in [parent, *parent.parents]:
        if candidate == project_root.parent:
            break
        if candidate.exists() and candidate.is_symlink():
            return None, "symlink_escape_blocked"
        if candidate == project_root:
            break

    if target.exists() and target.is_symlink():
        return None, "symlink_escape_blocked"

    return target, None


def parse_controlled_patch_operations(patch: str) -> list[dict[str, str]]:
    try:
        payload = json.loads(patch)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid_json: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("invalid_patch_payload: expected object")

    if payload.get("format") != "qonduit.patch/v1":
        raise ValueError("unsupported_patch_format")

    operations = payload.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("invalid_operations: expected non-empty list")

    parsed: list[dict[str, str]] = []
    for op in operations:
        if not isinstance(op, dict):
            raise ValueError("invalid_operation: expected object")

        action = op.get("action")
        path = op.get("path")
        content = op.get("content")
        if action not in {"write", "create"}:
            raise ValueError("invalid_action: only write/create allowed")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("invalid_path")
        if not isinstance(content, str):
            raise ValueError("invalid_content")

        parsed.append({
            "action": action,
            "path": path,
            "content": content,
        })

    return parsed


async def execute_apply_patch(project_id: str, patch: str) -> str:
    """Apply controlled patch operations inside the active project only."""
    safe_project = sanitize_identifier(project_id, "default")

    try:
        operations = parse_controlled_patch_operations(patch)
    except ValueError as exc:
        return json.dumps(
            {
                "ok": False,
                "status": "error",
                "applied": False,
                "project_id": safe_project,
                "error": "invalid_patch",
                "detail": str(exc),
                "files_changed": [],
                "affected_files": [],
                "summary": "Patch payload was invalid and was not applied.",
            },
            indent=2,
        )

    affected_files: list[str] = []
    errors: list[dict[str, str]] = []

    for op in operations:
        path = op["path"]
        target, error_code = ensure_writable_file_path(project_id, path)
        if target is None:
            errors.append({"path": path, "error": error_code or "invalid_path"})
            continue

        rel_path = str(target.relative_to(resolve_project_root(project_id)))
        is_create = op["action"] == "create"
        if is_create and target.exists():
            errors.append({"path": rel_path, "error": "file_already_exists"})
            continue

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(op["content"], encoding="utf-8")
            affected_files.append(rel_path)
        except Exception as exc:
            errors.append({"path": rel_path, "error": f"write_failed: {exc}"})

    ok = len(errors) == 0
    return json.dumps(
        {
            "ok": ok,
            "status": "success" if ok else "error",
            "applied": ok,
            "project_id": safe_project,
            "error": None if ok else "patch_apply_failed",
            "files_changed": affected_files,
            "affected_files": affected_files,
            "errors": errors,
            "applied_count": len(affected_files),
            "summary": (
                f"Applied {len(affected_files)} patch operation(s)."
                if ok
                else "Patch application failed for one or more files."
            ),
        },
        indent=2,
    )


def build_allowed_command(project_id: str, operation: str) -> tuple[list[str] | None, str]:
    project_root = resolve_project_root(project_id)
    if not project_root.is_dir():
        return None, "project_not_found"

    evidence = collect_project_markers(project_root)

    if (project_root / ".qonduit_safe_mock").is_file():
        if operation == "build":
            return ["python", "-c", "print('safe_mock_build_ok')"], "safe_mock"
        return ["python", "-c", "print('safe_mock_tests_ok')"], "safe_mock"

    ranking = rank_project_types(evidence)
    primary = ranking[0]["type"] if ranking else "unknown"

    if primary == "flutter":
        if shutil.which("flutter") is None:
            return None, "flutter_unavailable"
        if operation == "build":
            return ["flutter", "build", "apk", "--debug"], "flutter"
        return ["flutter", "test", "--reporter", "expanded"], "flutter"

    if primary == "android_kotlin":
        gradlew = project_root / "android" / "gradlew"
        gradlew_bat = project_root / "android" / "gradlew.bat"
        if gradlew.is_file():
            cmd = [str(gradlew)]
        elif gradlew_bat.is_file():
            cmd = [str(gradlew_bat)]
        else:
            return None, "gradlew_missing"
        task = "assembleDebug" if operation == "build" else "test"
        return cmd + [task, "--no-daemon"], "android_kotlin"

    if primary == "web":
        if shutil.which("npm") is None:
            return None, "npm_unavailable"
        npm_task = "build" if operation == "build" else "test"
        return ["npm", "run", npm_task], "web"

    return None, "unsupported_project_type"


async def run_allowed_project_command(
    project_id: str,
    operation: str,
    timeout_seconds: int,
) -> str:
    safe_project = sanitize_identifier(project_id, "default")
    command, detected_type = build_allowed_command(project_id, operation)
    if command is None:
        return json.dumps(
            {
                "ok": False,
                "status": "error",
                "applied": False,
                "project_id": safe_project,
                "operation": operation,
                "error": detected_type,
                "files_changed": [],
                "affected_files": [],
                "summary": "No allowlisted command available for this project type.",
            },
            indent=2,
        )

    timeout_seconds = max(10, min(timeout_seconds, 1200))
    project_root = resolve_project_root(project_id)
    log_dir = project_execution_log_dir(project_id)
    log_path = log_dir / f"{operation}.log"

    started = time.time()
    output_text = ""
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(project_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
        output_text = (stdout or b"").decode("utf-8", errors="ignore")
        exit_code = int(process.returncode or 0)
    except asyncio.TimeoutError:
        return json.dumps(
            {
                "ok": False,
                "status": "error",
                "applied": False,
                "project_id": safe_project,
                "project_type": detected_type,
                "operation": operation,
                "command": command,
                "error": "timeout",
                "timeout_seconds": timeout_seconds,
                "files_changed": [],
                "affected_files": [],
                "summary": f"{operation} timed out before completion.",
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "status": "error",
                "applied": False,
                "project_id": safe_project,
                "project_type": detected_type,
                "operation": operation,
                "command": command,
                "error": f"execution_failed: {exc}",
                "files_changed": [],
                "affected_files": [],
                "summary": f"{operation} command execution failed.",
            },
            indent=2,
        )

    output_text = output_text[:MAX_EXECUTION_OUTPUT_BYTES]
    duration_ms = int((time.time() - started) * 1000)
    log_path.write_text(output_text, encoding="utf-8")

    ok = exit_code == 0
    return json.dumps(
        {
            "ok": ok,
            "status": "success" if ok else "error",
            "applied": ok,
            "project_id": safe_project,
            "project_type": detected_type,
            "operation": operation,
            "command": command,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "log_source": str(log_path.relative_to(Path(GATEWAY_DATA_DIR))),
            "output_preview": output_text[-4000:],
            "files_changed": [str(log_path.relative_to(log_dir.parent))],
            "affected_files": [str(log_path.relative_to(log_dir.parent))],
            "summary": (
                f"{operation} completed successfully."
                if ok
                else f"{operation} failed with exit code {exit_code}."
            ),
        },
        indent=2,
    )


async def execute_run_build(
    project_id: str,
    timeout_seconds: int = 600,
) -> str:
    """Run allowlisted build command for the detected project type."""
    return await run_allowed_project_command(project_id, "build", timeout_seconds)


async def execute_run_tests(
    project_id: str,
    timeout_seconds: int = 600,
) -> str:
    """Run allowlisted test command for the detected project type."""
    return await run_allowed_project_command(project_id, "tests", timeout_seconds)


async def execute_tail_logs(
    project_id: str,
    source: str = "build",
    max_lines: int = 120,
) -> str:
    """Tail bounded project execution logs from known sources only."""
    safe_project = sanitize_identifier(project_id, "default")
    allowed_sources = {"build", "tests"}
    if source not in allowed_sources:
        return json.dumps(
            {
                "ok": False,
                "status": "error",
                "applied": False,
                "project_id": safe_project,
                "error": "invalid_source",
                "allowed_sources": sorted(allowed_sources),
                "files_changed": [],
                "affected_files": [],
                "summary": "Requested log source is not allowlisted.",
            },
            indent=2,
        )

    log_path = project_execution_log_dir(project_id) / f"{source}.log"
    if not log_path.is_file():
        return json.dumps(
            {
                "ok": False,
                "status": "error",
                "applied": False,
                "project_id": safe_project,
                "error": "log_not_found",
                "source": source,
                "files_changed": [],
                "affected_files": [],
                "summary": "Requested log file does not exist yet.",
            },
            indent=2,
        )

    bounded_lines = max(1, min(max_lines, MAX_LOG_LINES))
    raw = log_path.read_bytes()[-MAX_LOG_BYTES:]
    text = raw.decode("utf-8", errors="ignore")
    lines = text.splitlines()
    tail = "\n".join(lines[-bounded_lines:])

    return json.dumps(
        {
            "ok": True,
            "status": "success",
            "applied": True,
            "project_id": safe_project,
            "source": source,
            "line_count": len(tail.splitlines()),
            "truncated_bytes": len(raw),
            "files_changed": [
                str(log_path.relative_to(Path(GATEWAY_DATA_DIR)))
            ],
            "affected_files": [
                str(log_path.relative_to(Path(GATEWAY_DATA_DIR)))
            ],
            "summary": f"Returned up to {bounded_lines} log line(s).",
            "content": tail,
        },
        indent=2,
    )


TOOL_HANDLERS = {
    "retrieve_project_context": execute_retrieve_project_context,
    "search_project_files": execute_search_project_files,
    "list_project_files": execute_list_project_files,
    "read_file": execute_read_file,
    "get_project_file": execute_get_project_file,
    "detect_project_type": execute_detect_project_type,
    "get_project_entry_points": execute_get_project_entry_points,
    "apply_patch": execute_apply_patch,
    "run_build": execute_run_build,
    "run_tests": execute_run_tests,
    "tail_logs": execute_tail_logs,
}


async def execute_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    project_id: str,
    user_id: str | None,
) -> ToolResult:
    """Execute a tool and return the result."""
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        return ToolResult(
            tool_call_id=tool_args.get("_tool_call_id", "unknown"),
            name=tool_name,
            content=json.dumps(
                {
                    "ok": False,
                    "error": "unknown_tool",
                    "tool": tool_name,
                    "available_tools": sorted(list(TOOL_HANDLERS.keys())),
                    "hint": (
                        "Call detect_project_type, then "
                        "get_project_entry_points, then read_file."
                    ),
                },
                indent=2,
            ),
            is_error=True,
        )
    
    try:
        # Map tool arguments to handler parameters
        if tool_name == "retrieve_project_context":
            result = await handler(
                project_id=project_id,
                query=tool_args.get("query", ""),
                user_id=user_id,
                top_k=tool_args.get("top_k", 4),
            )
        elif tool_name == "search_project_files":
            result = await handler(
                project_id=project_id,
                pattern=tool_args.get("pattern", "*"),
                max_results=tool_args.get("max_results", 20),
            )
        elif tool_name == "list_project_files":
            result = await handler(
                project_id=project_id,
                directory=tool_args.get("directory"),
                extensions=tool_args.get("extensions"),
                max_results=tool_args.get("max_results", 50),
            )
        elif tool_name == "read_file":
            result = await handler(
                project_id=project_id,
                path=tool_args.get("path", ""),
                start_line=tool_args.get("start_line", 1),
                end_line=tool_args.get("end_line"),
            )
        elif tool_name == "get_project_file":
            result = await handler(
                project_id=project_id,
                path=tool_args.get("path", ""),
                max_bytes=tool_args.get("max_bytes", 120_000),
            )
        elif tool_name == "detect_project_type":
            result = await handler(project_id=project_id)
        elif tool_name == "get_project_entry_points":
            result = await handler(
                project_id=project_id,
                max_results=tool_args.get("max_results", 12),
            )
        elif tool_name == "apply_patch":
            result = await handler(
                project_id=project_id,
                patch=tool_args.get("patch", ""),
            )
        elif tool_name == "run_build":
            result = await handler(
                project_id=project_id,
                timeout_seconds=tool_args.get("timeout_seconds", 600),
            )
        elif tool_name == "run_tests":
            result = await handler(
                project_id=project_id,
                timeout_seconds=tool_args.get("timeout_seconds", 600),
            )
        elif tool_name == "tail_logs":
            result = await handler(
                project_id=project_id,
                source=tool_args.get("source", "build"),
                max_lines=tool_args.get("max_lines", 120),
            )
        else:
            result = f"Unknown tool: {tool_name}"
        
        return ToolResult(
            tool_call_id=tool_args.get("_tool_call_id", "unknown"),
            name=tool_name,
            content=result,
            is_error=False,
        )
    except Exception as e:
        logger.exception("execute_tool_failed tool=%s", tool_name)
        return ToolResult(
            tool_call_id=tool_args.get("_tool_call_id", "unknown"),
            name=tool_name,
            content=json.dumps(
                {
                    "ok": False,
                    "error": "tool_execution_failed",
                    "tool": tool_name,
                    "detail": str(e),
                },
                indent=2,
            ),
            is_error=True,
        )


@app.on_event("startup")
async def startup() -> None:
    ensure_collection()
    await ingestion_manager.start()
    logger.info(
        "gateway_startup llama_base=%s default_context_size=%s default_mode=%s "
        "gateway_data_dir=%s default_project=%s",
        LLAMA_BASE,
        DEFAULT_CONTEXT_SIZE,
        DEFAULT_MODE,
        GATEWAY_DATA_DIR,
        DEFAULT_PROJECT_NAMESPACE,
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    await ingestion_manager.stop()


def env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    cleaned = value.strip()
    return cleaned or default


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "invalid_int_env name=%s raw=%s fallback=%s",
            name,
            raw,
            default,
        )
        return default
    return value


def env_json(name: str, default: dict[str, Any]) -> dict[str, Any]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("invalid_json_env name=%s raw=%s", name, raw[:200])
        return default
    if not isinstance(parsed, dict):
        logger.warning("invalid_json_env_type name=%s expected=dict", name)
        return default
    return parsed


LLAMA_BASE = env_str("LLAMA_BASE", "http://192.168.5.5:8080")
DEFAULT_CONTEXT_SIZE = max(env_int("DEFAULT_CONTEXT_SIZE", 65536), 1024)
DEFAULT_MODE = env_str("DEFAULT_MODE", "chat")
GATEWAY_DATA_DIR = env_str("GATEWAY_DATA_DIR", "/app/data")
DEFAULT_PROJECT_NAMESPACE = env_str("DEFAULT_PROJECT_ID", DEFAULT_PROJECT_ID)
PROJECT_DEFAULT_MODE = env_str("PROJECT_DEFAULT_MODE", DEFAULT_MODE)
UPSTREAM_CONNECT_TIMEOUT_SECONDS = 10.0
UPSTREAM_WRITE_TIMEOUT_SECONDS = 60.0
UPSTREAM_POOL_TIMEOUT_SECONDS = 60.0
STREAM_KEEPALIVE_INTERVAL_SECONDS = 2.0
MODEL_ALIAS_CONFIG = env_json("MODEL_ALIAS_CONFIG", {})
PROJECT_DEFAULT_MODE_MAP = env_json("PROJECT_DEFAULT_MODE_MAP", {})
PROJECT_HOST_BINDINGS = env_json("PROJECT_HOST_BINDINGS", {})
RAG_PROJECT_FLAGS = env_json("RAG_PROJECT_FLAGS", {})
ENDPOINT_BINDINGS = env_json("ENDPOINT_BINDINGS", {})
PROJECTS_ROOT = env_str("PROJECTS_ROOT", "/opt/projects")
PROJECT_ALIAS_TARGET_MODEL = env_str("PROJECT_ALIAS_TARGET_MODEL", "gpt-oss:20b")
PROJECT_ALIAS_CACHE_TTL_SECONDS = max(env_int("PROJECT_ALIAS_CACHE_TTL_SECONDS", 60), 1)
INGESTION_POLL_SECONDS = max(env_int("INGESTION_POLL_SECONDS", 2), 1)
QONDUIT_MAX_RAG_CHUNKS = max(1, env_int("QONDUIT_MAX_RAG_CHUNKS", 4))
QONDUIT_MAX_RAG_CHARS = max(1000, env_int("QONDUIT_MAX_RAG_CHARS", 6000))
QONDUIT_MAX_RECENT_MESSAGES = max(1, env_int("QONDUIT_MAX_RECENT_MESSAGES", 8))
QONDUIT_MAX_RECENT_MESSAGES_CODING = max(
    QONDUIT_MAX_RECENT_MESSAGES,
    env_int("QONDUIT_MAX_RECENT_MESSAGES_CODING", 16),
)
QONDUIT_TARGET_PROMPT_TOKENS = max(
    1024,
    env_int("QONDUIT_TARGET_PROMPT_TOKENS", 8192),
)

UPLOAD_DIR = "/mnt/models/qonduit_uploads"


def _configure_ingestion_logger() -> None:
    log_path = Path(GATEWAY_DATA_DIR) / "ingestion.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if any(
        isinstance(handler, logging.FileHandler)
        and getattr(handler, "baseFilename", "") == str(log_path)
        for handler in ingestion_logger.handlers
    ):
        return

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    ingestion_logger.setLevel(logging.INFO)
    ingestion_logger.addHandler(file_handler)
    ingestion_logger.propagate = True


_configure_ingestion_logger()
ingestion_manager = IngestionManager(
    data_dir=GATEWAY_DATA_DIR,
    projects_root=PROJECTS_ROOT,
    logger=ingestion_logger,
    poll_seconds=float(INGESTION_POLL_SECONDS),
)

TEXT_EXTENSIONS = {
    ".txt", ".md", ".json", ".csv",
    ".py", ".c", ".cpp", ".h", ".hpp",
    ".java", ".kt", ".kts",
    ".xml", ".html", ".css", ".js", ".ts",
    ".sql", ".yaml", ".yml", ".toml", ".ini", ".sh",
    ".go", ".rs", ".swift", ".php", ".rb", ".pl", ".lua",
    ".vhdl", ".vhd", ".v", ".dart",
}

DEFAULT_SYSTEM_PROMPT = (
    "You are Qonduit, a practical local coding and systems assistant. "
    "Be accurate, structured, and concise. "
    "For any non-trivial task, think in phases and present the answer in small, bounded chunks. "

    "Core response rules: "
    "1. Break complex work into numbered steps or short sections. "
    "2. Prefer the smallest complete useful answer over a long answer. "
    "3. Do not try to fit an oversized answer into one response. "
    "4. If the full answer would be long, give the first useful chunk only, stop at a natural boundary, and end with: "
    "\"Reply with continue for the next chunk.\" "
    "5. Never ramble. Avoid repetition and unnecessary explanation. "
    "6. For coding tasks, give the plan first if the task is large, then provide only the current step's code. "
    "7. For debugging, start with the most likely cause, then the next concrete action. "
    "8. For infrastructure or setup tasks, prefer exact commands and explicit file edits. "
    "9. Keep each response self-contained and easy to apply immediately. "
    "10. If the user asks for full detail, still split the answer into manageable parts instead of one huge block. "

    "Formatting rules: "
    "Use short headings when helpful. "
    "Use short numbered steps for procedures. "
    "Keep paragraphs short. "
    "When giving code, include only code needed for the current step unless the user explicitly asks for the full file. "

    "Behavior rules: "
    "If context is large or the task is broad, summarize the plan briefly before details. "
    "If multiple valid approaches exist, recommend one and keep alternatives brief. "
    "If the answer risks being cut off, compress and stop cleanly rather than continuing mid-thought. "
    "If relevant information exists in the provided context, conversation summary, or knowledge base excerpts, use it directly instead of guessing. "
    "Optimize for reliability, clarity, and completion over verbosity."
)


class ToolFunction(BaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, Any] | None = None


class ToolDefinition(BaseModel):
    type: Literal["function"] = "function"
    function: ToolFunction


class ChatMessage(BaseModel):
    role: str
    content: Any | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None

    model_config = {"extra": "allow"}


class GatewayChatRequest(BaseModel):
    conversation_id: str | None = None
    project_id: str | None = None
    messages: list[ChatMessage]
    model: str
    context_size: int | None = Field(default=None)
    max_tokens: int = Field(default=2048)
    temperature: float = Field(default=0.7)
    stream: bool = False
    user: str | None = None
    rag_collection: str | None = None
    mode: str | None = None
    tools: list[ToolDefinition] | None = None
    tool_choice: str | dict[str, Any] | None = None

    model_config = {"extra": "allow"}


READ_ONLY_GROUNDING_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "detect_project_type",
            "description": (
                "Detect the project type with confidence-ranked evidence. "
                "Call this first before assuming framework defaults."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_entry_points",
            "description": (
                "List real startup files for the current project. "
                "For Android, prioritize AndroidManifest.xml, MainActivity, "
                "and Application classes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of entry points to return.",
                        "default": 12,
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read exact file lines by path. Use this to verify assumptions "
                "after detecting project type and entry points."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the project root.",
                    },
                    "start_line": {"type": "integer", "default": 1},
                    "end_line": {
                        "type": "integer",
                        "description": "Optional inclusive end line.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_file",
            "description": (
                "Read full project file content (size-capped). Use for exact "
                "inspection when line ranges are not needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the project root.",
                    },
                    "max_bytes": {
                        "type": "integer",
                        "description": "Maximum bytes to read.",
                        "default": 120000,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_project_files",
            "description": (
                "List files inside the project root only. Useful for finding "
                "candidate files before exact reads."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string"},
                    "extensions": {"type": "array", "items": {"type": "string"}},
                    "max_results": {"type": "integer", "default": 50},
                },
                "required": [],
            },
        },
    },
]


EXECUTION_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": (
                "Apply controlled patch operations within the active project. "
                "Patch must be JSON with format qonduit.patch/v1."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patch": {
                        "type": "string",
                        "description": (
                            "JSON string: {\"format\":\"qonduit.patch/v1\","
                            "\"operations\":[{\"action\":\"write|create\","
                            "\"path\":\"relative/path\","
                            "\"content\":\"...\"}]}"
                        ),
                    }
                },
                "required": ["patch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_build",
            "description": (
                "Run an allowlisted build command based on detected project "
                "type. Arbitrary command strings are not accepted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "timeout_seconds": {
                        "type": "integer",
                        "default": 600,
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": (
                "Run an allowlisted test command based on detected project "
                "type. Arbitrary command strings are not accepted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "timeout_seconds": {
                        "type": "integer",
                        "default": 600,
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tail_logs",
            "description": (
                "Read bounded recent build or test logs for the active "
                "project only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["build", "tests"],
                        "default": "build",
                    },
                    "max_lines": {
                        "type": "integer",
                        "default": 120,
                    },
                },
                "required": [],
            },
        },
    },
]

DEFAULT_CODING_TOOLS: list[dict[str, Any]] = (
    READ_ONLY_GROUNDING_TOOLS + EXECUTION_TOOLS
)


class RagIngestRequest(BaseModel):
    text: str
    source: str = "manual_test"
    collection: str = "default"
    document_name: str = "untitled"


class RagSearchRequest(BaseModel):
    query: str
    limit: int = 4
    collection: str = "default"


class RagCollectionCreateRequest(BaseModel):
    name: str


class RagCollectionDeleteRequest(BaseModel):
    name: str


class EmbeddingsRequest(BaseModel):
    input: str | list[str]
    model: str | None = None
    user: str | None = None


class GithubWebhookStubRequest(BaseModel):
    project_id: str
    repo_path: str
    branch: str | None = None
    delivery_id: str | None = None


class IngestionEnqueueRequest(BaseModel):
    project_id: str
    repo_path: str | None = None
    branch: str | None = None


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "qonduit-memory-gateway"}


@app.get("/v1/ingestion/status")
async def ingestion_status_all() -> dict:
    return await ingestion_manager.status_all()


@app.get("/v1/ingestion/status/{project_id}")
async def ingestion_status_project(project_id: str) -> dict:
    return await ingestion_manager.status_project(project_id)


@app.get("/v1/ingestion/debug")
async def ingestion_debug_state() -> dict:
    """Return full debug state including queue, active job, and history."""
    return await ingestion_manager.debug_state()


@app.post("/v1/ingestion/enqueue")
async def ingestion_enqueue(req: IngestionEnqueueRequest) -> dict:
    result = await ingestion_manager.enqueue(
        project_id=req.project_id,
        repo_path=req.repo_path,
        branch=req.branch,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@app.get("/v1/models")
@app.get("/models")
async def list_models() -> dict:
    alias_models = list(alias_models_for_models_endpoint())

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{LLAMA_BASE}/v1/models")
    except httpx.RequestError as exc:
        logger.exception("models_proxy_connection_failed error=%s", str(exc))
        return {
            "object": "list",
            "data": alias_models
            + [
                {
                    "id": "qonduit-default",
                    "object": "model",
                    "owned_by": "qonduit",
                }
            ],
        }

    if response.status_code >= 400:
        logger.error(
            "models_proxy_upstream_error status=%s body=%s",
            response.status_code,
            response.text[:300],
        )
        raise upstream_error(response.status_code, response.text)

    try:
        upstream = response.json()
        if isinstance(upstream, dict):
            data = upstream.get("data")
            if isinstance(data, list):
                merged = list(data)
                seen = {str(item.get("id")) for item in merged if isinstance(item, dict)}
                for alias_model in alias_models:
                    if alias_model["id"] not in seen:
                        merged.append(alias_model)
                upstream["data"] = merged
                return upstream
        return {"object": "list", "data": alias_models}
    except ValueError:
        logger.error("models_proxy_invalid_json body=%s", response.text[:300])
        raise upstream_error(502, "Upstream /v1/models returned invalid JSON")


@app.post("/v1/embeddings")
async def embeddings(req: EmbeddingsRequest) -> dict:
    try:
        return await create_embeddings_response(req.input, model=req.model or EMBEDDING_MODEL)
    except httpx.HTTPStatusError as error:
        status = error.response.status_code if error.response is not None else 502
        detail = error.response.text if error.response is not None else str(error)
        logger.error("embeddings_upstream_error status=%s detail=%s", status, detail[:300])
        raise upstream_error(status, detail)
    except Exception as error:
        logger.exception("embeddings_request_failed error=%s", str(error))
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Failed to generate embeddings",
                "detail": str(error),
            },
        )


def get_request_user_id(request: Request) -> str:
    raw = request.headers.get("X-Qonduit-User", "").strip().lower()
    safe = "".join(c for c in raw if c.isalnum() or c in ("-", "_"))
    return safe or "default"


def build_fallback_conversation_id(req: GatewayChatRequest, request: Request) -> str:
    seed_payload = {
        "model": req.model,
        "messages": [
            {"role": msg.role, "content": coerce_model_content_to_text(msg.content)}
            for msg in req.messages
        ],
        "user_agent": request.headers.get("user-agent", ""),
    }
    digest = sha256(
        json.dumps(seed_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"session-{digest[:16]}"


def resolve_conversation_id(req: GatewayChatRequest, request: Request) -> str:
    header_id = (
        request.headers.get("X-Conversation-ID", "").strip()
        or request.headers.get("X-Qonduit-Conversation", "").strip()
    )
    if header_id:
        return header_id

    if (req.user or "").strip():
        return req.user.strip()

    if (req.conversation_id or "").strip():
        return req.conversation_id.strip()

    return build_fallback_conversation_id(req, request)


def resolve_context_size(req: GatewayChatRequest, state: dict[str, Any]) -> int:
    if req.context_size is not None:
        return max(int(req.context_size), 1024)

    last_size = state.get("last_context_size")
    if isinstance(last_size, int) and last_size >= 1024:
        return last_size

    return DEFAULT_CONTEXT_SIZE


def sanitize_identifier(value: str | None, fallback: str) -> str:
    cleaned = "".join(c for c in (value or "") if c.isalnum() or c in ("-", "_"))
    if not cleaned:
        return fallback
    return cleaned


def resolve_project_root(project_id: str) -> Path:
    safe_project = sanitize_identifier(project_id, "default")
    return (Path(PROJECTS_ROOT) / safe_project).resolve()


def _is_within_dir(candidate: Path, base_dir: Path) -> bool:
    candidate_s = str(candidate.resolve())
    base_s = str(base_dir.resolve())
    return candidate_s == base_s or candidate_s.startswith(base_s + os.sep)


def resolve_project_relative_directory(
    project_id: str,
    directory: str | None,
) -> Path | None:
    base_dir = resolve_project_root(project_id)
    raw = (directory or "").strip().lstrip("/")
    candidate = (base_dir / raw).resolve() if raw else base_dir
    if not _is_within_dir(candidate, base_dir):
        return None
    return candidate


def resolve_project_relative_file(project_id: str, path: str) -> Path | None:
    base_dir = resolve_project_root(project_id)
    raw = (path or "").strip().lstrip("/")
    if not raw:
        return None
    candidate = (base_dir / raw).resolve()
    if not _is_within_dir(candidate, base_dir):
        return None
    return candidate


def collect_project_markers(project_root: Path) -> dict[str, bool]:
    def exists(rel: str) -> bool:
        return (project_root / rel).exists()

    has_android_manifest = exists("android/app/src/main/AndroidManifest.xml")
    has_android_gradle = (
        exists("android/settings.gradle")
        or exists("android/settings.gradle.kts")
        or exists("android/app/build.gradle")
        or exists("android/app/build.gradle.kts")
    )
    has_android_kotlin = bool(
        list((project_root / "android/app/src/main/kotlin").glob("**/*.kt"))
    )
    has_android_java = bool(
        list((project_root / "android/app/src/main/java").glob("**/*.java"))
    )
    has_main_activity = bool(
        list(project_root.glob("**/MainActivity.kt"))
        or list(project_root.glob("**/MainActivity.java"))
    )
    has_application_class = bool(
        list(project_root.glob("**/*Application.kt"))
        or list(project_root.glob("**/*Application.java"))
    )

    return {
        "android_manifest": has_android_manifest,
        "android_gradle": has_android_gradle,
        "android_kotlin": has_android_kotlin,
        "android_java": has_android_java,
        "android_main_activity": has_main_activity,
        "android_application_class": has_application_class,
        "flutter_pubspec": exists("pubspec.yaml"),
        "flutter_lib_main": exists("lib/main.dart"),
        "react_package_json": exists("package.json"),
        "react_src_main": (
            exists("src/main.tsx")
            or exists("src/main.jsx")
            or exists("src/index.tsx")
            or exists("src/index.jsx")
        ),
    }


def rank_project_types(evidence: dict[str, bool]) -> list[dict[str, Any]]:
    android_score = 0.0
    android_evidence: list[str] = []
    if evidence.get("android_manifest"):
        android_score += 0.35
        android_evidence.append("android/app/src/main/AndroidManifest.xml")
    if evidence.get("android_gradle"):
        android_score += 0.2
        android_evidence.append("android gradle files")
    if evidence.get("android_kotlin"):
        android_score += 0.2
        android_evidence.append("android/app/src/main/kotlin/**/*.kt")
    if evidence.get("android_java"):
        android_score += 0.1
        android_evidence.append("android/app/src/main/java/**/*.java")
    if evidence.get("android_main_activity"):
        android_score += 0.1
        android_evidence.append("MainActivity.kt/.java")
    if evidence.get("android_application_class"):
        android_score += 0.05
        android_evidence.append("*Application.kt/.java")
    android_score = min(android_score, 1.0)

    flutter_score = 0.0
    flutter_evidence: list[str] = []
    if evidence.get("flutter_pubspec"):
        flutter_score += 0.35
        flutter_evidence.append("pubspec.yaml")
    if evidence.get("flutter_lib_main"):
        flutter_score += 0.45
        flutter_evidence.append("lib/main.dart")
    if evidence.get("android_manifest"):
        flutter_score += 0.2
        flutter_evidence.append("android/app/src/main/AndroidManifest.xml")
    flutter_score = min(flutter_score, 1.0)

    web_score = 0.0
    web_evidence: list[str] = []
    if evidence.get("react_package_json"):
        web_score += 0.4
        web_evidence.append("package.json")
    if evidence.get("react_src_main"):
        web_score += 0.6
        web_evidence.append("src/main.tsx|jsx or src/index.tsx|jsx")
    web_score = min(web_score, 1.0)

    ranked = [
        {
            "type": "android_kotlin",
            "score": round(android_score, 3),
            "evidence": android_evidence,
        },
        {
            "type": "flutter",
            "score": round(flutter_score, 3),
            "evidence": flutter_evidence,
        },
        {
            "type": "web",
            "score": round(web_score, 3),
            "evidence": web_evidence,
        },
    ]
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked


def discover_entry_points(project_root: Path, project_type: str) -> list[dict[str, Any]]:
    def rel(path: Path) -> str:
        return str(path.relative_to(project_root))

    entries: list[dict[str, Any]] = []

    def add_if_exists(relative_path: str, priority: int, reason: str) -> None:
        candidate = project_root / relative_path
        if candidate.is_file():
            entries.append(
                {
                    "path": relative_path,
                    "priority": priority,
                    "reason": reason,
                }
            )

    if project_type in {"android_kotlin", "flutter"}:
        add_if_exists(
            "android/app/src/main/AndroidManifest.xml",
            100,
            "Android startup manifest",
        )
        for main_activity in sorted(project_root.glob("**/MainActivity.kt")):
            entries.append(
                {
                    "path": rel(main_activity),
                    "priority": 95,
                    "reason": "Android Activity entry point",
                }
            )
        for main_activity in sorted(project_root.glob("**/MainActivity.java")):
            entries.append(
                {
                    "path": rel(main_activity),
                    "priority": 94,
                    "reason": "Android Activity entry point",
                }
            )
        for app_file in sorted(project_root.glob("**/*Application.kt")):
            entries.append(
                {
                    "path": rel(app_file),
                    "priority": 90,
                    "reason": "Android Application initialization",
                }
            )
        for app_file in sorted(project_root.glob("**/*Application.java")):
            entries.append(
                {
                    "path": rel(app_file),
                    "priority": 89,
                    "reason": "Android Application initialization",
                }
            )

    if project_type == "flutter":
        add_if_exists("lib/main.dart", 85, "Flutter app root entry point")

    if project_type == "web":
        add_if_exists("src/main.tsx", 80, "React/TypeScript entry point")
        add_if_exists("src/main.jsx", 79, "React entry point")
        add_if_exists("src/index.tsx", 78, "React/TypeScript entry point")
        add_if_exists("src/index.jsx", 77, "React entry point")

    if not entries:
        add_if_exists("README.md", 20, "Fallback project overview")

    entries.sort(key=lambda item: item["priority"], reverse=True)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in entries:
        path = item["path"]
        if path in seen:
            continue
        seen.add(path)
        deduped.append(item)
    return deduped


def model_alias_entry(model: str) -> dict[str, Any] | None:
    entry = merged_alias_config().get(model)
    if isinstance(entry, dict):
        return entry
    return None


def discovered_alias_config(force_refresh: bool = False) -> dict[str, dict[str, Any]]:
    return project_alias_cache.get(
        projects_root=PROJECTS_ROOT,
        target_model=PROJECT_ALIAS_TARGET_MODEL,
        ttl_seconds=PROJECT_ALIAS_CACHE_TTL_SECONDS,
        force_refresh=force_refresh,
    )


def endpoint_alias_config() -> dict[str, dict[str, Any]]:
    aliases: dict[str, dict[str, Any]] = {}
    for host, binding in ENDPOINT_BINDINGS.items():
        if not isinstance(binding, dict):
            continue
        alias_id = str(binding.get("model_alias", "")).strip()
        if not alias_id:
            continue
        aliases[alias_id] = {
            "model": binding.get("model"),
            "project_id": binding.get("project_id"),
            "default_mode": binding.get("default_mode"),
            "rag_enabled": binding.get("rag_enabled"),
            "source": "endpoint_binding",
            "host": host,
        }
    return aliases


def merged_alias_config() -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    merged.update(endpoint_alias_config())
    for alias_id, alias in discovered_alias_config().items():
        if alias_id not in merged:
            merged[alias_id] = alias
    merged.update(
        {
            alias_id: alias
            for alias_id, alias in MODEL_ALIAS_CONFIG.items()
            if isinstance(alias, dict)
        }
    )
    return merged


def alias_models_for_models_endpoint() -> list[dict[str, Any]]:
    alias_models: list[dict[str, Any]] = []
    for alias_id, alias in merged_alias_config().items():
        owned_by = "qonduit-alias"
        if alias.get("source") == "auto_discovered_repo":
            owned_by = "qonduit-auto-discovery"
        elif alias.get("source") == "endpoint_binding":
            owned_by = "qonduit-endpoint-binding"
        alias_models.append(
            {
                "id": alias_id,
                "object": "model",
                "owned_by": owned_by,
                "metadata": {
                    "project_id": alias.get("project_id"),
                    "default_mode": alias.get("default_mode"),
                    "target_model": alias.get("model"),
                    "rag_enabled": alias.get("rag_enabled"),
                    "source": alias.get("source"),
                    "repo_path": alias.get("repo_path"),
                    "branch": alias.get("branch"),
                    "host": alias.get("host"),
                },
            }
        )
    return alias_models


def endpoint_binding_entry(request: Request) -> dict[str, Any] | None:
    host = (request.headers.get("host", "") or "").split(":")[0].strip().lower()
    if not host:
        return None
    entry = ENDPOINT_BINDINGS.get(host)
    if isinstance(entry, dict):
        return entry
    return None


def resolve_effective_model(req: GatewayChatRequest, request: Request) -> str:
    alias = model_alias_entry(req.model)
    if alias is not None:
        model_value = str(alias.get("model", "")).strip()
        if model_value:
            return model_value

    binding = endpoint_binding_entry(request)
    if binding is not None:
        model_value = str(binding.get("model", "")).strip()
        if model_value:
            return model_value

    return req.model


def resolve_project_id(req: GatewayChatRequest, request: Request) -> str:
    header_project = request.headers.get("X-Project-ID", "").strip()
    if header_project:
        return sanitize_identifier(header_project, DEFAULT_PROJECT_NAMESPACE)

    if (req.project_id or "").strip():
        return sanitize_identifier(req.project_id, DEFAULT_PROJECT_NAMESPACE)

    alias = model_alias_entry(req.model)
    if alias is not None:
        alias_project = str(alias.get("project_id", "")).strip()
        if alias_project:
            return sanitize_identifier(alias_project, DEFAULT_PROJECT_NAMESPACE)

    endpoint_binding = endpoint_binding_entry(request)
    if endpoint_binding is not None:
        binding_project = str(endpoint_binding.get("project_id", "")).strip()
        if binding_project:
            return sanitize_identifier(binding_project, DEFAULT_PROJECT_NAMESPACE)

    host = (request.headers.get("host", "") or "").split(":")[0].strip().lower()
    binding = PROJECT_HOST_BINDINGS.get(host)
    if isinstance(binding, str) and binding.strip():
        return sanitize_identifier(binding.strip(), DEFAULT_PROJECT_NAMESPACE)

    return sanitize_identifier(DEFAULT_PROJECT_NAMESPACE, DEFAULT_PROJECT_NAMESPACE)


def request_project_id(request: Request) -> str:
    header_project = request.headers.get("X-Project-ID", "").strip()
    if header_project:
        return sanitize_identifier(header_project, DEFAULT_PROJECT_NAMESPACE)
    return sanitize_identifier(DEFAULT_PROJECT_NAMESPACE, DEFAULT_PROJECT_NAMESPACE)


def resolve_mode(req: GatewayChatRequest, request: Request, project_id: str) -> str:
    requested_mode = (req.mode or "").strip().lower()
    if requested_mode in {"chat", "coding"}:
        return requested_mode

    header_mode = request.headers.get("X-Gateway-Mode", "").strip().lower()
    if header_mode in {"chat", "coding"}:
        return header_mode

    alias = model_alias_entry(req.model)
    if alias is not None:
        alias_mode = str(alias.get("default_mode", "")).strip().lower()
        if alias_mode in {"chat", "coding"}:
            return alias_mode

    endpoint_binding = endpoint_binding_entry(request)
    if endpoint_binding is not None:
        binding_mode = str(endpoint_binding.get("default_mode", "")).strip().lower()
        if binding_mode in {"chat", "coding"}:
            return binding_mode

    project_mode = PROJECT_DEFAULT_MODE_MAP.get(project_id)
    if isinstance(project_mode, str) and project_mode.strip().lower() in {"chat", "coding"}:
        return project_mode.strip().lower()

    env_mode = PROJECT_DEFAULT_MODE.strip().lower()
    if env_mode in {"chat", "coding"}:
        return env_mode

    return "chat"


def system_prompt_for_mode(mode: str) -> str:
    if mode == "coding":
        return (
            "You are Qonduit in CODING mode. "
            "Prioritize correctness, exact technical details, and reproducible steps. "
            "Preserve exact file paths, function/class names, commands, errors, and constraints. "
            "For repository questions, detect project type first and inspect real "
            "entry points before assuming framework defaults. "
            "Prefer exact file reads over guessed structures. "
            "When uncertain, state assumptions briefly and propose the next verification command."
        )
    return DEFAULT_SYSTEM_PROMPT


def is_technical_message(message: dict[str, Any]) -> bool:
    content = str(message.get("content", ""))
    if not content.strip():
        return False
    patterns = [
        r"```",
        r"\bTraceback\b",
        r"\bException\b",
        r"\bError\b",
        r"\bFAILED\b",
        r"\b(?:[A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|dart|go|rs|java|kt|json|yaml|yml|toml|ini|sh|md))\b",
        r"\b(?:python|pytest|pip|poetry|npm|pnpm|yarn|cargo|go|flutter|dart|make|uvicorn)\b",
        r"\b(?:must|do not|don't|cannot|can't|required|constraint)\b",
    ]
    return any(re.search(pattern, content, flags=re.IGNORECASE) for pattern in patterns)


def should_enable_rag(
    project_id: str,
    mode: str,
    alias: dict[str, Any] | None = None,
    binding: dict[str, Any] | None = None,
) -> bool:
    if not RAG_ENABLED:
        return False

    if alias is not None and "rag_enabled" in alias:
        value = alias.get("rag_enabled")
        if isinstance(value, bool):
            return value

    if binding is not None and "rag_enabled" in binding:
        value = binding.get("rag_enabled")
        if isinstance(value, bool):
            return value

    project_flag = RAG_PROJECT_FLAGS.get(project_id)
    if isinstance(project_flag, bool):
        return project_flag
    if isinstance(project_flag, str):
        normalized = project_flag.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False

    mode_flag = RAG_PROJECT_FLAGS.get(f"mode:{mode}")
    if isinstance(mode_flag, bool):
        return mode_flag

    return True


def upstream_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "message": "Upstream request failed",
            "upstream_status": status_code,
            "detail": detail,
        },
    )


def ensure_upload_dir() -> None:
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def sanitize_filename(filename: str) -> str:
    cleaned = filename.strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", cleaned)
    cleaned = cleaned.strip("._")
    return cleaned or "uploaded_file"


def build_saved_upload_path(user_id: str, collection_name: str, filename: str) -> str:
    safe_user = sanitize_filename(user_id)
    safe_collection = sanitize_filename(collection_name)
    safe_filename = sanitize_filename(filename)
    file_id = str(uuid.uuid4())
    collection_dir = os.path.join(UPLOAD_DIR, safe_user, safe_collection)
    os.makedirs(collection_dir, exist_ok=True)
    return os.path.join(collection_dir, f"{file_id}__{safe_filename}")


def collection_upload_dir(user_id: str, collection_name: str) -> str:
    safe_user = sanitize_filename(user_id)
    safe_collection = sanitize_filename(collection_name)
    return os.path.join(UPLOAD_DIR, safe_user, safe_collection)


def delete_collection_upload_dir(user_id: str, collection_name: str) -> int:
    import shutil

    target_dir = collection_upload_dir(user_id, collection_name)
    if not os.path.exists(target_dir):
        return 0

    file_count = 0
    for _, _, files in os.walk(target_dir):
        file_count += len(files)

    shutil.rmtree(target_dir, ignore_errors=True)
    return file_count


def code_edit_artifact_dir(user_id: str) -> str:
    safe_user = sanitize_filename(user_id)
    target_dir = os.path.join(UPLOAD_DIR, safe_user, "__code_edits__")
    os.makedirs(target_dir, exist_ok=True)
    return target_dir


def save_code_edit_artifact(user_id: str, filename: str, content: str) -> dict[str, Any]:
    safe_name = sanitize_filename(Path(filename).name or "modified_file.txt")
    if not Path(safe_name).suffix:
        safe_name = f"{safe_name}.txt"

    artifact_id = str(uuid.uuid4())
    saved_path = os.path.join(code_edit_artifact_dir(user_id), f"{artifact_id}__{safe_name}")

    with open(saved_path, "w", encoding="utf-8") as f:
        f.write(content)

    return {
        "id": artifact_id,
        "name": safe_name,
        "saved_path": saved_path,
        "size_bytes": len(content.encode("utf-8")),
        "type": "code_edit_file",
    }


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []

    chunks: list[str] = []
    start = 0
    text_len = len(cleaned)

    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = cleaned[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_len:
            break
        start = max(end - overlap, 0)

    return chunks


def extract_text_from_txt_like(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def extract_text_from_pdf(file_path: str) -> str:
    reader = PdfReader(file_path)
    parts: list[str] = []

    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        if page_text.strip():
            parts.append(page_text)

    return "\n\n".join(parts)


def extract_text_from_docx(file_path: str) -> str:
    doc = Document(file_path)
    parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    return "\n\n".join(parts)


def extract_text_from_csv_file(file_path: str) -> str:
    rows: list[str] = []
    with open(file_path, "r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(" | ".join(cell.strip() for cell in row))
    return "\n".join(rows)


def extract_text_from_xlsx(file_path: str) -> str:
    wb = load_workbook(file_path, data_only=True)
    parts: list[str] = []

    for sheet in wb.worksheets:
        parts.append(f"# Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            values = ["" if cell is None else str(cell) for cell in row]
            line = " | ".join(v.strip() for v in values if str(v).strip())
            if line:
                parts.append(line)

    return "\n".join(parts)


def extract_text_from_file(file_path: str, suffix: str) -> str:
    suffix = suffix.lower()

    if suffix in TEXT_EXTENSIONS:
        return extract_text_from_txt_like(file_path)

    if suffix == ".pdf":
        return extract_text_from_pdf(file_path)

    if suffix == ".docx":
        return extract_text_from_docx(file_path)

    if suffix == ".csv":
        return extract_text_from_csv_file(file_path)

    if suffix in {".xlsx", ".xls"}:
        return extract_text_from_xlsx(file_path)

    raise ValueError(f"Unsupported file type: {suffix}")


def latest_user_text(messages: list[ChatMessage]) -> str:
    for msg in reversed(messages):
        if msg.role == "user":
            return coerce_model_content_to_text(msg.content)
    return ""


def split_recent_history_and_current_user(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    last_user_index: int | None = None
    for idx in range(len(messages) - 1, -1, -1):
        if str(messages[idx].get("role", "")).lower() == "user":
            last_user_index = idx
            break
    if last_user_index is None:
        return list(messages), None
    current_user = dict(messages[last_user_index])
    history = [
        dict(message)
        for index, message in enumerate(messages)
        if index != last_user_index
    ]
    return history, current_user


def rag_stable_key(result: dict[str, Any]) -> str:
    payload = result.get("payload")
    if isinstance(payload, dict):
        source = str(payload.get("source") or "").strip()
        path = str(payload.get("path") or payload.get("file_path") or "").strip()
        chunk_index = payload.get("chunk_index")
        chunk_render = "" if chunk_index is None else str(chunk_index)
    else:
        source = ""
        path = ""
        chunk_render = ""
    doc_id = str(result.get("id") or "").strip()
    return "|".join([source, path, doc_id, chunk_render])


def sort_rag_results_deterministically(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    def sort_key(item: dict[str, Any]) -> tuple[float, str]:
        score = item.get("score")
        numeric_score = float(score) if isinstance(score, (int, float)) else 0.0
        return (-numeric_score, rag_stable_key(item))

    return sorted(results, key=sort_key)


def build_bounded_rag_chunks(
    rag_results: list[dict[str, Any]],
    max_chunks: int,
    max_chars: int,
) -> tuple[list[str], list[str]]:
    selected_chunks: list[str] = []
    selected_keys: list[str] = []
    consumed_chars = 0
    for result in rag_results:
        text = str(result.get("text", "")).strip()
        if not text:
            continue
        if len(selected_chunks) >= max_chunks:
            break
        remaining = max_chars - consumed_chars
        if remaining <= 0:
            break
        bounded_text = text[:remaining].strip()
        if not bounded_text:
            continue
        selected_chunks.append(bounded_text)
        selected_keys.append(rag_stable_key(result))
        consumed_chars += len(bounded_text)
    return selected_chunks, selected_keys


def approx_tokens_from_chars(total_chars: int) -> int:
    if total_chars <= 0:
        return 0
    return max(1, int(round(total_chars / 3.7)))


def _debug_preview(text: str, limit: int = 280) -> str:
    preview = text.replace("\n", "\\n")
    if len(preview) > limit:
        return preview[:limit] + "...(truncated)"
    return preview


def debug_code_edit_event(event: str, **kwargs: Any) -> None:
    parts: list[str] = []
    for key, value in kwargs.items():
        try:
            rendered = str(value)
        except Exception:
            rendered = "<unrenderable>"
        parts.append(f"{key}={rendered}")
    print(f"[code-edit-debug] {event} | " + " | ".join(parts), flush=True)


def is_code_edit_request(text: str) -> bool:
    lowered = text.lower()
    return (
        "code edit" in lowered
        or "modified file" in lowered
        or "update this file" in lowered
        or "rewrite this file" in lowered
    )


def unwrap_structured_text_payload(text: str) -> str:
    raw = text.strip()
    if not raw.startswith("[") or "text" not in raw:
        return text

    parsed: Any | None = None
    try:
        parsed = json.loads(raw)
    except Exception:
        try:
            parsed = ast.literal_eval(raw)
        except Exception:
            return text

    if not isinstance(parsed, list):
        return text

    parts: list[str] = []
    for item in parsed:
        if isinstance(item, dict):
            kind = str(item.get("type", "")).lower()
            if kind in {"text", "input_text"}:
                value = item.get("text")
                if isinstance(value, str) and value.strip():
                    parts.append(value)

    if not parts:
        return text
    return "\n".join(parts)


def extract_requested_filename(text: str) -> str | None:
    patterns = [
        r"file:\s*([^\n\r]+)",
        r"for file:\s*([^\n\r]+)",
        r"filename:\s*([^\n\r]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if value:
                return Path(value).name
    return None


def extract_inline_file_contents(text: str) -> str:
    normalized = unwrap_structured_text_payload(text)
    patterns = [
        r"Current file contents:\s*\n(?P<body>.*)$",
        r"Current file:\s*\n(?P<body>.*)$",
        r"<<<FILE\s*\n(?P<body>.*?)\nFILE\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        if match:
            body = match.group("body").strip("\n\r")
            if body.strip():
                return body
    return ""


def extract_code_edit_instruction(text: str) -> str:
    working = unwrap_structured_text_payload(text)

    working = re.sub(
        r"^\s*Code edit request for file:\s*[^\n\r]+\s*",
        "",
        working,
        flags=re.IGNORECASE,
    )
    working = re.sub(
        r"Current file contents:\s*\n.*$",
        "",
        working,
        flags=re.IGNORECASE | re.DOTALL,
    )
    working = re.sub(
        r"Current file:\s*\n.*$",
        "",
        working,
        flags=re.IGNORECASE | re.DOTALL,
    )
    working = re.sub(
        r"<<<FILE\s*\n.*?\nFILE\s*$",
        "",
        working,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return working.strip()


def build_code_edit_model_input(
    requested_filename: str | None,
    instruction: str,
    file_contents: str,
) -> str:
    target_name = requested_filename or "modified_file.txt"
    safe_instruction = instruction.strip() or "Update the file as requested."

    return (
        f"Target file: {target_name}\n\n"
        f"Edit instruction:\n{safe_instruction}\n\n"
        "Current file contents:\n"
        f"{file_contents}"
    )


def build_code_edit_contract_system_prompt(requested_filename: str | None) -> str:
    target_name = requested_filename or "modified_file.txt"
    return (
        "You are handling a code edit request. "
        "You will be given the target file name, a concrete edit instruction, and the current file contents. "
        "Use the provided file contents as the source of truth. "
        "Do not return a unified diff. "
        "Do not return markdown fences. "
        "Do not include conversational filler. "
        "Return only valid JSON with this exact schema:\n"
        "{\n"
        '  "executive_summary": ["short bullet", "short bullet"],\n'
        '  "change_summary": ["technical bullet", "technical bullet"],\n'
        '  "patch_confidence": "high|medium|low",\n'
        '  "modified_file": {\n'
        f'    "name": "{target_name}",\n'
        '    "content": "full updated file contents here"\n'
        "  }\n"
        "}\n"
        "Rules:\n"
        "1. executive_summary must be short and human-readable.\n"
        "2. change_summary must be technical and concise.\n"
        "3. patch_confidence must be exactly one of: high, medium, low.\n"
        "4. modified_file.content must contain the full updated file contents.\n"
        "5. Never return a diff.\n"
        "6. Never omit modified_file.content if you can complete the edit.\n"
        "7. Only use low patch_confidence when the instruction is ambiguous or the supplied file contents are clearly insufficient.\n"
    )


def coerce_model_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
            elif isinstance(item, str) and item.strip():
                parts.append(item)
        return "\n".join(parts).strip()

    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text

    return str(content or "")


def extract_json_object(raw: str) -> str | None:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return raw[start:end + 1]


def normalize_summary_lines(value: Any) -> list[str]:
    if isinstance(value, list):
        items = []
        for item in value:
            text = str(item).strip()
            if text:
                items.append(text)
        return items

    if isinstance(value, str):
        lines = []
        for line in value.splitlines():
            cleaned = line.strip().lstrip("-•* ").strip()
            if cleaned:
                lines.append(cleaned)
        return lines

    return []


def normalize_patch_confidence(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"high", "medium", "low"}:
        return text
    return "low"


def extract_fenced_code_block(raw: str) -> str:
    blocks = re.findall(
        r"```(?:[a-zA-Z0-9_+\-#.]+)?\s*\n(.*?)```",
        raw,
        flags=re.DOTALL,
    )
    cleaned_blocks = [block.strip("\n\r") for block in blocks if block.strip()]
    if not cleaned_blocks:
        return ""

    cleaned_blocks.sort(key=len, reverse=True)
    return cleaned_blocks[0]


def build_non_json_code_edit_fallback(
    raw: str,
    requested_filename: str | None,
) -> dict[str, Any] | None:
    recovered = extract_fenced_code_block(raw)
    if not recovered.strip():
        return None

    fallback_name = requested_filename or "modified_file.txt"
    return {
        "executive_summary": [
            "Recovered a code-edit result from a non-JSON model response."
        ],
        "change_summary": [
            "The model did not follow the JSON contract, so the gateway extracted "
            "the largest fenced code block as the modified file content."
        ],
        "patch_confidence": "low",
        "modified_file": {
            "name": Path(fallback_name).name or "modified_file.txt",
            "content": recovered,
        },
    }


def parse_code_edit_response(raw: Any, requested_filename: str | None) -> dict[str, Any]:
    raw_text = coerce_model_content_to_text(raw)
    default_name = requested_filename or "modified_file.txt"
    default = {
        "executive_summary": [
            "The model did not return the requested structured code-edit response."
        ],
        "change_summary": [
            "No attachment-ready modified file was produced from the response."
        ],
        "patch_confidence": "low",
        "modified_file": {
            "name": default_name,
            "content": "",
        },
    }

    blob = extract_json_object(raw_text)
    if not blob:
        recovered = build_non_json_code_edit_fallback(raw_text, requested_filename)
        if recovered is not None:
            return recovered
        return default

    try:
        parsed = json.loads(blob)
    except Exception:
        recovered = build_non_json_code_edit_fallback(raw_text, requested_filename)
        if recovered is not None:
            return recovered
        return default

    executive_summary = normalize_summary_lines(parsed.get("executive_summary"))
    change_summary = normalize_summary_lines(parsed.get("change_summary"))
    patch_confidence = normalize_patch_confidence(parsed.get("patch_confidence"))

    modified_file = parsed.get("modified_file", {})
    if not isinstance(modified_file, dict):
        modified_file = {}

    modified_name = str(
        modified_file.get("name")
        or parsed.get("modified_file_name")
        or default_name
    ).strip() or default_name

    modified_content = str(
        modified_file.get("content")
        or parsed.get("modified_file_content")
        or ""
    )

    if not modified_content.strip():
        recovered_content = extract_fenced_code_block(raw_text)
        if recovered_content.strip():
            modified_content = recovered_content
            if not change_summary:
                change_summary = [
                    "Recovered modified file content from a fenced code block "
                    "because modified_file.content was empty."
                ]
            patch_confidence = "low"

    if not executive_summary:
        executive_summary = ["Prepared a code-edit response."]
    if not change_summary:
        if modified_content.strip():
            change_summary = [f"Prepared updated file contents for {modified_name}."]
        else:
            change_summary = ["No updated file contents were provided by the model."]

    return {
        "executive_summary": executive_summary,
        "change_summary": change_summary,
        "patch_confidence": patch_confidence,
        "modified_file": {
            "name": Path(modified_name).name or default_name,
            "content": modified_content,
        },
    }


def format_code_edit_summary(parsed: dict[str, Any], artifact: dict[str, Any] | None) -> str:
    lines: list[str] = []

    lines.append("Executive Summary")
    for item in parsed["executive_summary"]:
        lines.append(f"• {item}")

    lines.append("")
    lines.append("Change Summary")
    for item in parsed["change_summary"]:
        lines.append(f"• {item}")

    lines.append("")
    lines.append(f"Patch Confidence: {str(parsed['patch_confidence']).upper()}")

    if artifact is not None:
        lines.append("")
        lines.append(f"Prepared File: {artifact['name']}")
        lines.append(f"Saved Path: {artifact['saved_path']}")

    return "\n".join(lines).strip()


def sse_chunk(model: str, content: str = "", finish_reason: str | None = None) -> str:
    payload = {
        "id": f"chatcmpl-qonduit-{uuid.uuid4().hex}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": content} if content else {},
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def split_for_stream(text: str, chunk_size: int = 180) -> list[str]:
    if not text:
        return []
    parts: list[str] = []
    start = 0
    while start < len(text):
        parts.append(text[start:start + chunk_size])
        start += chunk_size
    return parts


def strip_markdown_fences(text: str) -> str:
    trimmed = text.strip()
    if not trimmed.startswith("```"):
        return trimmed

    match = re.match(r"^```[^\n]*\n(?P<body>.*)\n```$", trimmed, flags=re.DOTALL)
    if not match:
        return trimmed
    return match.group("body").strip("\n\r")


def try_apply_extension_edit_locally(instruction: str, original_file: str) -> str | None:
    lowered = instruction.lower()
    if "add" not in lowered or "extension" not in lowered:
        return None

    ext_match = re.search(r"(\.[a-z0-9_+-]+)", instruction, flags=re.IGNORECASE)
    if not ext_match:
        return None
    extension = ext_match.group(1).lower()

    collection_match = re.search(
        r"(?P<prefix>\b[A-Z_]*EXTENSIONS\b\s*=\s*)(?P<open>[\[\(\{])(?P<body>.*?)(?P<close>[\]\)\}])",
        original_file,
        flags=re.DOTALL,
    )
    if not collection_match:
        return None

    body = collection_match.group("body")
    if re.search(rf"['\"]{re.escape(extension)}['\"]", body, flags=re.IGNORECASE):
        return original_file

    quote = "'" if "'" in body else '"'
    updated_body = body.rstrip()
    if updated_body and not updated_body.endswith(","):
        updated_body = f"{updated_body},"

    insertion = f"\n    {quote}{extension}{quote},"
    replaced = (
        f"{collection_match.group('prefix')}"
        f"{collection_match.group('open')}"
        f"{updated_body}{insertion}\n"
        f"{collection_match.group('close')}"
    )

    start, end = collection_match.span()
    return f"{original_file[:start]}{replaced}{original_file[end:]}"


def looks_like_full_file_content(original_file: str, candidate_file: str) -> bool:
    candidate = candidate_file.strip()
    if not candidate:
        return False

    if len(candidate) < max(120, int(len(original_file) * 0.2)):
        return False

    if "\n" not in candidate and len(original_file) > 400:
        return False

    return True


async def recover_modified_file_with_retry(
    model: str,
    requested_filename: str | None,
    instruction: str,
    original_file_contents: str,
) -> str:
    target_name = requested_filename or "modified_file.txt"
    retry_messages = [
        {
            "role": "system",
            "content": (
                "You are repairing a failed code-edit response. "
                "Return only the full updated file contents. "
                "Do not return JSON. "
                "Do not return markdown fences. "
                "Do not explain anything."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Target file: {target_name}\n\n"
                f"Edit instruction:\n{instruction.strip()}\n\n"
                "Current file contents:\n"
                f"{original_file_contents}"
            ),
        },
    ]

    retry_payload = {
        "model": model,
        "messages": retry_messages,
        "max_tokens": 8192,
        "temperature": 0.0,
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=45.0) as client:
        retry_response = await client.post(
            f"{LLAMA_BASE}/v1/chat/completions",
            json=retry_payload,
        )
        retry_response.raise_for_status()
        retry_data = retry_response.json()

    raw_content = str(retry_data["choices"][0]["message"]["content"] or "")
    return strip_markdown_fences(raw_content)


@app.post("/rag/test-ingest")
async def rag_test_ingest(req: RagIngestRequest, request: Request) -> dict:
    user_id = get_request_user_id(request)
    project_id = request_project_id(request)
    doc_id = await add_document(
        text=req.text,
        metadata={
            "source": req.source,
            "collection": req.collection,
            "document_name": req.document_name,
        },
        user_id=user_id,
        collection=req.collection,
        project_id=project_id,
    )
    return {"ok": True, "id": doc_id}


@app.post("/rag/test-search")
async def rag_test_search(req: RagSearchRequest, request: Request) -> dict:
    user_id = get_request_user_id(request)
    project_id = request_project_id(request)
    results = await search_documents(
        req.query,
        limit=req.limit,
        collection=req.collection,
        user_id=user_id,
        project_id=project_id,
    )
    return {"ok": True, "results": results}


@app.get("/rag/collections")
async def rag_list_collections(request: Request) -> dict:
    user_id = get_request_user_id(request)
    project_id = request_project_id(request)
    return {
        "ok": True,
        "collections": list_collections(user_id=user_id, project_id=project_id),
    }


@app.post("/rag/collections/create")
async def rag_create_collection(req: RagCollectionCreateRequest, request: Request) -> dict:
    user_id = get_request_user_id(request)
    project_id = request_project_id(request)
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Collection name cannot be empty")

    await create_collection_marker(name, user_id=user_id, project_id=project_id)
    return {"ok": True, "collection": name}


@app.post("/rag/collections/delete")
async def rag_delete_collection(req: RagCollectionDeleteRequest, request: Request) -> dict:
    user_id = get_request_user_id(request)
    project_id = request_project_id(request)
    collection_name = req.name.strip()
    if not collection_name:
        raise HTTPException(status_code=400, detail="Collection name cannot be empty")

    deleted_points = 0

    try:
        points, _ = qdrant.scroll(
            collection_name=f"{COLLECTION_NAME}__{project_id}",
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="collection",
                        match=MatchValue(value=collection_name),
                    ),
                    FieldCondition(
                        key="user_id",
                        match=MatchValue(value=user_id),
                    ),
                    FieldCondition(
                        key="project_id",
                        match=MatchValue(value=project_id),
                    ),
                ]
            ),
            limit=10000,
            with_payload=False,
            with_vectors=False,
        )

        point_ids = [p.id for p in points if p.id is not None]
        if point_ids:
            qdrant.delete(
                collection_name=f"{COLLECTION_NAME}__{project_id}",
                points_selector=point_ids,
            )
            deleted_points = len(point_ids)

        deleted_files = delete_collection_upload_dir(user_id, collection_name)

        return {
            "ok": True,
            "collection": collection_name,
            "deleted_points": deleted_points,
            "deleted_files": deleted_files,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete collection: {e}")


@app.post("/rag/upload")
async def rag_upload_document(
    request: Request,
    file: UploadFile = File(...),
    collection: str = Form(...),
    source: str = Form("qonduit_upload"),
) -> dict:
    ensure_upload_dir()

    user_id = get_request_user_id(request)
    project_id = request_project_id(request)
    collection_name = collection.strip()
    if not collection_name:
        raise HTTPException(status_code=400, detail="Collection cannot be empty")

    filename = file.filename or "uploaded_file"
    filename = sanitize_filename(filename)
    suffix = Path(filename).suffix.lower()

    if not suffix:
        raise HTTPException(status_code=400, detail="Uploaded file must have an extension")

    saved_path = build_saved_upload_path(user_id, collection_name, filename)

    try:
        with open(saved_path, "wb") as f:
            content = await file.read()
            f.write(content)

        extracted_text = extract_text_from_file(saved_path, suffix)
        if not extracted_text.strip():
            raise HTTPException(status_code=400, detail="No readable text found in file")

        chunks = chunk_text(extracted_text)
        if not chunks:
            raise HTTPException(status_code=400, detail="No text chunks generated from file")

        chunk_ids: list[str] = []
        for idx, chunk in enumerate(chunks):
            chunk_id = await add_document(
                text=chunk,
                metadata={
                    "source": source,
                    "collection": collection_name,
                    "document_name": filename,
                    "chunk_index": idx,
                    "file_type": suffix,
                    "saved_path": saved_path,
                },
                user_id=user_id,
                collection=collection_name,
                project_id=project_id,
            )
            chunk_ids.append(chunk_id)

        return {
            "ok": True,
            "collection": collection_name,
            "document_name": filename,
            "chunks_added": len(chunk_ids),
            "saved_path": saved_path,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload document: {e}")


@app.post("/internal/webhooks/github")
async def github_webhook_stub(req: GithubWebhookStubRequest) -> dict:
    """Webhook scaffold for repo ingestion automation.

    This endpoint intentionally does not execute git/pull or ingestion directly.
    It returns a deterministic contract that operators can wire to CI/job runners.
    """
    project_id = sanitize_identifier(req.project_id, DEFAULT_PROJECT_NAMESPACE)
    branch = (req.branch or "").strip() or "main"
    return {
        "ok": True,
        "message": "Webhook stub accepted. Trigger repo ingest job externally.",
        "contract": {
            "project_id": project_id,
            "repo_path": req.repo_path,
            "branch": branch,
            "delivery_id": req.delivery_id,
            "command": (
                "python -m app.ingest_repo "
                f"--project-id {project_id} "
                f"--repo-path {req.repo_path} "
                f"--branch {branch}"
            ),
        },
    }


@app.get("/v1/chat/completions")
@app.get("/chat/completions")
async def chat_completions_help() -> dict:
    return {
        "ok": True,
        "message": "Use POST /v1/chat/completions (or /chat/completions) with a JSON body.",
    }


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat(req: GatewayChatRequest, request: Request) -> Any:
    perf = PerfTracker(logger=logger)
    perf.mark("request_received", model=req.model, stream=req.stream)
    upstream_status_code: int | None = None
    upstream_usage: dict[str, Any] | None = None
    upstream_timings: dict[str, Any] | None = None
    llama_first_chunk_ms: float | None = None
    first_llama_request_ns: int | None = None
    total_llama_ms = 0.0
    summary_error: str | None = None

    user_id = get_request_user_id(request)
    alias = model_alias_entry(req.model)
    endpoint_binding = endpoint_binding_entry(request)
    with perf.step("memory_parse"):
        effective_model = resolve_effective_model(req, request)
        project_id = resolve_project_id(req, request)
        conversation_id = resolve_conversation_id(req, request)
        state = load_conversation(conversation_id, project_id=project_id)
        context_size = resolve_context_size(req, state)
        mode = resolve_mode(req, request, project_id)
        system_prompt = system_prompt_for_mode(mode)
        recent_window = (
            QONDUIT_MAX_RECENT_MESSAGES_CODING
            if mode == "coding"
            else QONDUIT_MAX_RECENT_MESSAGES
        )

        prior_recent = state.get("recent_messages", [])
        summary = state.get("summary", "")

    def build_message_dict(m: ChatMessage) -> dict[str, Any]:
        """Convert ChatMessage to OpenAI-style message dict, preserving tool fields."""
        msg_dict: dict[str, Any] = {"role": m.role}
        
        # Handle content - preserve structure for tool messages, coerce others to text
        if m.role == "tool":
            # Tool messages should have string content
            if isinstance(m.content, str):
                msg_dict["content"] = m.content
            else:
                msg_dict["content"] = str(m.content) if m.content is not None else ""
            if m.tool_call_id:
                msg_dict["tool_call_id"] = m.tool_call_id
        elif m.tool_calls is not None:
            # Assistant message with tool calls - preserve structured content
            msg_dict["content"] = coerce_model_content_to_text(m.content) if m.content is not None else ""
            msg_dict["tool_calls"] = m.tool_calls
        else:
            # Regular message - coerce to text
            msg_dict["content"] = coerce_model_content_to_text(m.content) if m.content is not None else ""
        
        return msg_dict

    incoming = [build_message_dict(m) for m in req.messages]
    combined_recent = prior_recent + incoming

    trimmed_recent, _ = trim_recent_messages(
        combined_recent,
        summary=summary,
        system_prompt=system_prompt,
        context_size=context_size,
        protect_message=is_technical_message if mode == "coding" else None,
    )

    overflow_count = len(combined_recent) - len(trimmed_recent)

    if overflow_count > 0:
        older = combined_recent[:overflow_count]
        summary = await summarize_messages(effective_model, summary, older, mode=mode)

    remaining_recent = combined_recent[overflow_count:]
    trimmed_recent, prompt_tokens = trim_recent_messages(
        remaining_recent,
        summary=summary,
        system_prompt=system_prompt,
        context_size=context_size,
        protect_message=is_technical_message if mode == "coding" else None,
    )

    budget = build_budget(context_size)
    max_tokens = min(req.max_tokens, budget.reserved_output)

    latest_text = latest_user_text(req.messages)

    rag_results = []
    rag_chunks = []
    rag_namespace = (req.rag_collection or "").strip() or None

    rag_active = should_enable_rag(project_id, mode, alias=alias, binding=endpoint_binding)
    logger.info(
        "chat_rag_state conversation_id=%s project_id=%s rag_enabled=%s "
        "namespace=%s request_model=%s effective_model=%s",
        conversation_id,
        project_id,
        rag_active,
        rag_namespace or "(none)",
        req.model,
        effective_model,
    )
    if latest_text.strip() and rag_active:
        try:
            used_user_fallback = False
            rag_results = await search_documents(
                latest_text,
                limit=RAG_TOP_K,
                collection=rag_namespace,
                user_id=user_id,
                project_id=project_id,
                perf=perf,
            )
            if not rag_results:
                used_user_fallback = True
                rag_results = await search_documents(
                    latest_text,
                    limit=RAG_TOP_K,
                    collection=rag_namespace,
                    user_id=None,
                    project_id=project_id,
                    perf=perf,
                )
            with perf.step("rag_assembly"):
                rag_chunks = [
                    item["text"].strip()
                    for item in rag_results
                    if item.get("text", "").strip()
                ]
            logger.info(
                "chat_rag_retrieval conversation_id=%s project_id=%s "
                "hit_count=%s fallback_without_user=%s",
                conversation_id,
                project_id,
                len(rag_results),
                "yes" if used_user_fallback else "no",
            )
        except Exception:
            logger.exception(
                "chat_rag_retrieval_failed conversation_id=%s project_id=%s",
                conversation_id,
                project_id,
            )
            rag_results = []
            rag_chunks = []
    elif not rag_active:
        logger.info(
            "chat_rag_skipped_disabled conversation_id=%s project_id=%s",
            conversation_id,
            project_id,
        )

    rag_results = sort_rag_results_deterministically(rag_results)
    rag_chunks, rag_chunk_keys = build_bounded_rag_chunks(
        rag_results=rag_results,
        max_chunks=QONDUIT_MAX_RAG_CHUNKS,
        max_chars=QONDUIT_MAX_RAG_CHARS,
    )
    rag_context = "\n\n".join(rag_chunks)

    with perf.step("prompt_assembly"):
        selected_collections = sorted(
            {
                c.strip()
                for c in ((req.rag_collection or "").split(","))
                if c.strip()
            }
        )
        if not selected_collections:
            selected_collections = [project_id]
        collection_identity_lines = "\n".join(
            f"- {collection}" for collection in selected_collections
        )
        collection_identity_block = (
            "Selected collection identities:\n"
            f"{collection_identity_lines}"
        )

        section_messages: list[tuple[str, list[dict[str, Any]]]] = [
            (
                "system_prompt",
                [{"role": "system", "content": system_prompt}],
            ),
            (
                "gateway_control_instructions",
                [
                    {
                        "role": "system",
                        "content": (
                            "Mode and control instructions:\n"
                            "Keep answers concise, deterministic, and "
                            "actionable.\n"
                            "Use available tools only when necessary."
                        ),
                    }
                ],
            ),
            (
                "selected_collection_identities",
                [{"role": "system", "content": collection_identity_block}],
            ),
        ]

        stable_prefix_messages = [
            message
            for _, messages in section_messages
            for message in messages
        ]

        history_messages, current_user_message = (
            split_recent_history_and_current_user(trimmed_recent)
        )
        if summary.strip():
            history_messages = [
                {
                    "role": "system",
                    "content": f"Conversation summary:\n{summary.strip()}",
                },
                *history_messages,
            ]
        history_messages = history_messages[-recent_window:]

        if history_messages:
            section_messages.append(
                ("recent_conversation_history", history_messages)
            )
        if rag_context:
            section_messages.append(
                (
                    "deterministic_rag_context",
                    [
                        {
                            "role": "system",
                            "content": (
                                "Relevant retrieved knowledge:\n"
                                f"{rag_context}"
                            ),
                        }
                    ],
                )
            )
        if current_user_message is not None:
            section_messages.append(
                ("current_user_message", [current_user_message])
            )

        first_dynamic_section_name = next(
            (
                name
                for name, messages in section_messages[3:]
                if messages
            ),
            None,
        )
        prompt_section_order = [
            name for name, messages in section_messages if messages
        ]
        final_messages = [
            message
            for _, messages in section_messages
            for message in messages
        ]
        dynamic_messages = final_messages[len(stable_prefix_messages):]

        stable_prefix_text = "".join(
            coerce_model_content_to_text(m.get("content", ""))
            for m in stable_prefix_messages
        )
        stable_prefix_hash = sha256(
            stable_prefix_text.encode("utf-8")
        ).hexdigest()
        stable_prefix_est_tokens = estimate_tokens(stable_prefix_text)
        rag_context_est_tokens = estimate_tokens(rag_context)
        recent_history_est_tokens = estimate_tokens(
            "".join(
                coerce_model_content_to_text(m.get("content", ""))
                for m in history_messages
            )
        )
        dynamic_prompt_est_tokens = estimate_tokens(
            "".join(
                coerce_model_content_to_text(m.get("content", ""))
                for m in dynamic_messages
            )
        )

        final_prompt_est_tokens = estimate_tokens(
            "".join(
                coerce_model_content_to_text(m.get("content", ""))
                for m in final_messages
            )
        )

        if final_prompt_est_tokens > QONDUIT_TARGET_PROMPT_TOKENS:
            while (
                current_user_message is not None
                and len(dynamic_messages) > 1
                and final_prompt_est_tokens > QONDUIT_TARGET_PROMPT_TOKENS
            ):
                dynamic_messages.pop(0)
                final_messages = stable_prefix_messages + dynamic_messages
                final_prompt_est_tokens = estimate_tokens(
                    "".join(
                        coerce_model_content_to_text(m.get("content", ""))
                        for m in final_messages
                    )
                )
            dynamic_prompt_est_tokens = estimate_tokens(
                "".join(
                    coerce_model_content_to_text(m.get("content", ""))
                    for m in dynamic_messages
                )
            )
            recent_history_est_tokens = estimate_tokens(
                "".join(
                    coerce_model_content_to_text(m.get("content", ""))
                    for m in dynamic_messages[:-1]
                )
            )
    logger.info(
        "chat_rag_injection conversation_id=%s project_id=%s injected=%s snippets=%s",
        conversation_id,
        project_id,
        bool(rag_context.strip()),
        len(rag_chunks),
    )

    input_message_count = len(req.messages)
    input_chars = sum(
        len(coerce_model_content_to_text(message.content))
        for message in req.messages
    )
    approx_input_tokens = approx_tokens_from_chars(input_chars)
    prompt_chars = sum(
        len(coerce_model_content_to_text(message.get("content", "")))
        for message in final_messages
    )
    approx_final_prompt_tokens = approx_tokens_from_chars(prompt_chars)
    prompt_perf_fields = {
        "stable_prefix_est_tokens": stable_prefix_est_tokens,
        "dynamic_prompt_est_tokens": dynamic_prompt_est_tokens,
        "rag_context_est_tokens": rag_context_est_tokens,
        "recent_history_est_tokens": recent_history_est_tokens,
        "final_prompt_est_tokens": final_prompt_est_tokens,
        "stable_prefix_hash": stable_prefix_hash,
        "first_dynamic_section_name": first_dynamic_section_name,
        "prompt_section_order": prompt_section_order,
        "rag_chunk_ids_or_stable_keys": rag_chunk_keys,
    }

    state["summary"] = summary
    state["project_id"] = project_id
    state["conversation_id"] = conversation_id
    state["recent_messages"] = trimmed_recent[-recent_window:]
    state["last_model"] = effective_model
    state["last_context_size"] = context_size
    state["last_mode"] = mode
    state["last_prompt_tokens"] = prompt_tokens
    state["last_reserved_output"] = budget.reserved_output
    state["metadata"] = {
        "mode": mode,
        "project_id": project_id,
        "rag_collection": (req.rag_collection or "").strip() or project_id,
        "rag_enabled": rag_active,
        "request_model": req.model,
        "effective_model": effective_model,
    }
    save_conversation(conversation_id, state, project_id=project_id)

    # Build payload for upstream - include tools if provided
    payload: dict[str, Any] = {
        "model": effective_model,
        "messages": final_messages,
        "max_tokens": max_tokens,
        "temperature": req.temperature,
        "stream": req.stream,
    }
    
    # Add tools and tool_choice if present in request.
    # In coding mode, provide a safe default read-only tool set when none is
    # provided to improve project grounding in agentic workflows.
    if req.tools is not None:
        payload["tools"] = [tool.model_dump() for tool in req.tools]
    elif mode == "coding":
        payload["tools"] = DEFAULT_CODING_TOOLS
        payload["tool_choice"] = "auto"
    if req.tool_choice is not None:
        payload["tool_choice"] = req.tool_choice

    if req.stream:
        async def stream_event_source() -> Any:
            nonlocal upstream_status_code, upstream_usage, upstream_timings
            nonlocal llama_first_chunk_ms, first_llama_request_ns, total_llama_ms
            nonlocal summary_error
            stream_payload = dict(payload)
            stream_payload["stream"] = True

            gateway_prepare_ms = (time.perf_counter_ns() - perf.start_ns) / 1_000_000
            stream_open_start_ns = time.perf_counter_ns()
            first_llama_request_ns = stream_open_start_ns
            stream_open_end_ns: int | None = None
            upstream_stream_open_ms: float | None = None
            first_byte_ms: float | None = None
            upstream_first_byte_ms: float | None = None
            client_visible_ttft_ms: float | None = None
            upstream_first_content_ms: float | None = None
            stream_chunks = 0
            stream_bytes = 0
            emitted_chars = 0
            assistant_parts: list[str] = []
            sent_done = False

            timeout = httpx.Timeout(
                connect=UPSTREAM_CONNECT_TIMEOUT_SECONDS,
                read=None,
                write=UPSTREAM_WRITE_TIMEOUT_SECONDS,
                pool=UPSTREAM_POOL_TIMEOUT_SECONDS,
            )

            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    with perf.step("llama_upstream_request"):
                        async with client.stream(
                            "POST",
                            f"{LLAMA_BASE}/v1/chat/completions",
                            json=stream_payload,
                        ) as upstream_response:
                            stream_open_end_ns = time.perf_counter_ns()
                            upstream_stream_open_ms = (
                                stream_open_end_ns - stream_open_start_ns
                            ) / 1_000_000
                            upstream_status_code = upstream_response.status_code

                            if upstream_response.status_code >= 400:
                                summary_error = (
                                    f"upstream_status_{upstream_response.status_code}"
                                )
                                yield sse_chunk(
                                    req.model,
                                    content=(
                                        "Upstream error "
                                        f"({upstream_response.status_code})"
                                    ),
                                )
                                yield sse_chunk(req.model, finish_reason="stop")
                                yield "data: [DONE]\n\n"
                                sent_done = True
                                return

                            upstream_lines = upstream_response.aiter_lines()
                            while True:
                                try:
                                    line = await asyncio.wait_for(
                                        anext(upstream_lines),
                                        timeout=STREAM_KEEPALIVE_INTERVAL_SECONDS,
                                    )
                                except asyncio.TimeoutError:
                                    yield ": keep-alive\n\n"
                                    continue
                                except StopAsyncIteration:
                                    break

                                if not line or not line.startswith("data:"):
                                    continue

                                data_line = line[5:].strip()
                                if not data_line:
                                    continue

                                now_ns = time.perf_counter_ns()
                                if first_byte_ms is None:
                                    first_byte_ms = (
                                        now_ns - perf.start_ns
                                    ) / 1_000_000
                                    if stream_open_end_ns is not None:
                                        upstream_first_byte_ms = (
                                            now_ns - stream_open_end_ns
                                        ) / 1_000_000

                                if data_line == "[DONE]":
                                    yield "data: [DONE]\n\n"
                                    sent_done = True
                                    break

                                stream_chunks += 1
                                formatted = f"data: {data_line}\n\n"
                                stream_bytes += len(
                                    formatted.encode("utf-8", errors="ignore")
                                )
                                yield formatted

                                try:
                                    parsed = json.loads(data_line)
                                except json.JSONDecodeError:
                                    continue

                                if isinstance(parsed.get("usage"), dict):
                                    upstream_usage = parsed["usage"]
                                if isinstance(parsed.get("timings"), dict):
                                    upstream_timings = parsed["timings"]

                                choices = parsed.get("choices", [])
                                if not choices:
                                    continue

                                delta = choices[0].get("delta", {})
                                text_delta = coerce_model_content_to_text(
                                    delta.get("content", ""),
                                )
                                if not text_delta:
                                    continue

                                emitted_chars += len(text_delta)
                                assistant_parts.append(text_delta)

                                if client_visible_ttft_ms is None:
                                    content_ns = time.perf_counter_ns()
                                    client_visible_ttft_ms = (
                                        content_ns - perf.start_ns
                                    ) / 1_000_000
                                    if stream_open_end_ns is not None:
                                        upstream_first_content_ms = (
                                            content_ns - stream_open_end_ns
                                        ) / 1_000_000
                                    llama_first_chunk_ms = (
                                        upstream_first_content_ms
                                        if upstream_first_content_ms is not None
                                        else None
                                    )
                                    perf.mark(
                                        "llama_first_chunk",
                                        chunk_index=stream_chunks,
                                    )
                                    if llama_first_chunk_ms is not None:
                                        perf.add_step_ms(
                                            "llama_first_chunk",
                                            llama_first_chunk_ms,
                                        )
            except Exception as e:
                summary_error = str(e)
                logger.exception(
                    "stream_request_failed conversation_id=%s model=%s error=%s",
                    conversation_id,
                    req.model,
                    str(e),
                )
                yield sse_chunk(req.model, content=f"Gateway streaming error: {str(e)}")
                yield sse_chunk(req.model, finish_reason="stop")
                yield "data: [DONE]\n\n"
                sent_done = True
            finally:
                stream_end_ns = time.perf_counter_ns()
                llama_total_ms = (
                    stream_end_ns - stream_open_start_ns
                ) / 1_000_000
                total_llama_ms += llama_total_ms
                perf.add_step_ms("llama_response_complete", llama_total_ms)
                if not sent_done:
                    yield sse_chunk(req.model, finish_reason="stop")
                    yield "data: [DONE]\n\n"

                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": "".join(assistant_parts),
                }
                state["summary"] = summary
                state["project_id"] = project_id
                state["conversation_id"] = conversation_id
                state["recent_messages"] = (
                    trimmed_recent + [assistant_message]
                )[-recent_window:]
                state["last_model"] = effective_model
                state["last_context_size"] = context_size
                state["last_mode"] = mode
                state["last_prompt_tokens"] = prompt_tokens
                state["last_reserved_output"] = budget.reserved_output
                state["metadata"] = {
                    "mode": mode,
                    "project_id": project_id,
                    "rag_collection": (req.rag_collection or "").strip()
                    or project_id,
                    "rag_enabled": rag_active,
                    "request_model": req.model,
                    "effective_model": effective_model,
                }
                save_conversation(conversation_id, state, project_id=project_id)

                perf.mark(
                    "llama_response_complete",
                    chunks=stream_chunks,
                    bytes=stream_bytes,
                )
                perf.summary(
                    model=effective_model,
                    stream=True,
                    input_message_count=input_message_count,
                    approx_input_tokens=approx_input_tokens,
                    approx_final_prompt_tokens=approx_final_prompt_tokens,
                    **prompt_perf_fields,
                    rag_chunk_count=len(rag_chunks),
                    qdrant_result_count=len(rag_results),
                    gateway_prepare_ms=round(gateway_prepare_ms, 3),
                    gateway_pre_llama_ms=round(gateway_prepare_ms, 3),
                    upstream_stream_open_ms=(
                        round(upstream_stream_open_ms, 3)
                        if upstream_stream_open_ms is not None
                        else None
                    ),
                    upstream_response_open_ms=(
                        round(upstream_stream_open_ms, 3)
                        if upstream_stream_open_ms is not None
                        else None
                    ),
                    first_byte_ms=(
                        round(first_byte_ms, 3) if first_byte_ms is not None else None
                    ),
                    upstream_first_byte_ms=(
                        round(upstream_first_byte_ms, 3)
                        if upstream_first_byte_ms is not None
                        else None
                    ),
                    client_visible_ttft_ms=(
                        round(client_visible_ttft_ms, 3)
                        if client_visible_ttft_ms is not None
                        else None
                    ),
                    upstream_first_content_ms=(
                        round(upstream_first_content_ms, 3)
                        if upstream_first_content_ms is not None
                        else None
                    ),
                    upstream_first_chunk_after_open_ms=(
                        round(upstream_first_content_ms, 3)
                        if upstream_first_content_ms is not None
                        else None
                    ),
                    llama_first_chunk_ms=(
                        round(llama_first_chunk_ms, 3)
                        if llama_first_chunk_ms is not None
                        else None
                    ),
                    llama_total_ms=round(llama_total_ms, 3),
                    upstream_status_code=upstream_status_code,
                    upstream_usage=upstream_usage,
                    upstream_timings=upstream_timings,
                    stream_chunks=stream_chunks,
                    stream_bytes=stream_bytes,
                    error=summary_error,
                )

        logger.info(
            "chat_request stream=true conversation_id=%s model=%s messages=%s",
            conversation_id,
            effective_model,
            len(req.messages),
        )
        return StreamingResponse(
            stream_event_source(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    # Tool execution loop constants
    MAX_TOOL_ITERATIONS = 5
    tool_iteration = 0
    working_messages = list(final_messages)  # Copy for tool loop
    
    # Helper function to call upstream model
    async def call_upstream_model(messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Call upstream model and return parsed response data."""
        nonlocal upstream_status_code, upstream_usage, upstream_timings
        nonlocal first_llama_request_ns, total_llama_ms
        call_payload = dict(payload)
        call_payload["messages"] = messages
        call_payload["stream"] = False

        if first_llama_request_ns is None:
            first_llama_request_ns = time.perf_counter_ns()
        llama_call_started_ns = time.perf_counter_ns()
        with perf.step("llama_upstream_request"):
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(
                    f"{LLAMA_BASE}/v1/chat/completions",
                    json=call_payload,
                )
        total_llama_ms += (time.perf_counter_ns() - llama_call_started_ns) / 1_000_000
        upstream_status_code = r.status_code
        
        if r.status_code >= 400:
            logger.error(
                "tool_loop_upstream_error conversation_id=%s model=%s status=%s iteration=%s",
                conversation_id,
                effective_model,
                r.status_code,
                tool_iteration,
            )
            raise HTTPException(
                status_code=502,
                detail=f"Upstream error ({r.status_code}): {r.text[:500]}",
            )
        
        try:
            parsed = r.json()
            if isinstance(parsed, dict):
                maybe_usage = parsed.get("usage")
                if isinstance(maybe_usage, dict):
                    upstream_usage = maybe_usage

                timings: dict[str, Any] = {}
                for key in (
                    "timings",
                    "prompt_eval",
                    "prompt_ms",
                    "prompt_n",
                    "predicted_ms",
                    "predicted_n",
                    "eval_ms",
                    "eval_count",
                ):
                    if key in parsed:
                        timings[key] = parsed.get(key)
                if isinstance(parsed.get("usage"), dict):
                    usage = parsed["usage"]
                    for key in (
                        "prompt_eval_count",
                        "prompt_eval_duration",
                        "eval_count",
                        "eval_duration",
                    ):
                        if key in usage:
                            timings[f"usage_{key}"] = usage.get(key)
                if timings:
                    upstream_timings = timings
            return parsed
        except ValueError:
            logger.error(
                "tool_loop_upstream_invalid_json conversation_id=%s model=%s iteration=%s",
                conversation_id,
                effective_model,
                tool_iteration,
            )
            raise HTTPException(
                status_code=502,
                detail="Upstream returned invalid JSON",
            )
    
    # Tool execution loop
    last_response_data: dict[str, Any] | None = None
    seen_tool_signatures: dict[str, int] = {}

    try:
        while tool_iteration <= MAX_TOOL_ITERATIONS:
            logger.info(
                "tool_loop_iteration_start conversation_id=%s iteration=%s max=%s has_tools=%s",
                conversation_id,
                tool_iteration,
                MAX_TOOL_ITERATIONS,
                bool(req.tools or mode == "coding"),
            )

            response_data = await call_upstream_model(working_messages)
            last_response_data = response_data

            choices = response_data.get("choices", [])
            if not choices:
                logger.error(
                    "tool_loop_no_choices conversation_id=%s iteration=%s response=%s",
                    conversation_id,
                    tool_iteration,
                    str(response_data)[:500],
                )
                break

            message_data = choices[0].get("message", {})
            finish_reason = choices[0].get("finish_reason", "stop")
            tool_calls = message_data.get("tool_calls")
            has_tool_calls = isinstance(tool_calls, list) and len(tool_calls) > 0

            logger.info(
                "tool_loop_model_response conversation_id=%s iteration=%s finish_reason=%s has_tool_calls=%s",
                conversation_id,
                tool_iteration,
                finish_reason,
                has_tool_calls,
            )

            if has_tool_calls:
                if tool_iteration >= MAX_TOOL_ITERATIONS:
                    logger.error(
                        "tool_loop_max_iterations_reached conversation_id=%s iteration=%s",
                        conversation_id,
                        tool_iteration,
                    )
                    last_response_data = {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": (
                                        "Tool execution stopped after reaching max "
                                        "iterations. Please refine the request."
                                    ),
                                },
                                "finish_reason": "stop",
                            }
                        ]
                    }
                    break

                logger.info(
                    "tool_loop_tool_calls_detected conversation_id=%s iteration=%s tool_count=%s",
                    conversation_id,
                    tool_iteration,
                    len(tool_calls),
                )

                assistant_tool_message = {
                    "role": "assistant",
                    "content": coerce_model_content_to_text(
                        message_data.get("content")
                    ) if message_data.get("content") is not None else "",
                    "tool_calls": tool_calls,
                }
                working_messages.append(assistant_tool_message)

                tool_results: list[ChatMessage] = []
                had_repeat_call = False
                repeated_calls_count = 0
                for tc in tool_calls:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "unknown")
                    tool_call_id = tc.get("id", f"call_{uuid.uuid4().hex[:8]}")
                    raw_arguments = func.get("arguments", "{}")
                    args_hash = sha256(str(raw_arguments).encode("utf-8")).hexdigest()[:12]

                    try:
                        tool_args = json.loads(raw_arguments)
                    except (json.JSONDecodeError, TypeError):
                        tool_args = {
                            "_tool_call_id": tool_call_id,
                        }
                        signature = f"{tool_name}:{args_hash}"
                        result = ToolResult(
                            tool_call_id=tool_call_id,
                            name=tool_name,
                            content=json.dumps(
                                {
                                    "ok": False,
                                    "status": "error",
                                    "applied": False,
                                    "error": "invalid_tool_arguments",
                                    "tool": tool_name,
                                    "args_hash": args_hash,
                                    "raw_arguments": str(raw_arguments)[:500],
                                    "files_changed": [],
                                    "summary": (
                                        "Tool arguments were invalid JSON. "
                                        "Fix arguments before retrying."
                                    ),
                                },
                                indent=2,
                            ),
                            is_error=True,
                        )
                        logger.warning(
                            "tool_loop_invalid_arguments conversation_id=%s iteration=%s tool=%s args_hash=%s",
                            conversation_id,
                            tool_iteration,
                            tool_name,
                            args_hash,
                        )
                    else:
                        if not isinstance(tool_args, dict):
                            tool_args = {"value": tool_args}
                        signature = (
                            f"{tool_name}:" + sha256(
                                json.dumps(tool_args, sort_keys=True).encode("utf-8")
                            ).hexdigest()[:12]
                        )
                        repeat_count = seen_tool_signatures.get(signature, 0)
                        is_repeat = repeat_count > 0
                        if is_repeat:
                            had_repeat_call = True
                            repeated_calls_count += 1
                            result = ToolResult(
                                tool_call_id=tool_call_id,
                                name=tool_name,
                                content=json.dumps(
                                    {
                                        "ok": False,
                                        "status": "blocked_repeat",
                                        "applied": False,
                                        "error": "repeated_tool_call",
                                        "tool": tool_name,
                                        "args_hash": signature.split(":", 1)[1],
                                        "repeat_count": repeat_count,
                                        "files_changed": [],
                                        "summary": (
                                            "Skipped repeated invocation with "
                                            "identical arguments to prevent "
                                            "pointless loops."
                                        ),
                                    },
                                    indent=2,
                                ),
                                is_error=True,
                            )
                            logger.warning(
                                "tool_loop_repeated_call conversation_id=%s iteration=%s tool=%s signature=%s repeat_count=%s",
                                conversation_id,
                                tool_iteration,
                                tool_name,
                                signature,
                                repeat_count,
                            )
                        else:
                            seen_tool_signatures[signature] = repeat_count + 1
                            tool_args["_tool_call_id"] = tool_call_id
                            logger.info(
                                "tool_loop_executing_tool conversation_id=%s iteration=%s tool=%s tool_call_id=%s signature=%s is_repeat=%s",
                                conversation_id,
                                tool_iteration,
                                tool_name,
                                tool_call_id,
                                signature,
                                is_repeat,
                            )
                            result = await execute_tool(
                                tool_name=tool_name,
                                tool_args=tool_args,
                                project_id=project_id,
                                user_id=user_id,
                            )

                    tool_msg = ChatMessage(
                        role="tool",
                        content=result.content,
                        tool_call_id=tool_call_id,
                    )
                    tool_results.append(tool_msg)

                    logger.info(
                        "tool_loop_tool_executed conversation_id=%s iteration=%s tool=%s tool_call_id=%s signature=%s is_error=%s",
                        conversation_id,
                        tool_iteration,
                        tool_name,
                        tool_call_id,
                        signature,
                        result.is_error,
                    )

                working_messages.extend(
                    [
                        {
                            "role": m.role,
                            "content": m.content,
                            "tool_call_id": m.tool_call_id,
                        }
                        for m in tool_results
                    ]
                )

                if repeated_calls_count == len(tool_calls):
                    logger.warning(
                        "tool_loop_exiting conversation_id=%s iteration=%s reason=%s",
                        conversation_id,
                        tool_iteration,
                        "all_tool_calls_repeated",
                    )
                    last_response_data = {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": (
                                        "Tools were already run with these exact "
                                        "arguments. I will stop tool calls now and "
                                        "summarize the latest successful results."
                                    ),
                                },
                                "finish_reason": "stop",
                            }
                        ]
                    }
                    break

                working_messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Tool results are now available. If the task is "
                            "satisfied, provide a final assistant answer and stop "
                            "calling tools. Only call another tool if a prior tool "
                            "failed or additional data is required."
                        ),
                    }
                )

                tool_iteration += 1
                logger.info(
                    "tool_loop_followup_model_call conversation_id=%s next_iteration=%s repeated_call_detected=%s continue_reason=%s",
                    conversation_id,
                    tool_iteration,
                    had_repeat_call,
                    "tool_calls_detected",
                )
                continue

            logger.info(
                "tool_loop_exiting conversation_id=%s iteration=%s reason=final_answer",
                conversation_id,
                tool_iteration,
                "model_returned_final_answer",
            )
            break
    except Exception as error:
        summary_error = str(error)
        perf.summary(
            model=effective_model,
            stream=req.stream,
            input_message_count=input_message_count,
            approx_input_tokens=approx_input_tokens,
            approx_final_prompt_tokens=approx_final_prompt_tokens,
            **prompt_perf_fields,
            rag_chunk_count=len(rag_chunks),
            qdrant_result_count=len(rag_results),
            gateway_pre_llama_ms=round(
                ((first_llama_request_ns or perf.start_ns) - perf.start_ns)
                / 1_000_000,
                3,
            ),
            llama_total_ms=round(total_llama_ms, 3),
            upstream_status_code=upstream_status_code,
            upstream_usage=upstream_usage,
            upstream_timings=upstream_timings,
            error=summary_error,
        )
        raise

    # Use last_response_data for final response, or make one final call if needed
    if last_response_data is None:
        # This shouldn't happen, but handle gracefully
        logger.error("tool_loop_no_response conversation_id=%s", conversation_id)
        raise HTTPException(status_code=502, detail="No response from upstream model")
    
    # Update working_messages in payload for state saving
    payload["messages"] = working_messages

    async def event_stream():
        nonlocal upstream_status_code, upstream_usage, upstream_timings
        nonlocal llama_first_chunk_ms, first_llama_request_ns, total_llama_ms
        nonlocal summary_error
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        if first_llama_request_ns is None:
            first_llama_request_ns = time.perf_counter_ns()
        stream_start_ns = time.perf_counter_ns()
        stream_upstream_open_started_ns = stream_start_ns
        stream_upstream_open_completed_ns: int | None = None
        client_visible_ttft_ms: float | None = None
        upstream_response_open_ms: float | None = None
        upstream_first_chunk_after_open_ms: float | None = None
        stream_llama_total_ms = 0.0
        emitted_chunks = 0
        emitted_chars = 0
        emitted_bytes = 0
        assistant_parts: list[str] = []
        sent_done = False
        timeout = httpx.Timeout(
            connect=UPSTREAM_CONNECT_TIMEOUT_SECONDS,
            read=None,
            write=UPSTREAM_WRITE_TIMEOUT_SECONDS,
            pool=UPSTREAM_POOL_TIMEOUT_SECONDS,
        )

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                with perf.step("llama_upstream_request"):
                    stream_ctx = client.stream(
                        "POST",
                        f"{LLAMA_BASE}/v1/chat/completions",
                        json=stream_payload,
                    )
                    r = await stream_ctx.__aenter__()
                    stream_upstream_open_completed_ns = time.perf_counter_ns()
                    upstream_response_open_ms = (
                        stream_upstream_open_completed_ns
                        - stream_upstream_open_started_ns
                    ) / 1_000_000
                try:
                    upstream_status_code = r.status_code
                    upstream_ms = int(
                        (time.perf_counter_ns() - stream_start_ns) / 1_000_000
                    )
                    logger.info(
                        "stream_upstream_response conversation_id=%s model=%s "
                        "status=%s latency_ms=%s",
                        conversation_id,
                        req.model,
                        r.status_code,
                        upstream_ms,
                    )

                    if r.status_code >= 400:
                        upstream_error = await r.aread()
                        error_text = upstream_error.decode(
                            "utf-8",
                            errors="replace",
                        )
                        yield sse_chunk(
                            req.model,
                            content=f"Upstream error ({r.status_code}): "
                            f"{error_text}",
                        )
                        yield sse_chunk(req.model, finish_reason="stop")
                        yield "data: [DONE]\n\n"
                        return

                    upstream_lines = r.aiter_lines()
                    while True:
                        try:
                            line = await asyncio.wait_for(
                                anext(upstream_lines),
                                timeout=STREAM_KEEPALIVE_INTERVAL_SECONDS,
                            )
                        except asyncio.TimeoutError:
                            yield ": keep-alive\n\n"
                            continue
                        except StopAsyncIteration:
                            break

                        if not line:
                            continue
                        if not line.startswith("data:"):
                            continue

                        data_line = line[5:].strip()
                        if not data_line:
                            continue

                        if data_line == "[DONE]":
                            sent_done = True
                            yield "data: [DONE]\n\n"
                            break

                        emitted_chunks += 1
                        emitted_bytes += len(data_line.encode("utf-8", errors="ignore"))
                        if llama_first_chunk_ms is None:
                            first_chunk_seen_ns = time.perf_counter_ns()
                            llama_first_chunk_ms = (
                                first_chunk_seen_ns - stream_start_ns
                            ) / 1_000_000
                            client_visible_ttft_ms = (
                                first_chunk_seen_ns - perf.start_ns
                            ) / 1_000_000
                            if stream_upstream_open_completed_ns is not None:
                                upstream_first_chunk_after_open_ms = (
                                    first_chunk_seen_ns
                                    - stream_upstream_open_completed_ns
                                ) / 1_000_000
                            perf.mark(
                                "llama_first_chunk",
                                chunk_index=emitted_chunks,
                            )
                            perf.add_step_ms("llama_first_chunk", llama_first_chunk_ms)
                        yield f"data: {data_line}\n\n"

                        try:
                            parsed = json.loads(data_line)
                        except json.JSONDecodeError:
                            continue

                        choices = parsed.get("choices", [])
                        if not choices:
                            if isinstance(parsed.get("usage"), dict):
                                upstream_usage = parsed["usage"]
                            if isinstance(parsed.get("timings"), dict):
                                upstream_timings = parsed["timings"]
                            continue

                        delta = choices[0].get("delta", {})
                        text_delta = coerce_model_content_to_text(
                            delta.get("content", ""),
                        )
                        if text_delta:
                            emitted_chars += len(text_delta)
                            assistant_parts.append(text_delta)
                        if isinstance(parsed.get("usage"), dict):
                            upstream_usage = parsed["usage"]
                        if isinstance(parsed.get("timings"), dict):
                            upstream_timings = parsed["timings"]

                    if not sent_done:
                        yield sse_chunk(req.model, finish_reason="stop")
                        yield "data: [DONE]\n\n"
                finally:
                    await stream_ctx.__aexit__(None, None, None)
        except Exception as e:
            summary_error = str(e)
            logger.exception(
                "stream_request_failed conversation_id=%s model=%s error=%s",
                conversation_id,
                req.model,
                str(e),
            )
            yield sse_chunk(req.model, content=f"Gateway streaming error: {str(e)}")
            yield sse_chunk(req.model, finish_reason="stop")
            yield "data: [DONE]\n\n"
            stream_llama_total_ms = (
                time.perf_counter_ns() - stream_start_ns
            ) / 1_000_000
            total_llama_ms += stream_llama_total_ms
            perf.add_step_ms(
                "llama_response_complete",
                stream_llama_total_ms,
            )
            perf.summary(
                model=effective_model,
                stream=True,
                input_message_count=input_message_count,
                approx_input_tokens=approx_input_tokens,
                approx_final_prompt_tokens=approx_final_prompt_tokens,
                **prompt_perf_fields,
                rag_chunk_count=len(rag_chunks),
                qdrant_result_count=len(rag_results),
                gateway_pre_llama_ms=round(
                    (stream_upstream_open_started_ns - perf.start_ns)
                    / 1_000_000,
                    3,
                ),
                client_visible_ttft_ms=(
                    round(client_visible_ttft_ms, 3)
                    if client_visible_ttft_ms is not None
                    else None
                ),
                upstream_response_open_ms=(
                    round(upstream_response_open_ms, 3)
                    if upstream_response_open_ms is not None
                    else None
                ),
                upstream_first_chunk_after_open_ms=(
                    round(upstream_first_chunk_after_open_ms, 3)
                    if upstream_first_chunk_after_open_ms is not None
                    else None
                ),
                llama_first_chunk_ms=(
                    round(llama_first_chunk_ms, 3)
                    if llama_first_chunk_ms is not None
                    else None
                ),
                llama_total_ms=round(stream_llama_total_ms, 3),
                upstream_status_code=upstream_status_code,
                upstream_usage=upstream_usage,
                upstream_timings=upstream_timings,
                error=summary_error,
            )
            return

        # Collect both content and tool_calls from streaming response
        assistant_content = "".join(assistant_parts)
        collected_tool_calls: list[dict[str, Any]] = []
        
        logger.info(
            "stream_complete conversation_id=%s model=%s chunks=%s chars=%s",
            conversation_id,
            req.model,
            emitted_chunks,
            emitted_chars,
        )
        stream_llama_total_ms = (time.perf_counter_ns() - stream_start_ns) / 1_000_000
        total_llama_ms += stream_llama_total_ms
        perf.add_step_ms(
            "llama_response_complete",
            stream_llama_total_ms,
        )
        perf.mark(
            "llama_response_complete",
            chunks=emitted_chunks,
            bytes=emitted_bytes,
        )

        # Build assistant message - preserve tool_calls if present
        assistant_message: dict[str, Any] = {"role": "assistant", "content": assistant_content}
        if collected_tool_calls:
            assistant_message["tool_calls"] = collected_tool_calls

        state["summary"] = summary
        state["project_id"] = project_id
        state["conversation_id"] = conversation_id
        state["recent_messages"] = (trimmed_recent + [assistant_message])[-recent_window:]
        state["last_model"] = effective_model
        state["last_context_size"] = context_size
        state["last_mode"] = mode
        state["last_prompt_tokens"] = prompt_tokens
        state["last_reserved_output"] = budget.reserved_output
        state["metadata"] = {
            "mode": mode,
            "project_id": project_id,
            "rag_collection": (req.rag_collection or "").strip() or project_id,
            "rag_enabled": rag_active,
            "request_model": req.model,
            "effective_model": effective_model,
        }
        save_conversation(conversation_id, state, project_id=project_id)
        perf.summary(
            model=effective_model,
            stream=True,
            input_message_count=input_message_count,
            approx_input_tokens=approx_input_tokens,
            approx_final_prompt_tokens=approx_final_prompt_tokens,
            **prompt_perf_fields,
            rag_chunk_count=len(rag_chunks),
            qdrant_result_count=len(rag_results),
            gateway_pre_llama_ms=round(
                (stream_upstream_open_started_ns - perf.start_ns)
                / 1_000_000,
                3,
            ),
            client_visible_ttft_ms=(
                round(client_visible_ttft_ms, 3)
                if client_visible_ttft_ms is not None
                else None
            ),
            upstream_response_open_ms=(
                round(upstream_response_open_ms, 3)
                if upstream_response_open_ms is not None
                else None
            ),
            upstream_first_chunk_after_open_ms=(
                round(upstream_first_chunk_after_open_ms, 3)
                if upstream_first_chunk_after_open_ms is not None
                else None
            ),
            llama_first_chunk_ms=(
                round(llama_first_chunk_ms, 3)
                if llama_first_chunk_ms is not None
                else None
            ),
            llama_total_ms=round(stream_llama_total_ms, 3),
            upstream_status_code=upstream_status_code,
            upstream_usage=upstream_usage,
            upstream_timings=upstream_timings,
            stream_chunks=emitted_chunks,
            stream_bytes=emitted_bytes,
            error=summary_error,
        )

    if req.stream:
        logger.info(
            "chat_request stream=true conversation_id=%s model=%s messages=%s",
            conversation_id,
            effective_model,
            len(req.messages),
        )
        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    # For non-streaming, we already have the final response from the tool loop
    # Use last_response_data which contains the final model response
    data = last_response_data
    
    non_stream_start = time.perf_counter()  # Track start time for logging (already elapsed during tool loop)
    non_stream_ms = int((time.perf_counter() - non_stream_start) * 1000)
    logger.info(
        "chat_request stream=false conversation_id=%s model=%s latency_ms=%s messages=%s tool_iterations=%s",
        conversation_id,
        effective_model,
        non_stream_ms,
        len(req.messages),
        tool_iteration,
    )
    perf.mark("llama_response_complete")
    perf.add_step_ms("llama_response_complete", round(total_llama_ms, 3))

    message_data = data.get("choices", [{}])[0].get("message", {})
    message_content = message_data.get("content", "")
    assistant_content = coerce_model_content_to_text(message_content) if message_content else ""
    
    # Build assistant message - preserve tool_calls if present in upstream response
    assistant_message: dict[str, Any] = {"role": "assistant"}
    if message_content:
        assistant_message["content"] = assistant_content
    else:
        assistant_message["content"] = ""
    
    # Pass through tool_calls from upstream if present
    tool_calls = message_data.get("tool_calls")
    if tool_calls:
        assistant_message["tool_calls"] = tool_calls

    state["summary"] = summary
    state["project_id"] = project_id
    state["conversation_id"] = conversation_id
    state["recent_messages"] = (trimmed_recent + [assistant_message])[-recent_window:]
    state["last_model"] = effective_model
    state["last_context_size"] = context_size
    state["last_mode"] = mode
    state["last_prompt_tokens"] = prompt_tokens
    state["last_reserved_output"] = budget.reserved_output
    state["metadata"] = {
        "mode": mode,
        "project_id": project_id,
        "rag_collection": (req.rag_collection or "").strip() or project_id,
        "rag_enabled": rag_active,
        "request_model": req.model,
        "effective_model": effective_model,
    }
    save_conversation(conversation_id, state, project_id=project_id)
    perf.summary(
        model=effective_model,
        stream=False,
        input_message_count=input_message_count,
        approx_input_tokens=approx_input_tokens,
        approx_final_prompt_tokens=approx_final_prompt_tokens,
        **prompt_perf_fields,
        rag_chunk_count=len(rag_chunks),
        qdrant_result_count=len(rag_results),
        gateway_pre_llama_ms=round(
            ((first_llama_request_ns or perf.start_ns) - perf.start_ns)
            / 1_000_000,
            3,
        ),
        llama_first_chunk_ms=(
            round(llama_first_chunk_ms, 3) if llama_first_chunk_ms is not None else None
        ),
        llama_total_ms=round(total_llama_ms, 3),
        upstream_status_code=upstream_status_code,
        upstream_usage=upstream_usage,
        upstream_timings=upstream_timings,
        error=summary_error,
    )

    # Build response message - include tool_calls if present
    response_message: dict[str, Any] = {"role": "assistant"}
    if message_content:
        response_message["content"] = assistant_content
    else:
        response_message["content"] = ""
    if tool_calls:
        response_message["tool_calls"] = tool_calls

    return {
        "id": data.get("id", f"chatcmpl-qonduit-{uuid.uuid4().hex}"),
        "object": "chat.completion",
        "created": data.get("created", int(time.time())),
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": response_message,
                "finish_reason": (
                    data.get("choices", [{}])[0].get("finish_reason") or "stop"
                ),
            }
        ],
        "usage": data.get("usage", {}),
    }
