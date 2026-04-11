from __future__ import annotations

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse, JSONResponse, Response
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
from typing import Any
from hashlib import sha256

from pypdf import PdfReader
from docx import Document
from openpyxl import load_workbook

from .budget import build_budget, estimate_tokens, trim_recent_messages
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

app = FastAPI(title="Qonduit Memory Gateway")
logger = logging.getLogger("qonduit.memory_gateway")
ingestion_logger = logging.getLogger("qonduit.memory_gateway.ingestion")


# Track in-flight stream requests for fallback retry
_in_flight_streams: dict[str, dict[str, Any]] = {}


def _log_stream_event(event_type: str, conversation_id: str, model: str, **kwargs: Any) -> None:
    """Log streaming events with consistent formatting."""
    extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
    logger.info(f"stream_{event_type} conversation_id={conversation_id} model={model} {extra}".strip())


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


class ChatMessage(BaseModel):
    role: str
    content: Any


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

    model_config = {"extra": "allow"}


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
    user_id = get_request_user_id(request)
    alias = model_alias_entry(req.model)
    endpoint_binding = endpoint_binding_entry(request)
    effective_model = resolve_effective_model(req, request)
    project_id = resolve_project_id(req, request)
    conversation_id = resolve_conversation_id(req, request)
    state = load_conversation(conversation_id, project_id=project_id)
    context_size = resolve_context_size(req, state)
    mode = resolve_mode(req, request, project_id)
    system_prompt = system_prompt_for_mode(mode)
    recent_window = 16 if mode == "coding" else 8

    prior_recent = state.get("recent_messages", [])
    summary = state.get("summary", "")

    incoming = [
        {
            "role": m.role,
            "content": coerce_model_content_to_text(m.content),
        }
        for m in req.messages
    ]
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
            )
            if not rag_results:
                used_user_fallback = True
                rag_results = await search_documents(
                    latest_text,
                    limit=RAG_TOP_K,
                    collection=rag_namespace,
                    user_id=None,
                    project_id=project_id,
                )
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

    rag_context = "\n\n".join(rag_chunks)

    final_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": f"Rolling summary:\n{summary or '(none)'}"},
    ]

    if rag_context:
        final_messages.append(
            {
                "role": "system",
                "content": (
                    "Relevant retrieved knowledge:\n"
                    f"{rag_context}"
                ),
            }
        )
    logger.info(
        "chat_rag_injection conversation_id=%s project_id=%s injected=%s snippets=%s",
        conversation_id,
        project_id,
        bool(rag_context.strip()),
        len(rag_chunks),
    )

    final_messages.extend(trimmed_recent)

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

    payload = {
        "model": effective_model,
        "messages": final_messages,
        "max_tokens": max_tokens,
        "temperature": req.temperature,
        "stream": req.stream,
    }

    async def event_stream():
        """Stream response from upstream with robust error handling and fallback support."""
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        stream_start = time.perf_counter()
        emitted_chunks = 0
        emitted_chars = 0
        assistant_parts: list[str] = []
        sent_done = False
        first_chunk_received = False
        timeout = httpx.Timeout(
            connect=UPSTREAM_CONNECT_TIMEOUT_SECONDS,
            read=None,
            write=UPSTREAM_WRITE_TIMEOUT_SECONDS,
            pool=UPSTREAM_POOL_TIMEOUT_SECONDS,
        )

        # Register this as an in-flight stream for potential fallback
        stream_id = str(uuid.uuid4())
        _in_flight_streams[stream_id] = {
            "conversation_id": conversation_id,
            "model": req.model,
            "start_time": stream_start,
            "chunks_received": 0,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{LLAMA_BASE}/v1/chat/completions",
                    json=stream_payload,
                ) as r:
                    upstream_ms = int((time.perf_counter() - stream_start) * 1000)
                    logger.info(
                        "stream_upstream_response conversation_id=%s model=%s "
                        "status=%s latency_ms=%s",
                        conversation_id,
                        req.model,
                        r.status_code,
                        upstream_ms,
                    )

                    if r.status_code >= 400:
                        upstream_error_content = await r.aread()
                        error_text = upstream_error_content.decode(
                            "utf-8",
                            errors="replace",
                        )
                        logger.error(
                            "stream_upstream_error_status conversation_id=%s model=%s "
                            "status=%s error=%s",
                            conversation_id,
                            req.model,
                            r.status_code,
                            error_text[:500],
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
                        # Check if downstream client disconnected
                        try:
                            disconnected = await request.is_disconnected()
                        except Exception:
                            disconnected = False
                        if disconnected:
                            logger.info(
                                "stream_downstream_disconnect conversation_id=%s model=%s "
                                "chunks_emitted=%s chars_emitted=%s",
                                conversation_id,
                                req.model,
                                emitted_chunks,
                                emitted_chars,
                            )
                            break

                        try:
                            line = await asyncio.wait_for(
                                anext(upstream_lines),
                                timeout=STREAM_KEEPALIVE_INTERVAL_SECONDS,
                            )
                        except asyncio.TimeoutError:
                            # Check again for client disconnect before sending keepalive
                            try:
                                disconnected = await request.is_disconnected()
                            except Exception:
                                disconnected = False
                            if disconnected:
                                logger.info(
                                    "stream_downstream_disconnect_keepalive conversation_id=%s model=%s",
                                    conversation_id,
                                    req.model,
                                )
                                break
                            yield ": keep-alive\n\n"
                            continue
                        except StopAsyncIteration:
                            logger.info(
                                "stream_upstream_exhausted conversation_id=%s model=%s "
                                "chunks_emitted=%s chars_emitted=%s",
                                conversation_id,
                                req.model,
                                emitted_chunks,
                                emitted_chars,
                            )
                            break
                        except httpx.ReadError as e:
                            logger.error(
                                "stream_upstream_read_error conversation_id=%s model=%s error=%s",
                                conversation_id,
                                req.model,
                                str(e),
                            )
                            break
                        except Exception as e:
                            logger.exception(
                                "stream_upstream_iter_error conversation_id=%s model=%s error=%s",
                                conversation_id,
                                req.model,
                                str(e),
                            )
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

                        # Log first chunk received
                        if not first_chunk_received:
                            first_chunk_received = True
                            time_to_first_chunk = int((time.perf_counter() - stream_start) * 1000)
                            logger.info(
                                "stream_first_chunk conversation_id=%s model=%s "
                                "time_to_first_chunk_ms=%s",
                                conversation_id,
                                req.model,
                                time_to_first_chunk,
                            )
                            _in_flight_streams[stream_id]["chunks_received"] = 1

                        emitted_chunks += 1
                        _in_flight_streams[stream_id]["chunks_received"] = emitted_chunks
                        yield f"data: {data_line}\n\n"

                        try:
                            parsed = json.loads(data_line)
                        except json.JSONDecodeError as e:
                            logger.warning(
                                "stream_chunk_parse_failed conversation_id=%s model=%s "
                                "chunk_preview=%s error=%s",
                                conversation_id,
                                req.model,
                                data_line[:100],
                                str(e),
                            )
                            continue

                        choices = parsed.get("choices", [])
                        if not choices:
                            continue

                        delta = choices[0].get("delta", {})
                        text_delta = coerce_model_content_to_text(
                            delta.get("content", ""),
                        )
                        if text_delta:
                            emitted_chars += len(text_delta)
                            assistant_parts.append(text_delta)

                    if not sent_done:
                        yield sse_chunk(req.model, finish_reason="stop")
                        yield "data: [DONE]\n\n"
        except asyncio.CancelledError:
            logger.info(
                "stream_canceled conversation_id=%s model=%s "
                "chunks_emitted=%s chars_emitted=%s",
                conversation_id,
                req.model,
                emitted_chunks,
                emitted_chars,
            )
            # Do not re-raise - let the caller handle cancellation gracefully
            # This prevents the exception from bubbling up and causing generic failures
            if not sent_done:
                yield sse_chunk(req.model, finish_reason="stop")
                yield "data: [DONE]\n\n"
            return
        except Exception as e:
            logger.exception(
                "stream_request_failed conversation_id=%s model=%s error=%s",
                conversation_id,
                req.model,
                str(e),
            )
            # Only yield error chunk if we haven't sent any data yet
            # This allows partial success to be handled gracefully
            if emitted_chunks == 0:
                yield sse_chunk(req.model, content=f"Gateway streaming error: {str(e)}")
                yield sse_chunk(req.model, finish_reason="stop")
                yield "data: [DONE]\n\n"
            # Re-raise for early failure detection and fallback triggering
            raise
        finally:
            # Clean up in-flight tracking
            _in_flight_streams.pop(stream_id, None)

        assistant_content = "".join(assistant_parts)
        logger.info(
            "stream_complete conversation_id=%s model=%s chunks=%s chars=%s",
            conversation_id,
            req.model,
            emitted_chunks,
            emitted_chars,
        )

        assistant_message = {
            "role": "assistant",
            "content": assistant_content,
        }

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

    # Helper function for non-stream fallback
    async def call_non_stream_fallback() -> Response:
        """Retry with stream=false when streaming fails early."""
        logger.info(
            "stream_fallback_nonstream_started conversation_id=%s model=%s",
            conversation_id,
            req.model,
        )
        non_stream_start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(f"{LLAMA_BASE}/v1/chat/completions", json=payload)
        except httpx.RequestError as exc:
            logger.exception(
                "stream_fallback_nonstream_connection_failed conversation_id=%s model=%s error=%s",
                conversation_id,
                req.model,
                str(exc),
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "Failed to connect to upstream chat backend (fallback)",
                    "upstream": LLAMA_BASE,
                },
            )

        if r.status_code >= 400:
            logger.error(
                "stream_fallback_nonstream_upstream_error conversation_id=%s model=%s status=%s",
                conversation_id,
                req.model,
                r.status_code,
            )
            raise upstream_error(r.status_code, r.text)

        try:
            data = r.json()
        except ValueError:
            logger.error(
                "stream_fallback_nonstream_invalid_json conversation_id=%s model=%s body=%s",
                conversation_id,
                req.model,
                r.text[:300],
            )
            raise HTTPException(
                status_code=502,
                detail="Upstream /v1/chat/completions returned invalid JSON (fallback)",
            )

        non_stream_ms = int((time.perf_counter() - non_stream_start) * 1000)
        logger.info(
            "stream_fallback_nonstream_succeeded conversation_id=%s model=%s latency_ms=%s",
            conversation_id,
            req.model,
            non_stream_ms,
        )

        message_content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        assistant_content = coerce_model_content_to_text(message_content)

        assistant_message = {"role": "assistant", "content": assistant_content}
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

        return JSONResponse(content=data)

    if req.stream:
        logger.info(
            "chat_request stream=true conversation_id=%s model=%s messages=%s",
            conversation_id,
            effective_model,
            len(req.messages),
        )

        # Track whether streaming succeeded or needs fallback
        _stream_result = {"failed_early": False, "chunks_emitted": 0, "error": None}

        async def wrapped_event_stream():
            """Wrap event_stream to detect early failures and trigger fallback."""
            chunks_emitted = 0
            try:
                async for chunk in event_stream():
                    chunks_emitted += 1
                    yield chunk
            except asyncio.CancelledError:
                _stream_result["failed_early"] = (chunks_emitted == 0)
                _stream_result["chunks_emitted"] = chunks_emitted
                raise
            except Exception as e:
                _stream_result["failed_early"] = (chunks_emitted == 0)
                _stream_result["chunks_emitted"] = chunks_emitted
                _stream_result["error"] = str(e)
                if chunks_emitted == 0:
                    logger.warning(
                        "stream_early_failure_triggering_fallback conversation_id=%s model=%s error=%s",
                        conversation_id,
                        req.model,
                        str(e),
                    )
                else:
                    logger.warning(
                        "stream_partial_failure_chunks_already_sent conversation_id=%s model=%s chunks=%s error=%s",
                        conversation_id,
                        req.model,
                        chunks_emitted,
                        str(e),
                    )
                raise

        try:
            stream_response = StreamingResponse(
                wrapped_event_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )
            return stream_response
        except Exception:
            # If streaming fails before response is sent, fall back to non-stream
            if _stream_result["failed_early"]:
                logger.info(
                    "stream_fallback_triggered_after_failure conversation_id=%s model=%s error=%s",
                    conversation_id,
                    req.model,
                    _stream_result.get("error", "unknown"),
                )
                return await call_non_stream_fallback()
            raise

    non_stream_start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(f"{LLAMA_BASE}/v1/chat/completions", json=payload)
    except httpx.RequestError as exc:
        logger.exception(
            "chat_upstream_connection_failed conversation_id=%s model=%s error=%s",
            conversation_id,
            effective_model,
            str(exc),
        )
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Failed to connect to upstream chat backend",
                "upstream": LLAMA_BASE,
            },
        )

    if r.status_code >= 400:
        logger.error(
            "chat_upstream_error conversation_id=%s model=%s status=%s",
            conversation_id,
            effective_model,
            r.status_code,
        )
        raise upstream_error(r.status_code, r.text)

    try:
        data = r.json()
    except ValueError:
        logger.error(
            "chat_upstream_invalid_json conversation_id=%s model=%s body=%s",
            conversation_id,
            effective_model,
            r.text[:300],
        )
        raise HTTPException(
            status_code=502,
            detail="Upstream /v1/chat/completions returned invalid JSON",
        )
    non_stream_ms = int((time.perf_counter() - non_stream_start) * 1000)
    logger.info(
        "chat_request stream=false conversation_id=%s model=%s latency_ms=%s messages=%s",
        conversation_id,
        effective_model,
        non_stream_ms,
        len(req.messages),
    )

    message_content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    assistant_content = coerce_model_content_to_text(message_content)

    assistant_message = {
        "role": "assistant",
        "content": assistant_content,
    }

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

    return {
        "id": data.get("id", f"chatcmpl-qonduit-{uuid.uuid4().hex}"),
        "object": "chat.completion",
        "created": data.get("created", int(time.time())),
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": assistant_content,
                },
                "finish_reason": (
                    data.get("choices", [{}])[0].get("finish_reason") or "stop"
                ),
            }
        ],
        "usage": data.get("usage", {}),
    }
