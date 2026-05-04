from __future__ import annotations

import os
import time
import requests
import subprocess
from pathlib import Path
from typing import Optional

from flask import Flask, jsonify, request, Response

app = Flask(__name__)

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


def _qonduit_model_list() -> list[str]:
    if not QONDUIT_MODEL_DIR.exists():
        return []
    return sorted(
        [p.name for p in QONDUIT_MODEL_DIR.glob("*.gguf")],
        key=str.lower,
    )


def _qonduit_suggested_ctx() -> int:
    # Keep it simple for now. Match your current launcher default behavior closely.
    # You can replace this later with smarter heuristics by file size or VRAM.
    return 65536


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
    return jsonify(
        {
            "ok": True,
            "models": models,
            "suggested_context": _qonduit_suggested_ctx(),
            "count": len(models),
        }
    )


@app.get("/api/v1/qonduit-router/status")
def qonduit_status():
    denied = _require_local()
    if denied:
        return denied

    return jsonify(
        {
            "ok": True,
            "container_name": QONDUIT_CONTAINER_NAME,
            "running": _docker_container_running(),
            "exists": _docker_container_exists(),
            "webui_base": QONDUIT_WEBUI_BASE,
            "llama_base": QONDUIT_LLAMA_BASE,
        }
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

    proc = subprocess.Popen(
        ["sudo", QONDUIT_SCRIPT_PATH, model, context],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return jsonify(
        {
            "ok": True,
            "started": True,
            "pid": proc.pid,
            "model": model,
            "context_size": int(context),
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

    return jsonify({"ok": True, "stopped": True, "container_name": QONDUIT_CONTAINER_NAME})

@app.get("/api/v1/qonduit-router/logs")
def qonduit_logs():
    denied = _require_local()
    if denied:
        return denied

    def generate():
        while True:
            exists = subprocess.run(
                ["sudo", "docker", "ps", "-a", "-q", "-f", f"name={QONDUIT_CONTAINER_NAME}"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout.strip()

            if not exists:
                yield "[qonduit] waiting for llama_server container...\n"
                time.sleep(2)
                continue

            proc = subprocess.Popen(
                ["sudo", "docker", "logs", "--tail", "200", "-f", QONDUIT_CONTAINER_NAME],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            try:
                assert proc.stdout is not None
                for line in iter(proc.stdout.readline, ""):
                    yield line
            except GeneratorExit:
                proc.kill()
                raise
            finally:
                try:
                    proc.kill()
                except Exception:
                    pass

            yield "[qonduit] log stream ended, reconnecting...\n"
            time.sleep(2)

    return Response(generate(), mimetype="text/plain")

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
