from __future__ import annotations

import json
import math
import os
import re
import time
import requests
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, request, Response

app = Flask(__name__)

# ── State file path ─────────────────────────────────────────────────────────
_STATE_DIR = Path("/var/lib/qonduit-router")
_STATE_FILE = _STATE_DIR / "state.json"
_STATE_DIR.mkdir(parents=True, exist_ok=True)

# ── CORS middleware (after_request handler) ─────────────────────────────────
# Environment-driven origin list with safe defaults.
#   ROUTER_CORS_ALLOW_ALL – set to "true" to allow all origins (local testing only)
#   ROUTER_CORS_ORIGINS   – comma-separated list of allowed origins (env override)
_ROUTER_CORS_RAW = os.getenv(
    "ROUTER_CORS_ORIGINS",
    ",".join([
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:32112",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://192.168.4.250:5173",
        "http://192.168.4.250:5174",
        "http://192.168.4.250:32112",
        "http://192.168.5.5:5173",
        "http://192.168.5.5:5174",
        "http://192.168.5.5:32112",
        "https://bolt.qneural.org",
    ]),
)

if os.getenv("ROUTER_CORS_ALLOW_ALL", "").lower() == "true":
    _router_cors_origins = ["*"]
else:
    _router_cors_origins = [o.strip() for o in _ROUTER_CORS_RAW.split(",") if o.strip()]

_ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"}
_ALLOWED_HEADERS = {"Content-Type", "Authorization", "Accept", "Origin"}


@app.after_request
def _cors_headers(response: Response) -> Response:
    origin = request.headers.get("Origin", "")
    if not origin:
        return response

    # Wildcard mode: allow any origin (no credentials)
    if _router_cors_origins == ["*"]:
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif origin in _router_cors_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
    else:
        return response

    response.headers["Access-Control-Allow-Methods"] = ", ".join(sorted(_ALLOWED_METHODS))
    response.headers["Access-Control-Allow-Headers"] = ", ".join(sorted(_ALLOWED_HEADERS))
    response.headers["Access-Control-Max-Age"] = "3600"

    # ── Private Network Access (PNA) ──────────────────────────────────────
    # Respond to Chrome's PNA preflight: Access-Control-Request-Private-Network
    pna_request = request.headers.get("Access-Control-Request-Private-Network")
    if pna_request and pna_request.lower() == "true":
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    elif _router_cors_origins == ["*"]:
        # Always advertise PNA when allow-all is enabled
        response.headers["Access-Control-Allow-Private-Network"] = "true"

    return response

QONDUIT_MODEL_DIR = Path("/mnt/models/llm")
QONDUIT_SCRIPT_PATH = "/opt/llama_go3.sh"
QONDUIT_CONTAINER_NAME = "llama_server"
QONDUIT_WEBUI_BASE = "http://192.168.5.5:3000"
QONDUIT_LLAMA_BASE = "http://192.168.5.5:8080"

QONDUIT_ALLOWED_IP_PREFIXES = ("192.168.", "10.", "172.16.", "127.0.0.", "::1")
QONDUIT_REQUIRE_LOCAL_NETWORK = False


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def _is_local_request() -> bool:
    ip = _client_ip()
    return any(ip.startswith(prefix) for prefix in QONDUIT_ALLOWED_IP_PREFIXES)


def _require_local():
    if not QONDUIT_REQUIRE_LOCAL_NETWORK:
        return None
    if not _is_local_request():
        return jsonify({"ok": False, "error": "local_network_only"}), 403
    return None


