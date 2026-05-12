from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import uuid
import requests
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from qonduit_slots import (
    load_slots,
    save_slots,
    get_slot,
    create_slot,
    update_slot,
    delete_slot,
    validate_slot,
    slot_to_live_status,
    collect_slot_errors,
    _QONDUIT_DEFAULT_HOST,
    _format_bytes_human,
)
from qonduit_docker_helpers import (
    container_exists,
    container_running,
    container_status,
    get_container_id,
    get_container_labels,
    stop_slot_container,
    remove_slot_container,
    stream_slot_logs,
    launch_slot_container,
    check_slot_ready,
    fetch_slot_models,
    port_is_available,
    docker_name_is_available,
    collect_gpu_summary,
    compute_auto_tensor_split,
    docker_available,
)
from qonduit_router_api_slots import register_slot_routes

from flask import Flask, jsonify, request, Response

app = Flask(__name__)

# ── State file path ─────────────────────────────────────────────────────────
_STATE_DIR = Path(__file__).parent / "data"
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

# ── Hugging Face Download Jobs (Phase 2) ─────────────────────────────────────
_QONDUIT_HF_ALLOW_NON_GGUF = os.getenv("HF_ALLOW_NON_GGUF", "false").lower() == "true"
_QONDUIT_HF_DOWNLOAD_MAX_CONCURRENT = int(os.getenv("HF_DOWNLOAD_MAX_CONCURRENT", "1"))

# Download job persistence
_DOWNLOAD_JOBS_DIR = Path(__file__).parent / "data"
_DOWNLOAD_JOBS_FILE = _DOWNLOAD_JOBS_DIR / "download_jobs.json"
_DOWNLOAD_JOBS_DIR.mkdir(parents=True, exist_ok=True)

# In-memory download job state: job_id -> job dict
_download_jobs: dict[str, dict[str, Any]] = {}
_download_jobs_lock = threading.Lock()

# Download queue: list of job_ids waiting to start
_download_queue: list[str] = []
_download_queue_lock = threading.Lock()

# Active download count
_download_active_count = 0
_download_active_count_lock = threading.Lock()

# Worker thread
_download_worker_thread: Optional[threading.Thread] = None
_download_shutdown_event = threading.Event()

# ── Quantization order mapping ──────────────────────────────────────────────
_QUANT_ORDER = [
    "Q2_K", "Q2_K_XL",
    "Q3_K_S", "Q3_K_M", "Q3_K_L", "Q3_K_XL",
    "Q4_0", "Q4_1", "Q4_K_S", "Q4_K_M", "Q4_K_L", "Q4_K_XL",
    "Q5_0", "Q5_1", "Q5_K_S", "Q5_K_M", "Q5_K_L", "Q5_K_XL",
    "Q6_K", "Q6_K_XL",
    "Q8_0", "Q8_K", "Q8_K_XL",
    "IQ1_S", "IQ1_M",
    "IQ2_XS", "IQ2_HS", "IQ2_S", "IQ2_M", "IQ2_XXS",
    "IQ3_XS", "IQ3_YS", "IQ3_S", "IQ3_M", "IQ3_XXS",
    "IQ4_XS", "IQ4_NL", "IQ4_XXS",
    "MXFP4", "MXFP4_MOE",
    "BF16", "F16", "F32",
]


def _get_model_dir() -> Path:
    """Return the configured model directory path."""
    return _QONDUIT_MODEL_DIR


def _human_size(nbytes: int | float) -> str:
    """Format bytes as a human-readable string (e.g. '24.8 GiB')."""
    if not isinstance(nbytes, (int, float)) or nbytes < 0:
        return "unknown"
    if nbytes == 0:
        return "0 B"
    units = [("GiB", 1 << 30), ("MiB", 1 << 20), ("KiB", 1 << 10), ("B", 1)]
    for unit, divisor in units:
        if nbytes >= divisor:
            value = nbytes / divisor
            if unit == "GiB":
                return f"{value:.1f} {unit}"
            return f"{int(value)} {unit}"
    return f"{int(nbytes)} B"


def _human_size_metric(nbytes: int | float) -> str:
    """Format bytes as decimal GB/MB (e.g. '24.8 GB')."""
    if not isinstance(nbytes, (int, float)) or nbytes < 0:
        return "unknown"
    if nbytes == 0:
        return "0 B"
    units = [("GB", 1_000_000_000), ("MB", 1_000_000), ("KB", 1_000), ("B", 1)]
    for unit, divisor in units:
        if nbytes >= divisor:
            value = nbytes / divisor
            if unit in ("GB",):
                return f"{value:.1f} {unit}"
            return f"{int(value)} {unit}"
    return f"{int(nbytes)} B"


def _parse_parameter_size(text: str) -> tuple[Optional[str], Optional[float], Optional[str]]:
    """Infer model parameter size from a repo ID, model ID, or filename.

    Returns (parameter_size_str, parameter_size_num, parameter_size_unit)
    e.g. ("35B", 35.0, "B") or ("unknown", None, None)

    Handles MoE names like 35B-A3B → total=35B, active=3B.
    """
    if not text:
        return None, None, None

    # Common parameter size patterns: 0.5B, 1.5B, 7B, 35B, 70B, etc.
    # Also handle MoE: 35B-A3B, 14B-A2.7B
    # Look for patterns like N.B or NMB where N is a number
    match = re.search(
        r'(?<!\d)(\d+(?:\.\d+)?)([Bb])(?!\d)',
        text,
    )
    if match:
        num_str = match.group(1)
        unit = match.group(2).upper()
        try:
            num = float(num_str)
            # Normalize: if num < 1000, treat as B (billions of params)
            if num < 1000:
                return f"{num_str}{unit}", num, unit
        except (ValueError, OverflowError):
            pass

    # Try to extract active parameters from MoE patterns like A3B
    active_match = re.search(r'A(\d+(?:\.\d+)?)([Bb])(?!\d)', text)
    active_num_str = None
    if active_match:
        active_num_str = active_match.group(1)
        active_unit = active_match.group(2).upper()
        try:
            active_num = float(active_num_str)
            return None, None, None  # We'll handle MoE separately
        except (ValueError, OverflowError):
            pass

    return None, None, None


