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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
