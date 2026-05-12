"""
Qonduit Router — slot-aware Docker helpers.

All functions take a slot dict and operate on that specific slot's container.
No function touches another slot's container.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Optional

import requests

# ── Configuration ────────────────────────────────────────────────────────────

_QONDUIT_LLAMA_IMAGE = os.getenv(
    "QONDUIT_LLAMA_IMAGE",
    "ghcr.io/ggerganov/llama.cpp:server",
)
_QONDUIT_LLAMA_SERVER_BIN = os.getenv(
    "QONDUIT_LLAMA_SERVER_BIN",
    "./build/bin/llama-server",
)
_QONDUIT_MODEL_MOUNT = "/mnt/models"
_QONDUIT_DOCKER_NETWORK = os.getenv("QONDUIT_DOCKER_NETWORK", "host")

# ── Docker subprocess helpers ────────────────────────────────────────────────


def _docker_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """Run a docker command with sudo."""
    return subprocess.run(
        ["sudo", "docker"] + args,
        capture_output=True,
        text=True,
        check=False,
        timeout=kwargs.pop("timeout", 30),
    )


def _docker_name_filter(name: str) -> str:
    """Return docker -f name= filter (exact match with slash prefix)."""
    return f"name=/{name}"


# ── Container existence/running checks ───────────────────────────────────────

def container_exists(slot: dict[str, Any]) -> bool:
    """Check if any container (running or stopped) exists for this slot."""
    result = _docker_run(
        [
            "ps", "-a", "-q",
            "-f", _docker_name_filter(slot["container_name"]),
        ]
    )
    return bool(result.stdout.strip())


def container_running(slot: dict[str, Any]) -> bool:
    """Check if the slot's container is currently running."""
    result = _docker_run(
        [
            "ps", "-q",
            "-f", _docker_name_filter(slot["container_name"]),
        ]
    )
    return bool(result.stdout.strip())


def container_status(slot: dict[str, Any]) -> dict[str, Any]:
    """Get detailed status of the slot's container."""
    info: dict[str, Any] = {
        "exists": False,
        "running": False,
        "container_id": "",
        "image": "",
        "status": "",
        "created_at": "",
        "last_error": None,
    }

    cid = get_container_id(slot)
    if not cid:
        return info

    info["exists"] = True
    info["container_id"] = cid

    # Get container inspect
    result = _docker_run(
        ["inspect", slot["container_name"]],
    )
    if result.returncode != 0:
        info["last_error"] = result.stderr.strip() or "docker inspect failed"
        return info

    try:
        data = json.loads(result.stdout)
        if data and isinstance(data, list):
            container_info = data[0]
            state = container_info.get("State", {})
            info["running"] = state.get("Running", False)
            info["status"] = state.get("Status", "")
            info["image"] = container_info.get("Config", {}).get("Image", "")

            created = container_info.get("Created", "")
            if created:
                info["created_at"] = created

            if not state.get("Running", False):
                exit_code = state.get("ExitCode", -1)
                error_msg = state.get("Error", "")
                if exit_code != 0 or error_msg:
                    info["last_error"] = error_msg or f"exit code {exit_code}"
    except (json.JSONDecodeError, KeyError, IndexError, OSError):
        info["last_error"] = "failed to parse container inspect"

    return info


def get_container_id(slot: dict[str, Any]) -> str:
    """Return container ID if running, empty string otherwise."""
    result = _docker_run(
        [
            "ps", "-q",
            "-f", _docker_name_filter(slot["container_name"]),
        ]
    )
    return result.stdout.strip()