def _parse_total_and_active_params(text: str) -> tuple[Optional[str], Optional[float], Optional[str], Optional[str], Optional[float]]:
    """Parse total and active parameter sizes from MoE model names.

    Examples:
        'Qwen3.6-35B-A3B-GGUF' → ('35B', 35.0, 'B', '3B', 3.0)
        'model-70B-A32B' → ('70B', 70.0, 'B', '32B', 32.0)
        'llama-7B' → ('7B', 7.0, 'B', None, None)

    Returns (total_str, total_num, total_unit, active_str, active_num)
    """
    if not text:
        return None, None, None, None, None

    # Match total params: number followed by B before -A
    total_match = re.search(r'(?<!\d)(\d+(?:\.\d+)?)([Bb])(?=\s*-[Aa])', text)
    active_match = re.search(r'-[Aa](\d+(?:\.\d+)?)([Bb])(?=\s*[-_]|$)', text)

    total_str = None
    total_num = None
    active_str = None
    active_num = None

    if total_match:
        try:
            total_num = float(total_match.group(1))
            total_str = f"{total_match.group(1)}{total_match.group(2).upper()}"
        except (ValueError, OverflowError):
            pass

    if active_match and total_num is not None:
        try:
            active_num = float(active_match.group(1))
            active_str = f"{active_match.group(1)}{active_match.group(2).upper()}"
        except (ValueError, OverflowError):
            pass

    return total_str, total_num, "B", active_str, active_num


def _get_file_size_via_head(url: str, timeout: float = 5.0) -> int | None:
    """Try to get file size via HEAD request to the resolve URL.

    Returns size in bytes or None on failure.
    """
    if not url:
        return None
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True)
        if resp.status_code in (200, 206, 301, 302, 307, 308):
            cl = resp.headers.get("Content-Length")
            if cl:
                try:
                    return int(cl)
                except (ValueError, TypeError):
                    pass
    except (requests.RequestException, OSError, ValueError):
        pass
    return None