def _docker_container_exists() -> bool:
    result = subprocess.run(
        ["sudo", "docker", "ps", "-a", "-q", "-f", f"name={QONDUIT_CONTAINER_NAME}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip())


def _docker_container_running() -> bool:
    result = subprocess.run(
        ["sudo", "docker", "ps", "-q", "-f", f"name={QONDUIT_CONTAINER_NAME}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip())


def _docker_container_id() -> str:
    """Return the container ID if running, empty string otherwise."""
    result = subprocess.run(
        ["sudo", "docker", "ps", "-q", "-f", f"name={QONDUIT_CONTAINER_NAME}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def _docker_container_image() -> str:
    """Return the image name of the running container, empty string otherwise."""
    result = subprocess.run(
        ["sudo", "docker", "inspect", "-f", "{{.Config.Image}}", QONDUIT_CONTAINER_NAME],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return ""


def _docker_container_labels() -> dict[str, str]:
    """Return the labels of the running container."""
    try:
        result = subprocess.run(
            ["sudo", "docker", "inspect", "-f", "{{json .Config.Labels}}", QONDUIT_CONTAINER_NAME],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _qonduit_model_list() -> list[str]:
    if not QONDUIT_MODEL_DIR.exists():
        return []
    return sorted(
        [p.name for p in QONDUIT_MODEL_DIR.glob("*.gguf")],
        key=str.lower,
    )


def _qonduit_model_metadata() -> list[dict[str, Any]]:
    """Return enriched metadata for every GGUF model on disk."""
    if not QONDUIT_MODEL_DIR.exists():
        return []

    running = _docker_container_running()
    labels = _docker_container_labels() if running else {}
    running_model = labels.get("qonduit.model", "")
    suggested_ctx = _qonduit_suggested_ctx()

    results: list[dict[str, Any]] = []
    for path in sorted(QONDUIT_MODEL_DIR.glob("*.gguf"), key=lambda p: p.name.lower()):
        stat = path.stat()
        name = path.name
        size_bytes = stat.st_size
        results.append({
            "name": name,
            "id": name,
            "path": str(path.relative_to(QONDUIT_MODEL_DIR)),
            "file_size_bytes": size_bytes,
            "file_size_human": _format_bytes_human(size_bytes),
            "parameter_size": _extract_parameter_size(name),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "suggested_context": suggested_ctx,
            "launchable": True,
            "is_running": name == running_model and running,
        })
    return results


def _extract_parameter_size(filename: str) -> str:
    """Best-effort extraction of parameter size (e.g. '35B') from a GGUF filename."""
    match = re.search(r'(?i)(\d+(?:\.\d+)?)\s*(B|b)', filename)
    if match:
        return f"{match.group(1)}{match.group(2)}"
    return "unknown"


def _qonduit_suggested_ctx() -> int:
    return 65536


def _format_bytes_human(nbytes: int) -> str:
    """Format bytes as a human-readable string (e.g. '24.8 GiB')."""
    if nbytes < 0:
        return "0 B"
    units = [("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10), ("B", 1)]
    for unit, divisor in units:
        if nbytes >= divisor:
            value = nbytes / divisor
            if unit == "GiB":
                return f"{value:.1f} {unit}"
            return f"{int(value)} {unit}"
    return f"{nbytes} B"


def _read_state() -> dict[str, Any]:
    """Read the state file, returning empty dict if missing or invalid."""
    try:
        if _STATE_FILE.exists():
            with open(_STATE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _write_state(model: str, context_size: int) -> None:
    """Write the current launch state to disk."""
    try:
        state = {
            "model": model,
            "context_size": context_size,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        _STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError:
        pass


def _clear_state() -> None:
    """Clear the state file (e.g. after stop)."""
    try:
        if _STATE_FILE.exists():
            _STATE_FILE.unlink()
    except OSError:
        pass


@app.get("/api/v1/qonduit-router/health")
def qonduit_health():
    return jsonify(
        {
            "ok": True,
            "service": "qonduit-router-api",
            "webui_base": QONDUIT_WEBUI_BASE,
            "llama_base": QONDUIT_LLAMA_BASE,
        }
    )


@app.get("/api/v1/qonduit-router/models")
def qonduit_models():
    denied = _require_local()
    if denied:
        return denied

    models = _qonduit_model_list()
    enriched = _qonduit_model_metadata()
    return jsonify(
        {
            "ok": True,
            "models": enriched,
            "model_names": models,
            "suggested_context": _qonduit_suggested_ctx(),
            "count": len(models),
        }
    )


@app.get("/api/v1/qonduit-router/status")
def qonduit_status():
    denied = _require_local()
    if denied:
        return denied

    state = _read_state()
    running = _docker_container_running()
    labels = _docker_container_labels() if running else {}

    # Try to get model/context from container labels first, then state file
    running_model = labels.get("qonduit.model", state.get("model", ""))
    context_size = labels.get("qonduit.context_size", "")
    if context_size:
        try:
            context_size = int(context_size)
        except (ValueError, TypeError):
            context_size = state.get("context_size", 0)
    else:
        context_size = state.get("context_size", 0)

    # Check readiness via llama health endpoint
    is_ready = False
    try:
        resp = requests.get(f"{QONDUIT_LLAMA_BASE}/health", timeout=2)
        is_ready = resp.status_code == 200
    except Exception:
        pass

    return jsonify({
        "ok": True,
        "container_name": QONDUIT_CONTAINER_NAME,
        "running": running,
        "exists": _docker_container_exists(),
        "webui_base": QONDUIT_WEBUI_BASE,
        "llama_base": QONDUIT_LLAMA_BASE,
        "running_model": running_model,
        "context_size": context_size,
        "last_launch": state.get("started_at", ""),
        "ready": is_ready,
        "container_id": _docker_container_id() if running else "",
        "image": _docker_container_image() if running else "",
    })


def _safe_launch(model: str, context_size: int) -> subprocess.Popen:
    """Launch llama_server via the launcher script, writing state."""
    _write_state(model, context_size)
    return subprocess.Popen(
        ["sudo", QONDUIT_SCRIPT_PATH, model, str(context_size)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@app.post("/api/v1/qonduit-router/launch")
def qonduit_launch():
    denied = _require_local()
    if denied:
        return denied

    payload = request.get_json(silent=True) or {}
    model = (payload.get("model") or "").strip()
    context = str(payload.get("context_size") or "").strip()

    if not model:
        return jsonify({"ok": False, "error": "model_required"}), 400

    if not context:
        context = str(_qonduit_suggested_ctx())

    if model not in _qonduit_model_list():
        return jsonify({"ok": False, "error": "model_not_found", "model": model}), 404

    ctx_int = int(context)
    proc = _safe_launch(model, ctx_int)

    return jsonify(
        {
            "ok": True,
            "started": True,
            "pid": proc.pid,
            "model": model,
            "context_size": ctx_int,
            "llama_base": QONDUIT_LLAMA_BASE,
            "webui_base": QONDUIT_WEBUI_BASE,
        }
    )


@app.post("/api/v1/qonduit-router/stop")
def qonduit_stop():
    denied = _require_local()
    if denied:
        return denied

    subprocess.run(
        ["sudo", "docker", "stop", QONDUIT_CONTAINER_NAME],
        capture_output=True,
        text=True,
        check=False,
    )
    _clear_state()

    return jsonify({"ok": True, "stopped": True, "container_name": QONDUIT_CONTAINER_NAME})


@app.post("/api/v1/qonduit-router/restart")
def qonduit_restart():
    denied = _require_local()
    if denied:
        return denied

    payload = request.get_json(silent=True) or {}
    model = (payload.get("model") or "").strip()
    context = str(payload.get("context_size") or "").strip()

    # If no model provided, try current running model from state/labels
    if not model:
        state = _read_state()
        model = state.get("model", "")
        if not model:
            return jsonify({"ok": False, "error": "model_required"}), 400

    # If no context provided, try current from state or labels
    if not context:
        state = _read_state()
        context = str(state.get("context_size", ""))
    if not context:
        context = str(_qonduit_suggested_ctx())

    if model not in _qonduit_model_list():
        return jsonify({"ok": False, "error": "model_not_found", "model": model}), 404

    ctx_int = int(context)
    proc = _safe_launch(model, ctx_int)

    return jsonify(
        {
            "ok": True,
            "restarted": True,
            "model": model,
            "context_size": ctx_int,
            "pid": proc.pid,
        }
    )


@app.get("/api/v1/qonduit-router/gpu")
def qonduit_gpu():
    denied = _require_local()
    if denied:
        return denied

    try:
        result = subprocess.run(
            [
                "sudo", "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "nvidia-smi failed")

        gpus: list[dict[str, Any]] = []
        total_mib = 0
        used_mib = 0
        free_mib = 0

        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            idx = int(parts[0])
            name = parts[1]
            mem_total = int(parts[2])
            mem_used = int(parts[3])
            mem_free = int(parts[4])

            gpus.append({
                "index": idx,
                "name": name,
                "memory_total_mib": mem_total,
                "memory_used_mib": mem_used,
                "memory_free_mib": mem_free,
            })
            total_mib += mem_total
            used_mib += mem_used
            free_mib += mem_free

        return jsonify({
            "ok": True,
            "gpus": gpus,
            "memory_total_mib": total_mib,
            "memory_used_mib": used_mib,
            "memory_free_mib": free_mib,
            "memory_total_human": _format_bytes_human(total_mib * 1024 * 1024),
            "memory_used_human": _format_bytes_human(used_mib * 1024 * 1024),
            "memory_free_human": _format_bytes_human(free_mib * 1024 * 1024),
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": "gpu_query_failed",
            "detail": str(e),
        }), 503

@app.get("/api/v1/qonduit-router/logs")
def qonduit_logs():
    denied = _require_local()
    if denied:
        return denied

    try:
        result = subprocess.run(
            ["sudo", "docker", "logs", "--tail", "300", QONDUIT_CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            logs_text = result.stdout if result.stdout else result.stderr
            return Response(
                (logs_text if logs_text else "[router] No log output available.\n"),
                mimetype="text/plain",
            )

        # Container doesn't exist or docker failed
        stderr = result.stderr.strip() if result.stderr else "docker returned non-zero exit"
        return Response(
            f"[router] llama_server logs unavailable.\n[router] docker returned: {stderr}\n",
            mimetype="text/plain",
        )

    except subprocess.TimeoutExpired:
        return Response(
            "[router] llama_server logs unavailable.\n[router] docker logs timed out.\n",
            mimetype="text/plain",
        )
    except FileNotFoundError:
        return Response(
            "[router] llama_server logs unavailable.\n[router] docker not found.\n",
            mimetype="text/plain",
        )
    except Exception as e:
        return Response(
            f"[router] llama_server logs unavailable.\n[router] error: {e}\n",
            mimetype="text/plain",
        )

@app.get("/api/v1/qonduit-router/context/suggest")
def qonduit_context_suggest():
    denied = _require_local()
    if denied:
        return denied

    return jsonify({"ok": True, "context_size": _qonduit_suggested_ctx()})

@app.get("/api/v1/qonduit-router/ready")
def qonduit_ready():
    denied = _require_local()
    if denied:
        return denied

    try:
        response = requests.get(f"{QONDUIT_LLAMA_BASE}/health", timeout=2)
        is_ready = response.status_code == 200
        return jsonify({"ok": True, "ready": is_ready})
    except Exception as e:
        return jsonify({"ok": True, "ready": False, "detail": str(e)})

# ── Hugging Face Model Management (Phase 1) ─────────────────────────────────

# ── Configuration ────────────────────────────────────────────────────────────
_QONDUIT_MODEL_DIR = Path(
    os.getenv("QONDUIT_MODEL_DIR", "/mnt/models/llm"),
)
_QONDUIT_HF_TOKEN = os.getenv("HF_TOKEN", "")
_QONDUIT_HF_SEARCH_LIMIT_DEFAULT = int(
    os.getenv("HF_SEARCH_LIMIT_DEFAULT", "20"),
)
_QONDUIT_HF_SEARCH_LIMIT_MAX = int(
    os.getenv("HF_SEARCH_LIMIT_MAX", "50"),
)
_QONDUIT_HF_REQUIRE_GGUF = os.getenv("HF_REQUIRE_GGUF", "true").lower() == "true"
_QONDUIT_HF_NETWORK_TIMEOUT = float(
    os.getenv("HF_NETWORK_TIMEOUT_SECONDS", "10"),
)
_QONDUIT_HF_SEARCH_COOLDOWN = float(
    os.getenv("HF_SEARCH_COOLDOWN_SECONDS", "5"),
)
_QONDUIT_HF_CACHE_TTL = float(
    os.getenv("HF_SEARCH_CACHE_TTL_SECONDS", "120"),
)
_QONDUIT_HF_ALLOW_DELETE = os.getenv("HF_ALLOW_DELETE", "true").lower() == "true"

_HF_HUB_AVAILABLE = False
try:
    import huggingface_hub  # noqa: F401
    _HF_HUB_AVAILABLE = True
except ImportError:
    pass

# ── In-memory cache for HF API results ───────────────────────────────────────
_hf_cache: dict[str, tuple[Any, float]] = {}
_hf_search_cooldown: dict[str, float] = {}

# ── Quantization order mapping ──────────────────────────────────────────────
_QUANT_ORDER = [
    "Q2_K", "Q3_K_S", "Q3_K_M", "Q3_K_L",
    "Q4_0", "Q4_K_S", "Q4_K_M",
    "Q5_0", "Q5_K_S", "Q5_K_M",
    "Q6_K",
    "Q8_0",
    "IQ2_XS", "IQ2_HS", "IQ2_S", "IQ2_M",
    "IQ3_XS", "IQ3_YS", "IQ3_S",
    "IQ4_XS", "IQ4_NL",
    "IQ1_S", "IQ1_M",
    "F16", "F32", "BF16",
]


def _get_model_dir() -> Path:
    """Return the configured model directory path."""
    return _QONDUIT_MODEL_DIR


def _human_size(nbytes: int) -> str:
    """Format bytes as a human-readable string (e.g. '24.8 GiB')."""
    if nbytes < 0:
        return "0 B"
    units = [("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10), ("B", 1)]
    for unit, divisor in units:
        if nbytes >= divisor:
            value = nbytes / divisor
            if unit == "GiB":
                return f"{value:.1f} {unit}"
            return f"{int(value)} {unit}"
    return f"{nbytes} B"


def _validate_model_filename(name: str) -> Optional[str]:
    """Validate a model filename for safe deletion.

    Returns an error message string if invalid, None if valid.
    """
    if not name or not isinstance(name, str):
        return "model_name_required"

    name = name.strip()
    if not name:
        return "model_name_required"

    # Reject absolute paths
    if name.startswith("/"):
        return "absolute_path_rejected"

    # Reject path traversal
    if ".." in name:
        return "path_traversal_blocked"

    # Reject forward slashes (no subdirectory components)
    if "/" in name or "\\" in name:
        return "path_traversal_blocked"

    # Only allow .gguf files by default
    if _QONDUIT_HF_ALLOW_DELETE and not name.lower().endswith(".gguf"):
        return "gguf_only"

    # Reject empty basename after normalization
    basename = Path(name).name
    if not basename:
        return "invalid_filename"

    return None


def _resolve_model_path(name: str) -> Optional[Path]:
    """Resolve and validate the model path is inside the model directory.

    Returns the resolved Path if safe, None otherwise.
    """
    error = _validate_model_filename(name)
    if error:
        return None

    model_dir = _get_model_dir()
    if not model_dir.exists():
        return None

    resolved = (model_dir / name).resolve()

    # Ensure the resolved path is strictly under the model directory
    try:
        resolved.relative_to(model_dir.resolve())
    except ValueError:
        return None

    return resolved


def _ensure_under_model_dir(path: Path) -> bool:
    """Check that *path* is strictly under the model directory."""
    try:
        path.resolve().relative_to(_get_model_dir().resolve())
        return True
    except ValueError:
        return False


def _parse_quant_from_filename(filename: str) -> str:
    """Extract quantization type from a GGUF filename.

    Examples:
        'model-Q4_K_M.gguf' -> 'Q4_K_M'
        'llama-3b-Q8_0.gguf' -> 'Q8_0'
        'model-IQ4_XS.gguf' -> 'IQ4_XS'
    """
    stem = Path(filename).stem  # e.g. 'model-Q4_K_M'
    for quant in _QUANT_ORDER:
        if quant in stem:
            return quant
    # Fallback: try to find any known quant pattern in the stem
    import re
    match = re.search(r'\b(Q[2-9]|IQ[1-4])_(?:K_)?[0-9_]+\b', stem, re.IGNORECASE)
    if match:
        return match.group(0).upper()
    return "unknown"


def _validate_repo_id(repo_id: str) -> Optional[str]:
    """Validate a Hugging Face repo ID format.

    Returns an error message if invalid, None if valid.
    """
    if not repo_id or not isinstance(repo_id, str):
        return "repo_id_required"

    repo_id = repo_id.strip()
    if not repo_id:
        return "repo_id_required"

    # Must match <owner>/<repo> pattern
    if "/" not in repo_id:
        return "invalid_repo_id_format"

    # Validate each part
    parts = repo_id.split("/")
    if len(parts) != 2:
        return "invalid_repo_id_format"

    owner, repo = parts
    if not owner or not repo:
        return "invalid_repo_id_format"

    # Basic alphanumeric + hyphen + underscore check
    if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$', owner):
        return "invalid_repo_id_format"
    if not re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?$', repo):
        return "invalid_repo_id_format"

    return None


# ── Hugging Face HTTP API functions ─────────────────────────────────────────

def _hf_http_request(url: str, headers: Optional[dict] = None, timeout: Optional[float] = None) -> Optional[dict]:
    """Make an HTTP GET request to Hugging Face API and return JSON.

    Returns None on any error.
    """
    if timeout is None:
        timeout = _QONDUIT_HF_NETWORK_TIMEOUT

    req_headers = {"Accept": "application/json"}
    if _QONDUIT_HF_TOKEN:
        req_headers["Authorization"] = f"Bearer {_QONDUIT_HF_TOKEN}"
    if headers:
        req_headers.update(headers)

    try:
        resp = requests.get(url, headers=req_headers, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        return None
    except (requests.RequestException, ValueError, OSError):
        return None


def _cached_hf_call(cache_key: str, fetch_func, **kwargs) -> tuple[Any, bool, float]:
    """Execute an HF API call with in-memory caching and cooldown.

    Returns (result, was_cached, cache_age_seconds).
    """
    now = time.time()

    # Check cache
    if cache_key in _hf_cache:
        result, cached_at = _hf_cache[cache_key]
        age = now - cached_at
        if age < _QONDUIT_HF_CACHE_TTL:
            return result, True, round(age, 1)

    # Evict expired cache entry
    if cache_key in _hf_cache:
        del _hf_cache[cache_key]

    # Execute the fetch
    result = fetch_func(**kwargs)

    # Store in cache
    _hf_cache[cache_key] = (result, now)

    return result, False, 0.0


def _hf_search_models(query: str, limit: int, sort: str) -> tuple[dict, bool, float]:
    """Search Hugging Face models for repos matching the query.

    Returns (result_dict, was_cached, cache_age_seconds).
    """
    # Clamp limit
    limit = min(max(limit, 1), _QONDUIT_HF_SEARCH_LIMIT_MAX)

    def _fetch(sort_key: str) -> Optional[list[dict]]:
        """Fetch search results from HF API."""
        url = (
            f"https://huggingface.co/api/models"
            f"?search={requests.utils.quote(query)}"
            f"&limit={limit}"
            f"&sort={sort_key}"
            f"&direction=-1"
        )
        data = _hf_http_request(url)
        if not isinstance(data, list):
            return None

        results = []
        for item in data:
            if not isinstance(item, dict):
                continue

            repo_id = item.get("id", "")
            if not repo_id:
                continue

            results.append({
                "repo_id": repo_id,
                "model_id": repo_id,
                "author": repo_id.split("/")[0] if "/" in repo_id else "",
                "downloads": item.get("downloads", 0),
                "likes": item.get("likes", 0),
                "last_modified": item.get("lastModified", ""),
                "tags": item.get("tags", []),
                "pipeline_tag": item.get("pipelineTag", ""),
                "private": item.get("private", False),
                "gated": item.get("gated", False),
                "url": f"https://huggingface.co/{repo_id}",
            })

        return results

    sort_map = {
        "downloads": "downloads",
        "likes": "likes",
        "lastModified": "lastModified",
    }
    sort_key = sort_map.get(sort, "downloads")

    cache_key = f"search:{query}:{sort_key}:{limit}"
    raw_results, was_cached, age = _cached_hf_call(
        cache_key, _fetch, sort_key=sort_key,
    )

    if raw_results is None:
        return None, was_cached, age

    # Optionally verify GGUF files exist in each repo
    if _QONDUIT_HF_REQUIRE_GGUF:
        verified = []
        max_inspect = limit * 3  # Inspect at most 3x requested limit
        inspected = 0
        for candidate in raw_results:
            if inspected >= max_inspect:
                break
            inspected += 1
            gguf_info = _hf_repo_gguf_files_internal(candidate["repo_id"])
            if gguf_info and gguf_info.get("gguf_count", 0) > 0:
                candidate["gguf_count"] = gguf_info["gguf_count"]
                candidate["sample_gguf_files"] = gguf_info.get("sample_files", [])
                verified.append(candidate)
            if len(verified) >= limit:
                break
        raw_results = verified

    # Add GGUF counts if not already set
    for item in raw_results:
        if "gguf_count" not in item:
            gguf_info = _hf_repo_gguf_files_internal(item["repo_id"])
            if gguf_info:
                item["gguf_count"] = gguf_info.get("gguf_count", 0)
                item["sample_gguf_files"] = gguf_info.get("sample_files", [])
            else:
                item["gguf_count"] = 0
                item["sample_gguf_files"] = []

    return raw_results, was_cached, age


def _hf_repo_gguf_files_internal(repo_id: str) -> Optional[dict]:
    """Internal: get GGUF file list for a repo (no caching)."""
    error = _validate_repo_id(repo_id)
    if error:
        return None

    def _fetch_repo() -> Optional[dict]:
        url = f"https://huggingface.co/api/models/{repo_id}"
        data = _hf_http_request(url)
        if not isinstance(data, dict):
            return None
        return data

    raw, _, _ = _cached_hf_call(
        f"repo:{repo_id}", _fetch_repo,
    )

    if not isinstance(raw, dict):
        return None

    siblings = raw.get("siblings")
    if not isinstance(siblings, list):
        return {"repo_id": repo_id, "gguf_count": 0, "files": []}

    gguf_files = []
    for sibling in siblings:
        if not isinstance(sibling, dict):
            continue
        path = sibling.get("rfilename") or sibling.get("path", "")
        if path.lower().endswith(".gguf"):
            size = sibling.get("size", 0)
            quant = _parse_quant_from_filename(path)
            gguf_files.append({
                "filename": Path(path).name,
                "path": path,
                "size_bytes": size if isinstance(size, int) else 0,
                "size_human": _human_size(size) if isinstance(size, (int, float)) and size >= 0 else "",
                "is_gguf": True,
                "quant": quant,
                "downloadable": True,
                "url": f"https://huggingface.co/{repo_id}/blob/main/{path}",
            })

    # Sort by quant order
    gguf_files.sort(key=lambda f: (_QUANT_ORDER.index(f["quant"]) if f["quant"] in _QUANT_ORDER else 999, f["filename"]))

    sample_files = [f["filename"] for f in gguf_files[:2]]

    return {
        "repo_id": repo_id,
        "gguf_count": len(gguf_files),
        "files": gguf_files,
        "sample_files": sample_files,
        "url": f"https://huggingface.co/{repo_id}",
        "gated": raw.get("gated", False),
        "private": raw.get("private", False),
    }


# ── Endpoint: GET /hf/search ────────────────────────────────────────────────

@app.get("/api/v1/qonduit-router/hf/search")
def qonduit_hf_search():
    """Search Hugging Face for models that may contain GGUF files."""
    try:
        query = (request.args.get("q") or "").strip()
        if not query:
            return jsonify({
                "ok": False,
                "error": "query_required",
                "query": query,
                "results": [],
                "cached": False,
                "retry_after_seconds": 0,
            }), 400

        limit = _QONDUIT_HF_SEARCH_LIMIT_DEFAULT
        try:
            limit = int(request.args.get("limit", _QONDUIT_HF_SEARCH_LIMIT_DEFAULT))
        except (ValueError, TypeError):
            pass
        limit = min(max(limit, 1), _QONDUIT_HF_SEARCH_LIMIT_MAX)

        sort = request.args.get("sort", "downloads")
        valid_sorts = ("downloads", "likes", "lastModified")
        if sort not in valid_sorts:
            sort = "downloads"

        # Rate-limit identical queries
        cooldown_key = f"cooldown:{query}:{sort}"
        now = time.time()
        if cooldown_key in _hf_search_cooldown:
            last_time = _hf_search_cooldown[cooldown_key]
            elapsed = now - last_time
            if elapsed < _QONDUIT_HF_SEARCH_COOLDOWN:
                retry_after = round(_QONDUIT_HF_SEARCH_COOLDOWN - elapsed, 1)
                return jsonify({
                    "ok": False,
                    "error": "rate_limited",
                    "query": query,
                    "limit": limit,
                    "sort": sort,
                    "require_gguf": _QONDUIT_HF_REQUIRE_GGUF,
                    "source": "huggingface",
                    "hf_models_url": f"https://huggingface.co/models?search={requests.utils.quote(query)}",
                    "cached": False,
                    "cache_age_seconds": 0,
                    "results": [],
                    "retry_after_seconds": retry_after,
                }), 429
        _hf_search_cooldown[cooldown_key] = now

        # Check cooldown cache expiry too
        for key in list(_hf_search_cooldown.keys()):
            if key.startswith("cooldown:") and now - _hf_search_cooldown[key] > _QONDUIT_HF_CACHE_TTL * 2:
                del _hf_search_cooldown[key]

        # Check result cache
        cache_key = f"search:{query}:{sort}:{limit}"
        if cache_key in _hf_cache:
            raw_results, cached_at = _hf_cache[cache_key]
            if raw_results is not None and isinstance(raw_results, list):
                hf_models_url = f"https://huggingface.co/models?search={requests.utils.quote(query)}"
                return jsonify({
                    "ok": True,
                    "query": query,
                    "count": len(raw_results),
                    "limit": limit,
                    "sort": sort,
                    "require_gguf": _QONDUIT_HF_REQUIRE_GGUF,
                    "source": "huggingface",
                    "hf_models_url": hf_models_url,
                    "cached": True,
                    "cache_age_seconds": round(now - cached_at, 1),
                    "results": raw_results,
                    "error": None,
                })

        # Fetch from HF
        results, was_cached, cache_age = _hf_search_models(query, limit, sort)

        if results is None:
            return jsonify({
                "ok": False,
                "error": "hf_unavailable",
                "detail": "Hugging Face API is currently unavailable. Please try again later.",
                "query": query,
                "limit": limit,
                "sort": sort,
                "require_gguf": _QONDUIT_HF_REQUIRE_GGUF,
                "source": "huggingface",
                "hf_models_url": f"https://huggingface.co/models?search={requests.utils.quote(query)}",
                "cached": False,
                "cache_age_seconds": 0,
                "results": [],
                "retry_after_seconds": max(10, _QONDUIT_HF_NETWORK_TIMEOUT),
            }), 503

        hf_models_url = f"https://huggingface.co/models?search={requests.utils.quote(query)}"
        return jsonify({
            "ok": True,
            "query": query,
            "count": len(results),
            "limit": limit,
            "sort": sort,
            "require_gguf": _QONDUIT_HF_REQUIRE_GGUF,
            "source": "huggingface",
            "hf_models_url": hf_models_url,
            "cached": was_cached,
            "cache_age_seconds": cache_age,
            "results": results,
            "error": None,
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": "search_failed",
            "detail": str(e),
            "query": query if 'query' in dir() else "",
            "results": [],
            "cached": False,
            "retry_after_seconds": 0,
        }), 500


# ── Endpoint: GET /hf/repo-files ────────────────────────────────────────────

@app.get("/api/v1/qonduit-router/hf/repo-files")
def qonduit_hf_repo_files():
    """List GGUF files available in a Hugging Face repo."""
    try:
        repo_id = (request.args.get("repo_id") or "").strip()
        if not repo_id:
            return jsonify({
                "ok": False,
                "error": "repo_id_required",
                "repo_id": repo_id,
                "files": [],
                "cached": False,
            }), 400

        error = _validate_repo_id(repo_id)
        if error:
            return jsonify({
                "ok": False,
                "error": "invalid_repo_id",
                "detail": error,
                "repo_id": repo_id,
                "url": "",
                "files": [],
                "cached": False,
            }), 400

        def _fetch() -> Optional[dict]:
            return _hf_repo_gguf_files_internal(repo_id)

        result, was_cached, cache_age = _cached_hf_call(
            f"repofiles:{repo_id}", _fetch,
        )

        if result is None:
            return jsonify({
                "ok": False,
                "error": "hf_unavailable",
                "detail": "Hugging Face API is currently unavailable.",
                "repo_id": repo_id,
                "url": f"https://huggingface.co/{repo_id}",
                "gguf_count": 0,
                "files": [],
                "cached": False,
                "retry_after_seconds": max(10, _QONDUIT_HF_NETWORK_TIMEOUT),
            }), 503

        files = result.get("files", [])
        return jsonify({
            "ok": True,
            "repo_id": repo_id,
            "url": result.get("url", f"https://huggingface.co/{repo_id}"),
            "gguf_count": result.get("gguf_count", len(files)),
            "gated": result.get("gated", False),
            "private": result.get("private", False),
            "cached": was_cached,
            "cache_age_seconds": cache_age,
            "files": files,
            "error": None,
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": "repo_files_failed",
            "detail": str(e),
            "repo_id": repo_id if 'repo_id' in dir() else "",
            "files": [],
            "cached": False,
        }), 500


# ── Endpoint: POST /models/delete ───────────────────────────────────────────

@app.post("/api/v1/qonduit-router/models/delete")
def qonduit_model_delete():
    """Safely delete (or trash) a local GGUF model file."""
    try:
        if not _QONDUIT_HF_ALLOW_DELETE:
            return jsonify({
                "ok": False,
                "error": "delete_disabled",
                "detail": "Model deletion is not enabled (HF_ALLOW_DELETE=false).",
            }), 403

        payload = request.get_json(silent=True) or {}
        model_name = (payload.get("model") or "").strip()
        confirm = payload.get("confirm", False)
        force = payload.get("force", False)

        if not model_name:
            return jsonify({
                "ok": False,
                "error": "model_name_required",
            }), 400

        if not confirm:
            return jsonify({
                "ok": False,
                "error": "confirmation_required",
                "detail": "Set confirm=true to delete a model.",
            }), 400

        # Validate filename
        error = _validate_model_filename(model_name)
        if error:
            return jsonify({
                "ok": False,
                "error": f"invalid_{error}",
                "detail": f"Model name validation failed: {error}",
            }), 400

        # Resolve path safely
        resolved = _resolve_model_path(model_name)
        if resolved is None:
            return jsonify({
                "ok": False,
                "error": "path_resolution_failed",
                "detail": "Could not resolve model path. Ensure the file exists in the model directory.",
            }), 404

        # Final safety check
        if not _ensure_under_model_dir(resolved):
            return jsonify({
                "ok": False,
                "error": "path_traversal_blocked",
                "detail": "Path traversal attempt blocked.",
            }), 403

        if not resolved.is_file():
            return jsonify({
                "ok": False,
                "error": "model_not_found",
                "detail": f"Model file not found: {resolved}",
            }), 404

        # Check if running model
        state = _read_state()
        running_model = state.get("model", "")
        if running_model and running_model == model_name:
            if not force:
                return jsonify({
                    "ok": False,
                    "error": "model_running",
                    "detail": "Stop the model before deleting it. Use force=true to override.",
                    "running_model": running_model,
                }), 409

        # Try to move to trash first
        model_dir = _get_model_dir()
        trash_dir = model_dir / ".trash"
        trash_path = None

        try:
            trash_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            trash_filename = f"{timestamp}-{model_name}"
            trash_path = trash_dir / trash_filename
            resolved.rename(trash_path)
        except OSError as e:
            # If trash move fails, fall back to direct delete (confirm was already checked)
            try:
                resolved.unlink()
            except OSError as e2:
                return jsonify({
                    "ok": False,
                    "error": "delete_failed",
                    "detail": f"Failed to delete model: {e2}",
                }), 500

        return jsonify({
            "ok": True,
            "deleted": True,
            "model": model_name,
            "path": str(resolved),
            "trash_path": str(trash_path) if trash_path else None,
            "method": "trash" if trash_path else "delete",
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": "delete_failed",
            "detail": str(e),
        }), 500


# ── Endpoint: GET /models/trash ─────────────────────────────────────────────

@app.get("/api/v1/qonduit-router/models/trash")
def qonduit_model_trash():
    """List files in the model trash directory."""
    try:
        model_dir = _get_model_dir()
        trash_dir = model_dir / ".trash"

        if not trash_dir.exists():
            return jsonify({
                "ok": True,
                "trash_dir": str(trash_dir),
                "count": 0,
                "files": [],
            })

        files = []
        for f in sorted(trash_dir.iterdir(), key=lambda p: p.name.lower()):
            if f.is_file():
                stat = f.stat()
                files.append({
                    "trash_name": f.name,
                    "original_name": f.name.split("-", 1)[1] if "-" in f.name else f.name,
                    "size_bytes": stat.st_size,
                    "size_human": _human_size(stat.st_size),
                    "trashed_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "path": str(f),
                })

        return jsonify({
            "ok": True,
            "trash_dir": str(trash_dir),
            "count": len(files),
            "files": files,
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": "trash_list_failed",
            "detail": str(e),
            "files": [],
        }), 500


# ── Endpoint: POST /models/restore ──────────────────────────────────────────

@app.post("/api/v1/qonduit-router/models/restore")
def qonduit_model_restore():
    """Restore a model file from trash."""
    try:
        if not _QONDUIT_HF_ALLOW_DELETE:
            return jsonify({
                "ok": False,
                "error": "delete_disabled",
                "detail": "Model management is not enabled (HF_ALLOW_DELETE=false).",
            }), 403

        payload = request.get_json(silent=True) or {}
        trash_name = (payload.get("trash_name") or "").strip()
        confirm = payload.get("confirm", False)

        if not trash_name:
            return jsonify({
                "ok": False,
                "error": "trash_name_required",
            }), 400

        if not confirm:
            return jsonify({
                "ok": False,
                "error": "confirmation_required",
                "detail": "Set confirm=true to restore a model.",
            }), 400

        model_dir = _get_model_dir()
        trash_dir = model_dir / ".trash"
        trash_path = (trash_dir / trash_name).resolve()

        # Safety: must be under trash dir
        try:
            trash_path.relative_to(trash_dir.resolve())
        except ValueError:
            return jsonify({
                "ok": False,
                "error": "path_traversal_blocked",
                "detail": "Path traversal attempt blocked.",
            }), 403

        if not trash_path.is_file():
            return jsonify({
                "ok": False,
                "error": "trash_file_not_found",
                "detail": f"Trash file not found: {trash_name}",
            }), 404

        # Extract original filename (remove timestamp prefix)
        parts = trash_name.split("-", 1)
        original_name = parts[1] if len(parts) > 1 else trash_name

        # Validate original name
        error = _validate_model_filename(original_name)
        if error:
            return jsonify({
                "ok": False,
                "error": "invalid_original_name",
                "detail": f"Restored filename validation failed: {error}",
            }), 400

        dest_path = (model_dir / original_name).resolve()

        # Don't overwrite existing files
        if dest_path.exists():
            return jsonify({
                "ok": False,
                "error": "file_exists",
                "detail": f"File already exists: {original_name}",
            }), 409

        trash_path.rename(dest_path)

        return jsonify({
            "ok": True,
            "restored": True,
            "model": original_name,
            "path": str(dest_path),
            "trash_name": trash_name,
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": "restore_failed",
            "detail": str(e),
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