def get_container_labels(slot: dict[str, Any]) -> dict[str, str]:
    """Return labels of the running container for this slot."""
    try:
        result = _docker_run(
            [
                "inspect",
                "-f",
                "{{json .Config.Labels}}",
                slot["container_name"],
            ],
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
    except (json.JSONDecodeError, OSError):
        pass
    return {}


# ── Container lifecycle operations ───────────────────────────────────────────

def stop_slot_container(slot: dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Stop a slot's container. Returns (success, error_string)."""
    if not container_running(slot):
        return True, None  # Already stopped

    result = _docker_run(
        ["stop", "-t", "30", slot["container_name"]],
        timeout=60,
    )
    if result.returncode == 0:
        return True, None
    return False, result.stderr.strip() or "docker stop failed"


def remove_slot_container(slot: dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Remove a slot's container. Returns (success, error_string)."""
    # Stop first if running
    stopped, err = stop_slot_container(slot)
    if not stopped:
        return False, err

    result = _docker_run(
        ["rm", "-f", slot["container_name"]],
        timeout=30,
    )
    if result.returncode == 0:
        return True, None
    return False, result.stderr.strip() or "docker rm failed"


def launch_slot_container(
    slot: dict[str, Any],
    launch_payload: dict[str, Any],
) -> tuple[bool, str, Optional[str]]:
    """
    Launch a Docker container for a slot.

    Returns:
        (success, message, error_string)
    """
    model = (launch_payload.get("model") or slot.get("model") or "").strip()
    if not model:
        return False, "", "model_required"

    model_path = f"{_QONDUIT_MODEL_MOUNT}/llm/{model}"

    # Check model file exists
    if not os.path.exists(model_path):
        return False, "", "model_not_found"

    # Determine context size
    context_size = int(
        launch_payload.get("context_size")
        or slot.get("context_size", 65536),
    )

    # Determine GPU settings
    gpu_devices = launch_payload.get("gpu_devices") or slot.get("gpu_devices", "all")
    tensor_split = launch_payload.get("tensor_split") or slot.get("tensor_split", "auto")
    embeddings = launch_payload.get("embeddings_enabled") or slot.get("embeddings_enabled", False)
    extra_args = launch_payload.get("extra_args") or slot.get("extra_args", [])

    # Compute tensor split value
    if str(tensor_split).lower() == "auto":
        split_val = compute_auto_tensor_split(gpu_devices)
    else:
        split_val = str(tensor_split)

    # Build GPU args
    if str(gpu_devices).lower() == "all":
        gpu_args = ["--gpus", "all"]
    else:
        gpu_args = [
            "--gpus",
            f'"device={str(gpu_devices).strip()}"',
        ]

    # Build command
    cmd = [
        _QONDUIT_LLAMA_SERVER_BIN,
        "--model", model_path,
        "--n-gpu-layers", "-1",
        "--ctx-size", str(context_size),
        "--host", "0.0.0.0",
        "--port", str(slot.get("internal_port", 8080)),
        "--tensor-split", split_val,
    ]

    if embeddings:
        cmd.append("--embeddings")

    cmd.extend(extra_args)

    # Port mapping
    port_map = f"{slot.get('host_port', 8080)}:{slot.get('internal_port', 8080)}"

    # Volume mounts
    volumes = [
        f"{_QONDUIT_MODEL_MOUNT}:{_QONDUIT_MODEL_MOUNT}:ro",
    ]
    volume_args = []
    for v in volumes:
        volume_args.extend(["-v", v])

    # Labels for tracking
    label_args = [
        "--label", f"qonduit.slot={slot['slot_id']}",
        "--label", f"qonduit.model={model}",
        "--label", f"qonduit.context_size={context_size}",
    ]

    # Build full docker run command
    run_cmd = (
        ["sudo", "docker", "run", "-d", "--rm"]
        + gpu_args
        + ["--name", slot["container_name"]]
        + ["-p", port_map]
        + volume_args
        + label_args
        + [_QONDUIT_LLAMA_IMAGE]
        + cmd
    )

    # Check if container already exists
    if container_exists(slot):
        remove_slot_container(slot)

    result = subprocess.run(
        run_cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode == 0:
        # Update slot with launched settings
        from qonduit_slots import update_slot
        update_slot(slot["slot_id"], {
            "model": model,
            "context_size": context_size,
            "gpu_devices": gpu_devices,
            "tensor_split": tensor_split,
            "embeddings_enabled": bool(embeddings),
            "extra_args": extra_args,
        }, force=True)

        # Update timestamps
        from qonduit_slots import load_slots
        slots = load_slots()
        for s in slots:
            if s["slot_id"] == slot["slot_id"]:
                from datetime import datetime, timezone
                s["last_started_at"] = datetime.now(timezone.utc).isoformat()
                s["last_error"] = None
        from qonduit_slots import save_slots
        save_slots(slots)

        return True, f"Container {slot['container_name']} launched", None
    else:
        error_msg = result.stderr.strip() or "docker run failed"
        from qonduit_slots import load_slots, save_slots
        from datetime import datetime, timezone
        slots = load_slots()
        for s in slots:
            if s["slot_id"] == slot["slot_id"]:
                s["last_error"] = error_msg[:500]
        save_slots(slots)
        return False, "", error_msg


def check_slot_ready(slot: dict[str, Any], timeout: float = 2.0) -> bool:
    """Check if a slot's endpoint responds to /health."""
    endpoint = slot.get("endpoint_base", "")
    if not endpoint:
        return False
    try:
        resp = requests.get(f"{endpoint}/health", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def fetch_slot_models(slot: dict[str, Any], timeout: float = 5.0) -> Optional[dict[str, Any]]:
    """Proxy /v1/models from a slot. Returns JSON response or None."""
    openai_base = slot.get("openai_base", "")
    if not openai_base:
        return None
    try:
        resp = requests.get(f"{openai_base}/models", timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def stream_slot_logs(slot: dict[str, Any]) -> tuple[str, int]:
    """
    Stream logs for a slot's container.

    Returns:
        (logs_text, error_code)
        error_code 0 = success, non-zero = error
    """
    try:
        result = subprocess.run(
            [
                "sudo", "docker", "logs",
                "--tail", "500",
                slot["container_name"],
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            logs_text = result.stdout if result.stdout else result.stderr
            return (logs_text if logs_text else "[router] No log output available.\n"), 0

        stderr = result.stderr.strip() if result.stderr else "docker returned non-zero exit"
        label = slot.get('display_name') or slot.get('slot_id') or slot.get('container_name', 'unknown')
        return (
            f"[router] {label} logs unavailable.\n"
            f"[router] docker returned: {stderr}\n"
        ), 1

    except subprocess.TimeoutExpired:
        label = slot.get('display_name') or slot.get('slot_id') or slot.get('container_name', 'unknown')
        return (
            f"[router] {label} logs unavailable.\n"
            f"[router] docker logs timed out.\n"
        ), 1
    except FileNotFoundError:
        label = slot.get('display_name') or slot.get('slot_id') or slot.get('container_name', 'unknown')
        return (
            f"[router] {label} logs unavailable.\n"
            f"[router] docker not found.\n"
        ), 1
    except Exception as e:
        label = slot.get('display_name') or slot.get('slot_id') or slot.get('container_name', 'unknown')
        return (
            f"[router] {label} logs unavailable.\n"
            f"[router] error: {e}\n"
        ), 1


# ── Resource availability checks ─────────────────────────────────────────────

def port_is_available(port: int, exclude_slot_id: str | None = None) -> bool:
    """Check if a host port is not in use by any known slot (except the given one)."""
    from qonduit_slots import load_slots
    slots = load_slots()
    return not any(
        s.get("host_port") == port and s.get("slot_id") != exclude_slot_id
        for s in slots
    )


def docker_name_is_available(name: str, exclude_slot_id: str | None = None) -> bool:
    """Check if a Docker container name is not in use by any known slot (except the given one)."""
    from qonduit_slots import load_slots
    slots = load_slots()
    return not any(
        s.get("container_name") == name and s.get("slot_id") != exclude_slot_id
        for s in slots
    )


def collect_gpu_summary() -> dict[str, Any]:
    """Run nvidia-smi and return GPU summary."""
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

        from qonduit_slots import _format_bytes_human

        gpus = []
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

        return {
            "ok": True,
            "gpus": gpus,
            "memory_total_mib": total_mib,
            "memory_used_mib": used_mib,
            "memory_free_mib": free_mib,
            "memory_total_human": _format_bytes_human(total_mib * 1024 * 1024),
            "memory_used_human": _format_bytes_human(used_mib * 1024 * 1024),
            "memory_free_human": _format_bytes_human(free_mib * 1024 * 1024),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "gpu_query_failed",
            "detail": str(e),
        }


def compute_auto_tensor_split(gpu_devices: Any) -> str:
    """Compute tensor_split from GPU device list.

    If gpu_devices is 'all', returns '1' (single device).
    If gpu_devices is '0,1,2', returns '1,1,1' (equal split).
    """
    gpu_str = str(gpu_devices).strip()
    if gpu_str.lower() == "all":
        return "1"
    devices = [d.strip() for d in gpu_str.split(",")]
    return ",".join(["1"] * len(devices))


# ── Docker availability ──────────────────────────────────────────────────────

def docker_available() -> bool:
    """Check if Docker daemon is reachable."""
    result = _docker_run(["info"], timeout=5)
    return result.returncode == 0