def _size_fields(nbytes: int | float | None) -> dict:
    """Build size-related fields for a file entry.

    Returns dict with:
      - size_bytes: int or None
      - size_human: str (GiB/MiB)
      - size_human_metric: str (GB/MB)
      - size_gib: float or None
      - size_gb: float or None
    """
    if isinstance(nbytes, (int, float)) and nbytes > 0:
        return {
            "size_bytes": int(nbytes),
            "size_human": _human_size(nbytes),
            "size_human_metric": _human_size_metric(nbytes),
            "size_gib": round(nbytes / (1 << 30), 2),
            "size_gb": round(nbytes / 1_000_000_000, 2),
        }
    return {
        "size_bytes": None,
        "size_human": "unknown",
        "size_human_metric": "unknown",
        "size_gib": None,
        "size_gb": None,
    }


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

    Handles modern quant names including _XL, _XXS variants, MXFP4, BF16, etc.

    Examples:
        'model-Q4_K_XL.gguf' -> 'Q4_K_XL'
        'llama-3b-Q8_0.gguf' -> 'Q8_0'
        'model-IQ4_XS.gguf' -> 'IQ4_XS'
        'model-IQ2_XXS.gguf' -> 'IQ2_XXS'
        'model-BF16.gguf' -> 'BF16'
    """
    stem = Path(filename).stem  # e.g. 'model-Q4_K_XL'
    name = stem

    # Sort by length descending so longer names (Q4_K_XL) match before shorter (Q4_K)
    known_quants = sorted(_QUANT_ORDER, key=len, reverse=True)

    for q in known_quants:
        if q in name:
            return q

    # Fallback: regex for common patterns
    patterns = [
        r'\b(Q8_K(?:_XL)?)\b',
        r'\b(Q6_K(?:_XL)?)\b',
        r'\b(Q5_K(?:_XL|_L|_M|_S)?|Q5_[01])\b',
        r'\b(Q4_K(?:_XL|_L|_M|_S)?|Q4_[01])\b',
        r'\b(Q3_K(?:_XL|_L|_M|_S)?)\b',
        r'\b(Q2_K(?:_XL)?)\b',
        r'\b(IQ3_XXS|IQ3_XS|IQ3_M|IQ3_S)\b',
        r'\b(IQ4_XXS|IQ4_XS|IQ4_NL)\b',
        r'\b(IQ2_XXS|IQ2_XS|IQ2_S|IQ2_M)\b',
        r'\b(IQ1_[MS])\b',
        r'\b(MXFP4(?:_MOE)?)\b',
        r'\b(BF16)\b',
        r'\b(F16|F32)\b',
    ]
    for pat in patterns:
        m = re.search(pat, name)
        if m:
            return m.group(1)

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


def _hf_search_models(
    query: str,
    limit: int,
    sort: str,
    verify_gguf: bool = False,
) -> tuple[dict, bool, float]:
    """Search Hugging Face models for repos matching the query.

    Fast path (default): metadata-only search using HF API tags and repo names.
    Slow path (verify_gguf=True): inspects each candidate repo for actual GGUF files.

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

    # Cache key includes verify flag so verified and unverified results
    # are cached separately.
    cache_key = f"search:{query}:{sort_key}:{limit}:verify={verify_gguf}"
    raw_results, was_cached, age = _cached_hf_call(
        cache_key, _fetch, sort_key=sort_key,
    )

    if raw_results is None:
        return None, was_cached, age

    # ── Fast path: metadata-only GGUF filtering ──────────────────────────
    has_gguf_meta = _QONDUIT_HF_REQUIRE_GGUF or verify_gguf
    if has_gguf_meta:
        filtered = []
        for candidate in raw_results:
            tags_lower = [t.lower() for t in candidate.get("tags", [])]
            repo_lower = candidate["repo_id"].lower()
            if "gguf" in tags_lower or "gguf" in repo_lower:
                # Metadata indicates GGUF; do NOT inspect files here.
                candidate["gguf_count"] = None  # unknown without inspection
                candidate["sample_gguf_files"] = []
                candidate["gguf_verified"] = False
                filtered.append(candidate)
            else:
                # No GGUF in metadata — skip entirely when require_gguf
                if _QONDUIT_HF_REQUIRE_GGUF:
                    continue
                # When only verify_gguf=True but require_gguf=False, keep
                # the result but mark it as unverified.
                candidate["gguf_count"] = 0
                candidate["sample_gguf_files"] = []
                candidate["gguf_verified"] = False
                filtered.append(candidate)
        raw_results = filtered

    # Add parameter sizes from metadata only (fast, no API call to repo endpoint).
    for item in raw_results:
        if "gguf_count" not in item:
            item["gguf_count"] = 0
            item["sample_gguf_files"] = []
        if "gguf_verified" not in item:
            item["gguf_verified"] = False
        if "parameter_size" not in item:
            fp, fn, fu, fa, fan = _parse_total_and_active_params(
                item.get("model_id", item.get("repo_id", "")),
            )
            item["parameter_size"] = fp
            item["parameter_size_num"] = fn
            item["parameter_size_unit"] = fu
            item["parameter_size_active"] = fa
            item["parameter_size_active_num"] = fan

    # ── Slow path: inspect each candidate for actual GGUF files ──────────
    if verify_gguf:
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
                candidate["gguf_verified"] = True
                candidate["parameter_size"] = gguf_info.get("parameter_size")
                candidate["parameter_size_num"] = gguf_info.get("parameter_size_num")
                candidate["parameter_size_unit"] = gguf_info.get("parameter_size_unit")
                candidate["parameter_size_active"] = gguf_info.get("parameter_size_active")
                candidate["parameter_size_active_num"] = gguf_info.get("parameter_size_active_num")
                verified.append(candidate)
            if len(verified) >= limit:
                break
        raw_results = verified

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

    # Extract repo ID for parameter size inference
    repo_parts = repo_id.split("/")
    repo_name = repo_parts[-1] if repo_parts else repo_id

    # Infer parameter size from repo name
    param_total, param_num, param_unit, param_active_total, param_active_num = _parse_total_and_active_params(repo_name)

    gguf_files = []
    for sibling in siblings:
        if not isinstance(sibling, dict):
            continue
        path = sibling.get("rfilename") or sibling.get("path", "")
        if path.lower().endswith(".gguf"):
            filename = Path(path).name
            quant = _parse_quant_from_filename(path)

            # Start with sibling-provided size
            size = sibling.get("size") if isinstance(sibling.get("size"), (int, float)) else None

            # If size is missing/zero, try HEAD request to resolve URL
            if not size or size == 0:
                resolve_url = f"https://huggingface.co/{repo_id}/resolve/main/{path}"
                head_size = _get_file_size_via_head(resolve_url, timeout=_QONDUIT_HF_NETWORK_TIMEOUT)
                if head_size and head_size > 0:
                    size = head_size

            # Build size fields
            sf = _size_fields(size)

            # Infer parameter size from filename
            fp, fn, fu, fa, fan = _parse_total_and_active_params(filename)

            # Build downloadable URL
            blob_url = f"https://huggingface.co/{repo_id}/blob/main/{path}"
            resolve_url = f"https://huggingface.co/{repo_id}/resolve/main/{path}"

            gguf_files.append({
                "filename": filename,
                "path": path,
                "is_gguf": True,
                "quant": quant,
                "downloadable": True,
                "url": blob_url,
                "resolve_url": resolve_url,
                **sf,
                "parameter_size": fp,
                "parameter_size_num": fn,
                "parameter_size_unit": fu,
                "parameter_size_active": fa,
                "parameter_size_active_num": fan,
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
        "parameter_size": param_total,
        "parameter_size_num": param_num,
        "parameter_size_unit": param_unit,
        "parameter_size_active": param_active_total,
        "parameter_size_active_num": param_active_num,
    }


# ── Download Job Helpers ─────────────────────────────────────────────────────

def _persist_download_jobs() -> None:
    """Persist download job state and queue to disk."""
    try:
        with _download_jobs_lock:
            jobs_snapshot = {
                jid: dict(job) for jid, job in _download_jobs.items()
            }
        with _download_queue_lock:
            queue_snapshot = list(_download_queue)
        snapshot = {
            "jobs": jobs_snapshot,
            "queue": queue_snapshot,
        }
        with open(_DOWNLOAD_JOBS_FILE, "w") as f:
            json.dump(snapshot, f, indent=2)
    except OSError:
        pass


def _load_download_jobs() -> None:
    """Load persisted download jobs from disk on startup.

    Supports both the new format ({"jobs": {...}, "queue": [...]}) and
    the legacy format (raw dict of jobs) for backward compatibility.

    - "downloading" jobs are marked "interrupted" (cannot resume mid-download).
    - "queued" jobs are preserved as "queued" so the worker picks them up.
    - The in-memory queue list is rebuilt from queued jobs.
    - The worker thread is started automatically if queued jobs exist.
    """
    global _download_active_count
    try:
        if not _DOWNLOAD_JOBS_FILE.exists():
            return
        with open(_DOWNLOAD_JOBS_FILE, "r") as f:
            raw: dict = json.load(f)

        # Handle both new format and legacy format
        if isinstance(raw, dict) and "jobs" in raw and "queue" in raw:
            # New format
            jobs_snapshot: dict[str, dict[str, Any]] = raw["jobs"]
            queue_snapshot: list[str] = raw.get("queue", [])
        elif isinstance(raw, dict):
            # Legacy format: raw dict of jobs
            jobs_snapshot = raw
            queue_snapshot = []
        else:
            return

        with _download_jobs_lock:
            for jid, job in jobs_snapshot.items():
                status = job.get("status", "failed")
                if status == "downloading":
                    # Cannot resume an in-progress download; mark interrupted
                    job["status"] = "interrupted"
                    job["error"] = "Service restart; in-progress downloads not resumed."
                    job["completed_at"] = datetime.now(timezone.utc).isoformat()
                elif status == "queued":
                    # Queued jobs were never started; keep them as queued
                    job["status"] = "queued"
                    job["error"] = None
                    job["completed_at"] = None
                # All other statuses (complete, failed, cancelled, interrupted)
                # are preserved as-is.
                _download_jobs[jid] = job

            # Count currently active downloads (should be 0 after restart)
            _download_active_count = sum(
                1 for j in _download_jobs.values()
                if j.get("status") == "downloading"
            )

        # Rebuild the in-memory queue from persisted queue list + queued jobs
        with _download_queue_lock:
            _download_queue.clear()
            for jid in queue_snapshot:
                if jid in _download_jobs and _download_jobs[jid].get("status") == "queued":
                    _download_queue.append(jid)
            # Also scan for any queued jobs not in the persisted queue
            for jid, job in _download_jobs.items():
                if job.get("status") == "queued" and jid not in _download_queue:
                    _download_queue.append(jid)

        # Start the worker if there are queued jobs
        if _download_queue:
            _ensure_download_worker_started()

    except (json.JSONDecodeError, OSError):
        pass


def _prune_completed_jobs(max_retained: int = 50) -> None:
    """Remove old completed/failed/cancelled/interrupted jobs to prevent unbounded growth."""
    with _download_jobs_lock:
        keep = []
        for jid, job in sorted(_download_jobs.items(), key=lambda x: x[1].get("completed_at", "") or ""):
            status = job.get("status", "")
            if status in ("downloading", "queued"):
                keep.append(jid)
        if len(keep) >= max_retained:
            keep = keep[-max_retained:]
        for jid in list(_download_jobs.keys()):
            if jid not in keep:
                del _download_jobs[jid]


def _validate_repo_id(repo_id: str) -> Optional[str]:
    """Validate a Hugging Face repo_id format."""
    if not repo_id or not isinstance(repo_id, str):
        return "repo_id_required"
    repo_id = repo_id.strip()
    if not repo_id:
        return "repo_id_required"
    # Must match owner/name pattern
    if not re.match(r'^[a-zA-Z0-9_][a-zA-Z0-9_.-]*/[a-zA-Z0-9_][a-zA-Z0-9_.-]*$', repo_id):
        return "invalid_repo_id_format"
    return None


def _validate_download_filename(filename: str) -> Optional[str]:
    """Validate a download filename for safety.

    Allows nested paths (like 'BF16/model-00001.gguf') but blocks traversal.
    """
    if not filename or not isinstance(filename, str):
        return "filename_required"

    filename = filename.strip()
    if not filename:
        return "filename_required"

    # Reject absolute paths anywhere
    if filename.startswith("/"):
        return "absolute_path_rejected"

    # Reject backslash paths
    if "\\" in filename:
        return "path_traversal_blocked"

    # Reject path traversal sequences
    parts = filename.replace("\\", "/").split("/")
    for part in parts:
        if part == "..":
            return "path_traversal_blocked"

    # Reject empty path components
    parts_clean = [p for p in parts if p]
    if not parts_clean:
        return "invalid_filename"

    # Validate each path component is a safe filename
    for part in parts_clean:
        if not part or part.startswith(".") and part not in (".", ".."):
            # Allow hidden dirs but not hidden-only paths
            pass
        if len(part) > 255:
            return "filename_too_long"

    # GGUF-only enforcement
    last_part = parts_clean[-1]
    if not _QONDUIT_HF_ALLOW_NON_GGUF and not last_part.lower().endswith(".gguf"):
        return "non_gguf_blocked"

    return None


def _validate_target_name(target_name: str) -> Optional[str]:
    """Validate a target filename for the local filesystem."""
    if not target_name or not isinstance(target_name, str):
        return "target_name_required"

    target_name = target_name.strip()
    if not target_name:
        return "target_name_required"

    # Reject absolute paths
    if target_name.startswith("/") or target_name.startswith("\\"):
        return "absolute_path_rejected"

    # Reject path traversal
    if ".." in target_name or "/" in target_name or "\\" in target_name:
        return "path_traversal_blocked"

    # Only allow .gguf files by default
    if not _QONDUIT_HF_ALLOW_NON_GGUF and not target_name.lower().endswith(".gguf"):
        return "non_gguf_blocked"

    # Reject empty basename
    basename = Path(target_name).name
    if not basename:
        return "invalid_target_name"

    return None


def _resolve_target_path(target_name: str) -> Optional[Path]:
    """Resolve target path safely within the model directory."""
    model_dir = _get_model_dir()
    if not model_dir.exists():
        return None
    resolved = (model_dir / target_name).resolve()
    try:
        resolved.relative_to(model_dir.resolve())
    except ValueError:
        return None
    return resolved


def _clean_partial_file(partial_path: Path) -> None:
    """Remove a stale partial download file."""
    try:
        if partial_path.exists():
            partial_path.unlink()
    except OSError:
        pass


def _make_hf_resolve_url(repo_id: str, filename: str) -> str:
    """Build a Hugging Face resolve URL for a file, safely encoding the path."""
    from urllib.parse import quote
    # Encode each path segment separately to preserve slashes
    parts = filename.split("/")
    encoded_parts = [quote(p, safe="") for p in parts]
    encoded_path = "/".join(encoded_parts)
    return f"https://huggingface.co/{repo_id}/resolve/main/{encoded_path}"


def _format_job(job: dict[str, Any]) -> dict[str, Any]:
    """Format a job dict for API response (remove internal fields)."""
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "repo_id": job["repo_id"],
        "filename": job["filename"],
        "target_name": job["target_name"],
        "target_path": job["target_path"],
        "bytes_downloaded": job.get("bytes_downloaded", 0),
        "total_bytes": job.get("total_bytes"),
        "progress": job.get("progress"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "error": job.get("error"),
        "cancel_requested": job.get("cancel_requested", False),
    }


def _download_worker() -> None:
    """Background worker thread that processes download jobs from the queue.

    Runs in a loop, picking up jobs when slots are available.
    Exits when _download_shutdown_event is set.
    """
    global _download_active_count

    chunk_size = 8 * 1024 * 1024  # 8 MiB chunks

    while not _download_shutdown_event.is_set():
        job_id = None

        # Wait for a job from the queue
        with _download_queue_lock:
            if _download_queue:
                job_id = _download_queue.pop(0)
            else:
                # No jobs available; wait briefly before checking again
                # Use a short sleep with shutdown event check so we can exit promptly
                import time as _time
                _download_shutdown_event.wait(timeout=0.5)
                continue

        if job_id is None:
            continue

        # Acquire a download slot
        with _download_active_count_lock:
            if _download_active_count >= _QONDUIT_HF_DOWNLOAD_MAX_CONCURRENT:
                # No slots available; re-queue
                with _download_queue_lock:
                    _download_queue.insert(0, job_id)
                continue
            _download_active_count += 1

        # Process the job
        job: Optional[dict[str, Any]] = None
        with _download_jobs_lock:
            if job_id in _download_jobs:
                job = _download_jobs[job_id]

        if job is None:
            # Job was removed while we were waiting
            with _download_active_count_lock:
                _download_active_count -= 1
            continue

        # Mark as downloading
        job["status"] = "downloading"
        job["started_at"] = datetime.now(timezone.utc).isoformat()
        job["error"] = None
        job["bytes_downloaded"] = 0
        job["progress"] = 0.0
        _persist_download_jobs()

        partial_path = Path(job.get("partial_path", ""))
        target_path = Path(job.get("target_path", ""))
        total_bytes = job.get("total_bytes")
        cancel_requested = False

        try:
            download_url = job.get("download_url", "")
            if not download_url:
                raise ValueError("No download URL available")

            headers = {}
            if _QONDUIT_HF_TOKEN:
                headers["Authorization"] = f"Bearer {_QONDUIT_HF_TOKEN}"

            # Clean up stale partial file if it exists
            if partial_path.exists():
                _clean_partial_file(partial_path)

            with requests.get(
                download_url,
                stream=True,
                timeout=(_QONDUIT_HF_NETWORK_TIMEOUT * 30),
                headers=headers,
            ) as resp:
                if resp.status_code == 401 or resp.status_code == 403:
                    raise ValueError(
                        f"Hugging Face authentication failed (status {resp.status_code}). "
                        "Check HF_TOKEN for gated/private repos."
                    )
                if resp.status_code == 404:
                    raise ValueError(f"File not found on Hugging Face (HTTP {resp.status_code})")
                if resp.status_code != 200:
                    raise ValueError(f"Download failed with HTTP {resp.status_code}")

                # Get total size from Content-Length if available
                resp_total = resp.headers.get("Content-Length")
                if resp_total and total_bytes is None:
                    try:
                        total_bytes = int(resp_total)
                        job["total_bytes"] = total_bytes
                    except (ValueError, TypeError):
                        pass

                with open(partial_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=chunk_size):
                        if not chunk:
                            continue

                        # Check shutdown / cancellation between chunks
                        if _download_shutdown_event.is_set():
                            _clean_partial_file(partial_path)
                            with _download_jobs_lock:
                                job["status"] = "interrupted"
                                job["error"] = "shutdown"
                                job["completed_at"] = datetime.now(timezone.utc).isoformat()
                            _persist_download_jobs()
                            raise KeyboardInterrupt("Worker shutting down")

                        with _download_jobs_lock:
                            if job.get("cancel_requested", False):
                                cancel_requested = True
                                break

                        f.write(chunk)
                        job["bytes_downloaded"] = f.tell()

                        if total_bytes and total_bytes > 0:
                            job["progress"] = min(1.0, job["bytes_downloaded"] / total_bytes)
                        else:
                            job["progress"] = None

                        _persist_download_jobs()

                if cancel_requested:
                    # Cancelled during download
                    _clean_partial_file(partial_path)
                    with _download_jobs_lock:
                        job["status"] = "cancelled"
                        job["completed_at"] = datetime.now(timezone.utc).isoformat()
                    _persist_download_jobs()
                    continue

            # Download completed successfully — atomically rename partial to final
            if target_path.exists() and not job.get("overwrite", False):
                with _download_jobs_lock:
                    job["status"] = "failed"
                    job["error"] = "target_file_exists"
                    job["completed_at"] = datetime.now(timezone.utc).isoformat()
                _persist_download_jobs()
                continue

            # Atomic rename
            try:
                # If overwriting, write to temp then rename
                if target_path.exists():
                    # Overwrite: write to temp then rename
                    temp_target = target_path.with_suffix(target_path.suffix + ".tmp")
                    partial_path.rename(temp_target)
                    temp_target.replace(target_path)
                else:
                    partial_path.rename(target_path)
            except OSError as e:
                raise OSError(f"Failed to finalize download: {e}")

            # Mark complete
            with _download_jobs_lock:
                job["status"] = "complete"
                job["progress"] = 1.0
                job["completed_at"] = datetime.now(timezone.utc).isoformat()
            _persist_download_jobs()

            # Invalidate model list cache if it exists
            try:
                if hasattr(app, "model_list_cache"):
                    app.model_list_cache = None
                    app.model_list_cache_time = 0
            except Exception:
                pass

        except Exception as e:
            # Clean up partial on failure
            _clean_partial_file(partial_path)
            with _download_jobs_lock:
                job["status"] = "failed"
                job["error"] = str(e)
                job["completed_at"] = datetime.now(timezone.utc).isoformat()
            _persist_download_jobs()
        finally:
            with _download_active_count_lock:
                _download_active_count -= 1
            # Prune old jobs periodically
            _prune_completed_jobs()


def _start_download_worker() -> None:
    """Start the background download worker thread if not already running."""
    global _download_worker_thread
    if _download_worker_thread is not None and _download_worker_thread.is_alive():
        return
    _download_worker_thread = threading.Thread(
        target=_download_worker,
        name="qonduit-hf-download-worker",
        daemon=True,
    )
    _download_worker_thread.start()


def _stop_download_worker() -> None:
    """Stop the background download worker thread (for testing/teardown)."""
    global _download_worker_thread
    _download_shutdown_event.set()
    if _download_worker_thread is not None and _download_worker_thread.is_alive():
        _download_worker_thread.join(timeout=5)
    _download_worker_thread = None


# ── Endpoint: POST /hf/download ─────────────────────────────────────────────

@app.post("/api/v1/qonduit-router/hf/download")
def qonduit_hf_download():
    """Start a Hugging Face GGUF download job."""
    try:
        denied = _require_local()
        if denied:
            return denied

        payload = request.get_json(silent=True) or {}
        repo_id = (payload.get("repo_id") or "").strip()
        filename = (payload.get("filename") or "").strip()
        target_name = (payload.get("target_name") or "").strip() or None
        overwrite = bool(payload.get("overwrite", False))
        dry_run = bool(payload.get("dry_run", False))

        # Validate repo_id
        error = _validate_repo_id(repo_id)
        if error:
            return jsonify({"ok": False, "error": "invalid_repo_id", "detail": error}), 400

        # Validate filename
        error = _validate_download_filename(filename)
        if error:
            return jsonify({"ok": False, "error": "invalid_filename", "detail": error}), 400

        # Determine target_name
        if not target_name:
            target_name = Path(filename).name

        # Validate target_name
        error = _validate_target_name(target_name)
        if error:
            return jsonify({"ok": False, "error": "invalid_target_name", "detail": error}), 400

        # Resolve target path
        target_path = _resolve_target_path(target_name)
        if target_path is None:
            return jsonify({
                "ok": False,
                "error": "path_resolution_failed",
                "detail": "Could not resolve target path within model directory.",
            }), 500

        # Build download URL
        download_url = _make_hf_resolve_url(repo_id, filename)

        if dry_run:
            # Validate without creating a job
            exists = target_path.exists()
            # Try to get file size via HEAD
            size_bytes = None
            size_human = "unknown"
            try:
                head_resp = requests.head(
                    download_url,
                    timeout=_QONDUIT_HF_NETWORK_TIMEOUT,
                    headers={"Authorization": f"Bearer {_QONDUIT_HF_TOKEN}"} if _QONDUIT_HF_TOKEN else {},
                    allow_redirects=True,
                )
                if head_resp.status_code in (200, 206, 301, 302, 307, 308):
                    cl = head_resp.headers.get("Content-Length")
                    if cl:
                        try:
                            size_bytes = int(cl)
                            size_human = _human_size(size_bytes)
                        except (ValueError, TypeError):
                            pass
            except (requests.RequestException, OSError, ValueError):
                pass

            return jsonify({
                "ok": True,
                "dry_run": True,
                "repo_id": repo_id,
                "filename": filename,
                "target_name": target_name,
                "target_path": str(target_path),
                "exists": exists,
                "downloadable": True,
                "size_bytes": size_bytes,
                "size_human": size_human,
            })

        # Check if target already exists
        if not overwrite and target_path.exists():
            return jsonify({
                "ok": False,
                "error": "model_exists",
                "detail": f"Target file already exists: {target_name}. Set overwrite=true to replace.",
                "target_path": str(target_path),
            }), 409

        # Check for stale partial file
        partial_path = Path(f"/mnt/models/llm/.partial.{target_name}.download")
        if partial_path.exists():
            return jsonify({
                "ok": False,
                "error": "partial_exists",
                "detail": f"Stale partial download file exists: {partial_path}. Delete manually or wait.",
                "partial_path": str(partial_path),
            }), 409

        # Create job
        job_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        job = {
            "job_id": job_id,
            "status": "queued",
            "repo_id": repo_id,
            "filename": filename,
            "target_name": target_name,
            "target_path": str(target_path),
            "partial_path": str(partial_path),
            "download_url": download_url,
            "overwrite": overwrite,
            "bytes_downloaded": 0,
            "total_bytes": None,
            "progress": 0.0,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "cancel_requested": False,
        }

        with _download_jobs_lock:
            _download_jobs[job_id] = job

        with _download_queue_lock:
            _download_queue.append(job_id)

        _ensure_download_worker_started()
        _persist_download_jobs()

        return jsonify({
            "ok": True,
            "job_id": job_id,
            "status": "queued",
            "repo_id": repo_id,
            "filename": filename,
            "target_name": target_name,
            "target_path": str(target_path),
            "dry_run": False,
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": "download_start_failed",
            "detail": str(e),
        }), 500


# ── Endpoint: GET /hf/downloads ─────────────────────────────────────────────

@app.get("/api/v1/qonduit-router/hf/downloads")
def qonduit_hf_downloads_list():
    """List all download jobs."""
    try:
        denied = _require_local()
        if denied:
            return denied

        # Check if worker thread is alive; restart if needed
        _maybe_restart_download_worker()

        with _download_jobs_lock:
            jobs_snapshot = dict(_download_jobs)

        jobs = []
        active_count = 0
        queued_count = 0
        for jid, job in sorted(jobs_snapshot.items(), key=lambda x: x[1].get("started_at", "") or "", reverse=True):
            jobs.append(_format_job(job))
            if job.get("status") == "downloading":
                active_count += 1
            elif job.get("status") == "queued":
                queued_count += 1

        return jsonify({
            "ok": True,
            "jobs": jobs,
            "active_count": active_count,
            "queued_count": queued_count,
            "worker_alive": _download_worker_thread is not None and _download_worker_thread.is_alive(),
            "total": len(jobs),
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": "downloads_list_failed",
            "detail": str(e),
        }), 500


# ── Endpoint: GET /hf/downloads/<job_id> ────────────────────────────────────

@app.get("/api/v1/qonduit-router/hf/downloads/<job_id>")
def qonduit_hf_download_get(job_id: str):
    """Get a specific download job."""
    try:
        denied = _require_local()
        if denied:
            return denied

        with _download_jobs_lock:
            job = _download_jobs.get(job_id)

        if job is None:
            return jsonify({
                "ok": False,
                "error": "job_not_found",
                "detail": f"Job {job_id} not found.",
            }), 404

        return jsonify({
            "ok": True,
            "job": _format_job(job),
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": "download_get_failed",
            "detail": str(e),
        }), 500


# ── Endpoint: POST /hf/downloads/<job_id>/cancel ────────────────────────────

@app.post("/api/v1/qonduit-router/hf/downloads/<job_id>/cancel")
def qonduit_hf_download_cancel(job_id: str):
    """Cancel a download job."""
    try:
        denied = _require_local()
        if denied:
            return denied

        with _download_jobs_lock:
            job = _download_jobs.get(job_id)

            if job is None:
                return jsonify({
                    "ok": False,
                    "error": "job_not_found",
                    "detail": f"Job {job_id} not found.",
                }), 404

            status = job.get("status", "")

            if status in ("complete", "failed", "cancelled", "interrupted"):
                return jsonify({
                    "ok": False,
                    "error": f"already_{status}",
                    "detail": f"Job is already {status}.",
                }), 409

            job["cancel_requested"] = True

        # Persist outside the lock to avoid deadlock
        # ( _persist_download_jobs acquires _download_jobs_lock )
        _persist_download_jobs()

        return jsonify({
            "ok": True,
            "job_id": job_id,
            "status": "cancelling",
            "detail": "Cancellation requested. The job will stop after the current chunk.",
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": "download_cancel_failed",
            "detail": str(e),
        }), 500


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

        # Optional: request full GGUF verification (slow)
        verify_gguf = request.args.get("verify", "false").lower() == "true"

        # Rate-limit identical queries
        cooldown_key = f"cooldown:{query}:{sort}"
        now = time.time()
        in_cooldown = False
        if cooldown_key in _hf_search_cooldown:
            last_time = _hf_search_cooldown[cooldown_key]
            elapsed = now - last_time
            if elapsed < _QONDUIT_HF_SEARCH_COOLDOWN:
                in_cooldown = True

        # Check result cache FIRST (before rate-limit blocks)
        cache_key = f"search:{query}:{sort}:{limit}:verify={verify_gguf}"
        cached_results = None
        cached_age = 0
        if cache_key in _hf_cache:
            raw_results, cached_at = _hf_cache[cache_key]
            if raw_results is not None and isinstance(raw_results, list):
                cached_results = raw_results
                cached_age = round(now - cached_at, 1)

        # If cache exists, return it (with rate_limited flag if in cooldown)
        if cached_results is not None:
            hf_models_url = f"https://huggingface.co/models?search={requests.utils.quote(query)}"
            resp = {
                "ok": True,
                "query": query,
                "count": len(cached_results),
                "limit": limit,
                "sort": sort,
                "require_gguf": _QONDUIT_HF_REQUIRE_GGUF,
                "source": "huggingface",
                "hf_models_url": hf_models_url,
                "cached": True,
                "cache_age_seconds": cached_age,
                "results": cached_results,
                "error": None,
            }
            if in_cooldown:
                resp["rate_limited"] = True
                resp["retry_after_seconds"] = round(_QONDUIT_HF_SEARCH_COOLDOWN - (now - _hf_search_cooldown[cooldown_key]), 1)
            return jsonify(resp)

        # If in cooldown and NO cache, return 429
        if in_cooldown:
            retry_after = round(_QONDUIT_HF_SEARCH_COOLDOWN - (now - _hf_search_cooldown[cooldown_key]), 1)
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

        # Fetch from HF
        results, was_cached, cache_age = _hf_search_models(
            query, limit, sort, verify_gguf=verify_gguf,
        )

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

        # Check cooldown for repo files
        cooldown_key = f"cooldown:repofiles:{repo_id}"
        now = time.time()
        in_cooldown = False
        if cooldown_key in _hf_search_cooldown:
            last_time = _hf_search_cooldown[cooldown_key]
            elapsed = now - last_time
            if elapsed < _QONDUIT_HF_SEARCH_COOLDOWN:
                in_cooldown = True

        # Check result cache FIRST
        cache_key = f"repofiles:{repo_id}"
        cached_results = None
        cached_age = 0
        if cache_key in _hf_cache:
            raw_results, cached_at = _hf_cache[cache_key]
            if raw_results is not None and isinstance(raw_results, dict):
                cached_results = raw_results
                cached_age = round(now - cached_at, 1)

        # If cache exists, return it (with rate_limited flag if in cooldown)
        if cached_results is not None:
            resp = {
                "ok": True,
                "repo_id": repo_id,
                "url": cached_results.get("url", f"https://huggingface.co/{repo_id}"),
                "gguf_count": cached_results.get("gguf_count", 0),
                "gated": cached_results.get("gated", False),
                "private": cached_results.get("private", False),
                "cached": True,
                "cache_age_seconds": cached_age,
                "files": cached_results.get("files", []),
                "parameter_size": cached_results.get("parameter_size"),
                "parameter_size_num": cached_results.get("parameter_size_num"),
                "parameter_size_unit": cached_results.get("parameter_size_unit"),
                "parameter_size_active": cached_results.get("parameter_size_active"),
                "parameter_size_active_num": cached_results.get("parameter_size_active_num"),
                "error": None,
            }
            if in_cooldown:
                resp["rate_limited"] = True
                resp["retry_after_seconds"] = round(_QONDUIT_HF_SEARCH_COOLDOWN - (now - _hf_search_cooldown[cooldown_key]), 1)
            return jsonify(resp)

        # If in cooldown and NO cache, return 429
        if in_cooldown:
            retry_after = round(_QONDUIT_HF_SEARCH_COOLDOWN - (now - _hf_search_cooldown[cooldown_key]), 1)
            return jsonify({
                "ok": False,
                "error": "rate_limited",
                "repo_id": repo_id,
                "url": f"https://huggingface.co/{repo_id}",
                "gguf_count": 0,
                "files": [],
                "cached": False,
                "retry_after_seconds": retry_after,
            }), 429

        _hf_search_cooldown[cooldown_key] = now

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
            "parameter_size": result.get("parameter_size"),
            "parameter_size_num": result.get("parameter_size_num"),
            "parameter_size_unit": result.get("parameter_size_unit"),
            "parameter_size_active": result.get("parameter_size_active"),
            "parameter_size_active_num": result.get("parameter_size_active_num"),
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


# ── Endpoint: POST /models/trash/delete ─────────────────────────────────────

@app.post("/api/v1/qonduit-router/models/trash/delete")
def qonduit_model_trash_delete():
    """Permanently delete (empty) a trashed model file.

    This is the final destruction step after a model was moved to trash.
    Only .gguf files in the trash directory are eligible.
    """
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
                "detail": "Set confirm=true to permanently delete a trashed file.",
            }), 400

        model_dir = _get_model_dir()
        trash_dir = (model_dir / ".trash").resolve()

        if not trash_dir.exists():
            return jsonify({
                "ok": False,
                "error": "trash_dir_not_found",
                "detail": "Trash directory does not exist.",
            }), 404

        trash_path = (trash_dir / trash_name).resolve()

        # Safety: must be under trash dir (prevents path traversal)
        try:
            trash_path.relative_to(trash_dir)
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

        # Only allow .gguf files for safety
        if not trash_path.name.lower().endswith(".gguf"):
            return jsonify({
                "ok": False,
                "error": "non_gguf_rejected",
                "detail": f"Only .gguf files can be permanently deleted. Refused: {trash_name}",
            }), 403

        # Check if the trashed file belongs to the running model
        parts = trash_name.split("-", 1)
        original_name = parts[1] if len(parts) > 1 else trash_name
        state = _read_state()
        running_model = state.get("model", "")
        if running_model and running_model == original_name:
            return jsonify({
                "ok": False,
                "error": "model_running",
                "detail": "Cannot permanently delete the currently running model.",
                "running_model": running_model,
            }), 409

        trash_path.unlink()

        return jsonify({
            "ok": True,
            "deleted": True,
            "trash_name": trash_name,
            "original_name": original_name,
            "path": str(trash_path),
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": "trash_delete_failed",
            "detail": str(e),
        }), 500


# ── Startup initialization ──────────────────────────────────────────────────
# Load persisted jobs at import time (fast, no threads).
_load_download_jobs()

# Start the download worker lazily (only when first used).
# This prevents the worker thread from blocking during test imports.
_download_worker_started = False


def _maybe_restart_download_worker() -> None:
    """Restart the download worker thread if it died but there are queued jobs.

    This handles the case where the worker thread crashed or exited but
    queued jobs remain in memory (e.g., after a transient error).
    """
    global _download_worker_started
    worker_dead = (
        _download_worker_thread is None
        or not _download_worker_thread.is_alive()
    )
    if worker_dead:
        with _download_queue_lock:
            has_queued = bool(_download_queue)
        if has_queued or _download_worker_started:
            _download_worker_started = True
            _start_download_worker()


def _ensure_download_worker_started() -> None:
    """Start the download worker thread on first use (lazy initialization)."""
    global _download_worker_started
    if not _download_worker_started:
        _download_worker_started = True
        _start_download_worker()


# ── Register multi-slot endpoints (Phases 3-7) ──────────────────────────────

register_slot_routes(app)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
