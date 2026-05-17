"""
Qonduit Router — slot-aware Docker helpers.

All functions take a slot dict and operate on that specific slot's container.
No function touches another slot's container.
"""

from __future__ import annotations

import json
import importlib
import importlib.util
import os
import re
import subprocess
from typing import Any, Optional


class _RequestsUnavailable:
    """Minimal requests-compatible placeholder for missing test deps."""

    def get(self, *_args: Any, **_kwargs: Any) -> Any:
        """Raise the dependency error when HTTP helpers are actually used."""
        raise ModuleNotFoundError("No module named 'requests'")


requests = (
    importlib.import_module("requests")
    if importlib.util.find_spec("requests") is not None
    else _RequestsUnavailable()
)

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

# ── GPU detection configuration ─────────────────────────────────────────────

_QONDUIT_GPU_MIN_TOTAL_MIB = int(
    os.getenv("QONDUIT_GPU_MIN_TOTAL_MIB", "8192"),
)
_QONDUIT_GPU_EXCLUDE_NAME_REGEX = os.getenv(
    "QONDUIT_GPU_EXCLUDE_NAME_REGEX",
    "K620|Quadro K620",
)
_QONDUIT_DEFAULT_GPU_DEVICES = os.getenv(
    "QONDUIT_DEFAULT_GPU_DEVICES",
    "auto",
)

# Cached results for repeated calls
_usable_gpu_cache: dict[str, Any] = {}

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


def docker_available() -> bool:
    """Check if Docker daemon is reachable and responsive."""
    try:
        result = _docker_run(["info"], timeout=10)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return False


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

    Resolves gpu_devices to usable GPUs (excludes low-memory/display GPUs)
    when gpu_devices is "all".

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

    # Determine GPU settings — resolve "all" to usable GPUs
    gpu_devices = launch_payload.get("gpu_devices") or slot.get("gpu_devices", "all")
    resolved_gpu_devices = resolve_gpu_devices(gpu_devices)
    # Determine tensor_split — distinguish cleared / auto / explicit / inherited
    if "tensor_split" in launch_payload:
        tensor_split = launch_payload["tensor_split"]
        tensor_split_cleared = tensor_split is None or str(tensor_split).strip() == ""
    elif "tensor_split" in slot:
        tensor_split = slot["tensor_split"]
        tensor_split_cleared = tensor_split is None or str(tensor_split).strip() == ""
    else:
        tensor_split = None
        tensor_split_cleared = True
    embeddings = launch_payload.get("embeddings_enabled") or slot.get("embeddings_enabled", False)
    extra_args = launch_payload.get("extra_args") or slot.get("extra_args", [])

    # Determine parallel_slots (default 1)
    raw_parallel_slots = launch_payload.get(
        "parallel_slots",
        slot.get("parallel_slots", 1),
    )
    if raw_parallel_slots is None or (
        isinstance(raw_parallel_slots, str)
        and not raw_parallel_slots.strip()
    ):
        parallel_slots = 1
    else:
        try:
            parallel_slots = int(raw_parallel_slots)
        except (ValueError, TypeError):
            parallel_slots = 1
    if parallel_slots < 1:
        parallel_slots = 1
    if parallel_slots > 16:
        parallel_slots = 16

    # Determine cache types (default f16)
    cache_type_k = (launch_payload.get("cache_type_k") or slot.get("cache_type_k", "f16") or "f16").strip().lower()
    cache_type_v = (launch_payload.get("cache_type_v") or slot.get("cache_type_v", "f16") or "f16").strip().lower()
    _ALLOWED = ["f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"]
    if cache_type_k not in _ALLOWED:
        cache_type_k = "f16"
    if cache_type_v not in _ALLOWED:
        cache_type_v = "f16"

    # Determine batch_size (default 8192)
    _BATCH_DEFAULT = 8192
    _UBATCH_DEFAULT = 2048
    if "batch_size" in launch_payload:
        raw_bs = launch_payload["batch_size"]
        if raw_bs is None or str(raw_bs).strip() == "":
            batch_size = _BATCH_DEFAULT
        else:
            try:
                batch_size = int(raw_bs)
                if batch_size <= 0:
                    batch_size = _BATCH_DEFAULT
            except (ValueError, TypeError):
                batch_size = _BATCH_DEFAULT
    elif "batch_size" in slot:
        raw_bs = slot["batch_size"]
        if isinstance(raw_bs, int) and raw_bs > 0:
            batch_size = raw_bs
        else:
            batch_size = _BATCH_DEFAULT
    else:
        batch_size = _BATCH_DEFAULT

    # Determine ubatch_size (default 2048)
    if "ubatch_size" in launch_payload:
        raw_ub = launch_payload["ubatch_size"]
        if raw_ub is None or str(raw_ub).strip() == "":
            ubatch_size = _UBATCH_DEFAULT
        else:
            try:
                ubatch_size = int(raw_ub)
                if ubatch_size <= 0:
                    ubatch_size = _UBATCH_DEFAULT
            except (ValueError, TypeError):
                ubatch_size = _UBATCH_DEFAULT
    elif "ubatch_size" in slot:
        raw_ub = slot["ubatch_size"]
        if isinstance(raw_ub, int) and raw_ub > 0:
            ubatch_size = raw_ub
        else:
            ubatch_size = _UBATCH_DEFAULT
    else:
        ubatch_size = _UBATCH_DEFAULT

    # Enforce ubatch_size <= batch_size
    if ubatch_size > batch_size:
        ubatch_size = batch_size

    # Compute tensor split value from resolved GPUs
    if tensor_split_cleared:
        split_val = None
    elif str(tensor_split).lower() == "auto":
        split_val = compute_auto_tensor_split(resolved_gpu_devices)
    else:
        split_val = str(tensor_split)

    # Build GPU args from resolved devices
    if not resolved_gpu_devices:
        return False, "", "no_usable_gpus"

    gpu_args = [
        "--gpus",
        f'"device={resolved_gpu_devices}"',
    ]

    # ── Extra args conflict handling ───────────────────────────────────────
    # When a first-class field is set, filter out conflicting flags from
    # extra_args to prevent duplicate args.
    filtered_extra_args: list[str] = []
    extra_args_warning: str | None = None

    # Flags that are actively set via first-class fields
    has_tensor_split = (not tensor_split_cleared
                        and str(tensor_split).lower() != "auto")
    has_batch = True  # batch_size always has a default value
    has_ubatch = True  # ubatch_size always has a default value

    skip_next_extra_arg = False
    for arg in extra_args:
        arg_str = str(arg).strip()
        if skip_next_extra_arg:
            skip_next_extra_arg = False
            continue

        # tensor_split conflicts
        if has_tensor_split:
            if arg_str in ("--tensor-split", "-ts"):
                extra_args_warning = (
                    f"Ignoring {arg_str} from extra_args because "
                    f"tensor_split field is set"
                )
                skip_next_extra_arg = True
                continue
            if arg_str.startswith("--tensor-split="):
                extra_args_warning = (
                    "Ignoring --tensor-split from extra_args because "
                    "tensor_split field is set"
                )
                continue

        # batch_size conflicts
        if has_batch:
            if arg_str in ("--batch-size", "-b"):
                extra_args_warning = (
                    f"Ignoring {arg_str} from extra_args because "
                    f"batch_size field is set"
                )
                skip_next_extra_arg = True
                continue
            if arg_str.startswith("--batch-size="):
                extra_args_warning = (
                    "Ignoring --batch-size from extra_args because "
                    "batch_size field is set"
                )
                continue

        # ubatch_size conflicts
        if has_ubatch:
            if arg_str in ("--ubatch-size", "-ub"):
                extra_args_warning = (
                    f"Ignoring {arg_str} from extra_args because "
                    f"ubatch_size field is set"
                )
                skip_next_extra_arg = True
                continue
            if arg_str.startswith("--ubatch-size="):
                extra_args_warning = (
                    "Ignoring --ubatch-size from extra_args because "
                    "ubatch_size field is set"
                )
                continue

        filtered_extra_args.append(arg)

    # Build command
    cmd = [
        _QONDUIT_LLAMA_SERVER_BIN,
        "--model", model_path,
        "--n-gpu-layers", "-1",
        "--ctx-size", str(context_size),
        "--host", "0.0.0.0",
        "--port", str(slot.get("internal_port", 8080)),
    ]

    # Parallel slots flag
    if parallel_slots > 1:
        probe_cache = probe_llama_server_flags()
        parallel_flag = probe_cache.get("parallel_flag", "--parallel")
        if parallel_flag not in ("--parallel", "-np"):
            parallel_flag = "--parallel"
        cmd.extend([parallel_flag, str(parallel_slots)])

    if split_val is not None:
        cmd.extend(["--tensor-split", split_val])

    if embeddings:
        cmd.append("--embeddings")

    # Cache type flags
    if cache_type_k:
        cmd.extend(["--cache-type-k", cache_type_k])
    if cache_type_v:
        cmd.extend(["--cache-type-v", cache_type_v])

    # Batch size flags
    cmd.extend(["--batch-size", str(batch_size)])
    cmd.extend(["--ubatch-size", str(ubatch_size)])

    cmd.extend(filtered_extra_args)

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
            "parallel_slots": parallel_slots,
            "cache_type_k": cache_type_k,
            "cache_type_v": cache_type_v,
            "batch_size": batch_size,
            "ubatch_size": ubatch_size,
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
    """Check if a host port is not in use by any known slot (except the given one).

    Also checks OS-level TCP listeners so we don't collide with real processes.
    """
    from qonduit_slots import load_slots

    # Check slot registry
    slots = load_slots()
    if any(
        s.get("host_port") == port and s.get("slot_id") != exclude_slot_id
        for s in slots
    ):
        return False

    # Check OS-level TCP listeners
    try:
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split()
                for part in parts:
                    if part.endswith(f":{port}") or part.split(":")[-1] == str(port):
                        return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return True


def docker_name_is_available(name: str, exclude_slot_id: str | None = None) -> bool:
    """Check if a Docker container name is not in use by any known slot (except the given one).

    Also checks Docker directly so we don't collide with real containers.
    """
    from qonduit_slots import load_slots

    # Check slot registry
    slots = load_slots()
    if any(
        s.get("container_name") == name and s.get("slot_id") != exclude_slot_id
        for s in slots
    ):
        return False

    # Check Docker directly
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"name={name}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return True


def find_port_conflict(
    port: int,
    exclude_slot_id: str | None = None,
) -> dict[str, Any] | None:
    """Find which slot owns a conflicting port. Returns conflict info or None.

    If *exclude_slot_id* is given, that slot is ignored — useful when the
    caller is preflighting its own slot and wants to know about *other*
    slots only.
    """
    from qonduit_slots import load_slots

    slots = load_slots()
    for s in slots:
        if s.get("host_port") == port and s.get("slot_id") != exclude_slot_id:
            return {
                "slot_id": s.get("slot_id"),
                "container_name": s.get("container_name"),
                "host_port": port,
            }
    return None


def find_container_name_conflict(
    name: str,
    exclude_slot_id: str | None = None,
) -> dict[str, Any] | None:
    """Find which slot owns a conflicting container name. Returns conflict info or None.

    If *exclude_slot_id* is given, that slot is ignored — useful when the
    caller is preflighting its own slot and wants to know about *other*
    slots only.
    """
    from qonduit_slots import load_slots

    slots = load_slots()
    for s in slots:
        if s.get("container_name") == name and s.get("slot_id") != exclude_slot_id:
            return {
                "slot_id": s.get("slot_id"),
                "container_name": name,
                "host_port": s.get("host_port"),
            }
    return None


def collect_gpu_summary() -> dict[str, Any]:
    """Run nvidia-smi and return GPU summary with usable/excluded info."""
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
        all_indices: list[int] = []

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
            all_indices.append(idx)
            total_mib += mem_total
            used_mib += mem_used
            free_mib += mem_free

        usable_indices = [i for i in all_indices if not any(
            e["index"] == i for e in detect_excluded_gpus()
        )]
        usable_gpu_devices = ",".join(str(i) for i in usable_indices)

        return {
            "ok": True,
            "gpus": gpus,
            "memory_total_mib": total_mib,
            "memory_used_mib": used_mib,
            "memory_free_mib": free_mib,
            "memory_total_human": _format_bytes_human(total_mib * 1024 * 1024),
            "memory_used_human": _format_bytes_human(used_mib * 1024 * 1024),
            "memory_free_human": _format_bytes_human(free_mib * 1024 * 1024),
            "usable_gpu_indices": usable_indices,
            "usable_gpu_devices": usable_gpu_devices,
            "excluded_gpus": detect_excluded_gpus(),
            "default_gpu_devices": get_default_gpu_devices(),
            "gpu_min_total_mib": _QONDUIT_GPU_MIN_TOTAL_MIB,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "gpu_query_failed",
            "detail": str(e),
        }


def compute_auto_tensor_split(gpu_devices: Any) -> str:
    """Compute tensor_split from GPU device list.

    If gpu_devices is 'all', resolves to usable GPUs and returns '1,1,...'.
    If gpu_devices is '0,1,2', returns '1,1,1' (equal split).
    """
    gpu_str = str(gpu_devices).strip()
    if gpu_str.lower() in ("all", "all_raw"):
        resolved = resolve_gpu_devices(gpu_str)
        devices = [d.strip() for d in resolved.split(",")]
        return ",".join(["1"] * len(devices))
    devices = [d.strip() for d in gpu_str.split(",")]
    return ",".join(["1"] * len(devices))


# ── Suggested tensor split generation ────────────────────────────────────────
#
# Normalization rule:
#   The free-VRAM-weighted normalized split divides each GPU's free memory
#   (in MiB) by 102.4 to produce a readable integer that preserves the
#   relative proportions of the raw free-VRAM values.  The divisor 102.4
#   was chosen so that a GPU with ~14 GiB free yields ~137 (close to the
#   llama.cpp convention of values in the 1-100 range per GPU).  This
#   avoids zero values because every included GPU has at least the
#   QONDUIT_GPU_MIN_TOTAL_MIB threshold of memory, and we only include
#   GPUs that have some free memory.
#
# Example with the target machine's GPUs (effective inference GPUs):
#   GPU 0 free: 14128 MiB  →  14128 / 102.4  ≈ 138
#   GPU 2 free:  5614 MiB  →   5614 / 102.4  ≈  55
#   GPU 3 free:  8174 MiB  →   8174 / 102.4  ≈  80
#   GPU 4 free:  9280 MiB  →   9280 / 102.4  ≈  91
#   GPU 5 free:  8174 MiB  →   8174 / 102.4  ≈  80
#   GPU 6 free:  8112 MiB  →   8112 / 102.4  ≈  79
#   GPU 7 free:  8138 MiB  →   8138 / 102.4  ≈  79


def _gpu_free_mem_map(gpu_summary: dict[str, Any], effective_gpu_indices: list[int] | None) -> dict[int, int]:
    """Return {gpu_index: free_mib} for GPUs in effective_gpu_indices.

    If effective_gpu_indices is None, returns all usable GPUs from the summary.
    Only includes GPUs that have free memory > 0.
    """
    free_map: dict[int, int] = {}
    if not gpu_summary.get("ok") or not gpu_summary.get("gpus"):
        return free_map

    if effective_gpu_indices is None:
        # Use all usable GPUs from the summary
        usable = gpu_summary.get("usable_gpu_indices")
        if usable is not None:
            effective_gpu_indices = [int(i) for i in str(usable).split(",") if i.strip()]
        else:
            # Fallback: all GPUs in the summary
            effective_gpu_indices = [g["index"] for g in gpu_summary["gpus"]]

    for gpu in gpu_summary["gpus"]:
        idx = gpu["index"]
        if idx in effective_gpu_indices:
            free = gpu.get("memory_free_mib", 0)
            if free > 0:
                free_map[idx] = free

    return free_map


def compute_suggested_tensor_splits(
    gpu_summary: dict[str, Any],
    effective_gpu_count: int,
) -> dict[str, str | None]:
    """Compute suggested tensor splits for the frontend.

    Returns a dict with:
      - "even": equal split, e.g. "1,1,1,1,1,1,1"
      - "free_vram_weighted_raw": raw free memory per GPU, e.g. "14128,5614,8174,9280,8174,8112,8138"
      - "free_vram_weighted_normalized": normalized by 102.4, e.g. "138,55,80,91,80,79,79"
      - "warning" (optional): if GPU memory data unavailable

    If GPU free memory data is unavailable, returns None for weighted suggestions
    with a warning.  Never fakes weighted split as even split when data is missing.
    """
    suggestions: dict[str, str | None] = {}

    # Always return even split
    suggestions["even"] = ",".join(["1"] * max(effective_gpu_count, 0))

    # Build free memory map for effective GPUs
    effective_gpu_indices: list[int] | None = None
    if effective_gpu_count > 0 and gpu_summary.get("ok") and gpu_summary.get("usable_gpu_indices"):
        usable = gpu_summary["usable_gpu_indices"]
        # Handle both list (e.g. [0,2,3]) and string (e.g. "0,2,3") formats
        if isinstance(usable, list):
            effective_gpu_indices = [int(i) for i in usable if str(i).strip()]
        else:
            effective_gpu_indices = [int(i) for i in str(usable).split(",") if str(i).strip()]
        # If we have fewer effective GPUs than usable, take the first N
        if len(effective_gpu_indices) > effective_gpu_count:
            effective_gpu_indices = effective_gpu_indices[:effective_gpu_count]

    free_map = _gpu_free_mem_map(gpu_summary, effective_gpu_indices)

    if not free_map:
        # No GPU free memory data available — return None for weighted, with warning
        suggestions["free_vram_weighted_raw"] = None
        suggestions["free_vram_weighted_normalized"] = None
        suggestions["warning"] = "GPU free memory data unavailable; weighted suggestions not computed."
        return suggestions

    # Sort by GPU index to preserve CUDA_VISIBLE_DEVICES ordering
    sorted_indices = sorted(free_map.keys())
    raw_values = [str(free_map[idx]) for idx in sorted_indices]
    suggestions["free_vram_weighted_raw"] = ",".join(raw_values)

    # Normalized: divide by 102.4, clamp to minimum 1 to avoid zeros
    normalized_values = []
    for idx in sorted_indices:
        val = free_map[idx] / 102.4
        normalized_values.append(str(max(1, round(val))))
    suggestions["free_vram_weighted_normalized"] = ",".join(normalized_values)

    return suggestions


# ── GPU detection helpers ────────────────────────────────────────────────────


def _run_nvidia_smi() -> Optional[subprocess.CompletedProcess]:
    """Run nvidia-smi and return the completed process, or None on failure."""
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
        return result if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def detect_usable_gpus() -> str:
    """Detect GPUs usable for inference.

    Excludes GPUs below the memory threshold or matching the exclude regex.
    Returns comma-separated GPU indices, e.g. '0,2,3,4,5,6,7'.

    Results are cached after the first successful call.
    """
    global _usable_gpu_cache

    if "usable_indices" in _usable_gpu_cache:
        return _usable_gpu_cache["usable_indices"]

    default_devices = _QONDUIT_DEFAULT_GPU_DEVICES.strip()
    if default_devices.lower() != "auto":
        # Explicit override — validate and return as-is
        if re.match(r"^[0-9]+(,[0-9]+)*$", default_devices):
            _usable_gpu_cache["usable_indices"] = default_devices
            return default_devices
        # If invalid format, fall through to auto-detection
        _usable_gpu_cache["usable_indices"] = ""
        return ""

    result = _run_nvidia_smi()
    if result is None:
        _usable_gpu_cache["usable_indices"] = ""
        return ""

    try:
        exclude_re = re.compile(_QONDUIT_GPU_EXCLUDE_NAME_REGEX)
        usable: list[int] = []
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            idx = int(parts[0])
            name = parts[1]
            mem_total = int(parts[2])

            if mem_total < _QONDUIT_GPU_MIN_TOTAL_MIB:
                continue
            if exclude_re.search(name):
                continue

            usable.append(idx)

        gpu_str = ",".join(str(i) for i in usable)
        _usable_gpu_cache["usable_indices"] = gpu_str
        return gpu_str
    except (ValueError, OSError):
        _usable_gpu_cache["usable_indices"] = ""
        return ""


def detect_excluded_gpus() -> list[dict[str, Any]]:
    """Return list of GPUs excluded from inference.

    Each entry has: index, name, reason
    """
    result = _run_nvidia_smi()
    if result is None:
        return []

    try:
        exclude_re = re.compile(_QONDUIT_GPU_EXCLUDE_NAME_REGEX)
        excluded: list[dict[str, Any]] = []
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            idx = int(parts[0])
            name = parts[1]
            mem_total = int(parts[2])

            reasons: list[str] = []
            if mem_total < _QONDUIT_GPU_MIN_TOTAL_MIB:
                reasons.append(
                    f"memory_total_mib ({mem_total}) below threshold {_QONDUIT_GPU_MIN_TOTAL_MIB}"
                )
            if exclude_re.search(name):
                reasons.append(f"name matches exclude regex '{_QONDUIT_GPU_EXCLUDE_NAME_REGEX}'")

            for reason in reasons:
                excluded.append({
                    "index": idx,
                    "name": name,
                    "reason": reason,
                })
        return excluded
    except (ValueError, OSError):
        return []


def get_default_gpu_devices() -> str:
    """Return the default GPU device string for new slots.

    If QONDUIT_DEFAULT_GPU_DEVICES is set to an explicit list, returns it.
    Otherwise, returns the auto-detected usable GPU list.
    """
    default_devices = _QONDUIT_DEFAULT_GPU_DEVICES.strip()
    if default_devices.lower() != "auto":
        return default_devices
    return detect_usable_gpus()


def resolve_gpu_devices(gpu_devices: Any) -> str:
    """Resolve a gpu_devices value to an actual GPU list.

    - "all" → auto-detected usable GPUs
    - "all_raw" → all GPUs from nvidia-smi (raw)
    - "0,2,3" → validated explicit list
    """
    gpu_str = str(gpu_devices).strip()

    if gpu_str.lower() == "all":
        return detect_usable_gpus()

    if gpu_str.lower() == "all_raw":
        result = _run_nvidia_smi()
        if result is None:
            return ""
        try:
            indices = []
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 1:
                    indices.append(parts[0])
            return ",".join(indices)
        except (ValueError, OSError):
            return ""

    # Explicit GPU list — validate
    if re.match(r"^[0-9]+(,[0-9]+)*$", gpu_str):
        return gpu_str

    # Invalid — return as-is (caller should handle error)
    return gpu_str


def validate_gpu_devices(gpu_devices: Any, all_available: Optional[list[int]] = None) -> Optional[str]:
    """Validate an explicit GPU device list.

    Returns an error string if invalid, None if valid.
    """
    gpu_str = str(gpu_devices).strip()
    if gpu_str.lower() in ("all", "all_raw"):
        return None

    if not re.match(r"^[0-9]+(,[0-9]+)*$", gpu_str):
        return 'gpu_devices must be "all", "all_raw", or comma-separated GPU IDs like "0,1,2"'

    try:
        requested = [int(d.strip()) for d in gpu_str.split(",")]
    except ValueError:
        return f"Invalid GPU ID in: {gpu_str}"

    if all_available is not None:
        available_set = set(all_available)
        for gid in requested:
            if gid not in available_set:
                return f"GPU {gid} is not available (available: {available_set})"

    return None


# ── llama.cpp flag probing ───────────────────────────────────────────────────

# Cached flag support detection (set by probe_llama_server_flags)
_llama_server_flag_cache: dict[str, Any] | None = None


def probe_llama_server_flags() -> dict[str, Any]:
    """Probe the installed llama-server for supported flags.

    Returns a dict with:
      - "parallel_flag": detected parallel flag or "unknown"
      - "cache_type_k_flag": detected cache-type-k flag or "unknown"
      - "cache_type_v_flag": detected cache-type-v flag or "unknown"
      - "probed": True/False

    If probing fails, returns defaults without raising.
    """
    global _llama_server_flag_cache
    # Re-probe on each call so tests and upgraded llama.cpp binaries see the
    # current flag set immediately. The cache is retained only as a last-known
    # value for callers that explicitly inspect/reset it.

    result: dict[str, Any] = {
        "ok": False,
        "parallel_flag": "--parallel",
        "supports_parallel": False,
        "supports_np": False,
        "error": None,
        # Backward-compatible fields used by existing tests/callers.
        "cache_type_k_flag": "--cache-type-k",
        "cache_type_v_flag": "--cache-type-v",
        "probed": False,
    }

    try:
        # Try to run llama-server --help inside a dummy container or from PATH
        # First try docker image (common way llama-server is deployed)
        images_to_try = [
            # Common llama.cpp container image names
            "ghcr.io/ggerganov/llama.cpp",
            # Try any running llama-server container to inspect its help
        ]

        help_text = ""

        # Try to find llama-server in docker images
        for img in images_to_try:
            try:
                inspect = subprocess.run(
                    ["docker", "image", "inspect", img,
                     "--format", "{{.Config.Cmd}}"],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                if inspect.returncode == 0 and inspect.stdout.strip():
                    result_text = subprocess.run(
                        ["docker", "run", "--rm", img, "--help"],
                        capture_output=True, text=True, check=False, timeout=15,
                    )
                    if result_text.returncode == 0:
                        help_text = result_text.stdout
                        break
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue

        # Fallback: try running llama-server directly from PATH
        if not help_text:
            try:
                result_text = subprocess.run(
                    ["llama-server", "--help"],
                    capture_output=True, text=True, check=False, timeout=15,
                )
                if result_text.returncode == 0:
                    help_text = result_text.stdout
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        # Fallback: try llama.cpp server from common paths
        if not help_text:
            common_paths = [
                "/usr/local/bin/llama-server",
                "/usr/bin/llama-server",
                "./llama-server",
            ]
            for path in common_paths:
                try:
                    result_text = subprocess.run(
                        [path, "--help"],
                        capture_output=True, text=True, check=False, timeout=15,
                    )
                    if result_text.returncode == 0:
                        help_text = result_text.stdout
                        break
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    continue

        if isinstance(help_text, bytes):
            help_text = help_text.decode("utf-8", errors="ignore")

        if help_text:
            result["ok"] = True
            result["probed"] = True
            result["supports_parallel"] = "--parallel" in help_text
            result["supports_np"] = (
                "-np" in help_text or "--num-processor" in help_text
            )
            if result["supports_parallel"]:
                result["parallel_flag"] = "--parallel"
            elif result["supports_np"]:
                result["parallel_flag"] = "-np"
            # Check for cache type flags
            if "--cache-type-k" in help_text:
                result["cache_type_k_flag"] = "--cache-type-k"
            if "--cache-type-v" in help_text:
                result["cache_type_v_flag"] = "--cache-type-v"
        else:
            result["probed"] = False
            result["error"] = "llama-server help text unavailable"
            result["warning"] = "llama-server help text unavailable; using default flag assumptions"

    except Exception as e:
        result["error"] = str(e)
        result["warning"] = f"flag probing failed: {e}"

    _llama_server_flag_cache = result
    return result


def reset_flag_probe_cache() -> None:
    """Clear the flag probe cache (useful for testing)."""
    global _llama_server_flag_cache
    _llama_server_flag_cache = None


# ── KV cache estimate ────────────────────────────────────────────────────────

# Byte multipliers per element for each cache type (for K or V tensor)
_CACHE_TYPE_BYTES: dict[str, float] = {
    "f32": 4.0,
    "f16": 2.0,
    "bf16": 2.0,
    "q8_0": 1.0,
    "q5_0": 0.625,
    "q5_1": 0.625,
    "q4_0": 0.5,
    "q4_1": 0.5,
    "iq4_nl": 0.5,
}

# Baseline: f16 for both K and V = 2.0 + 2.0 = 4.0 bytes per element
_BASELINE_CACHE_BYTES = 4.0


def estimate_kv_cache_mib(
    context_size: int,
    parallel_slots: int,
    cache_type_k: str,
    cache_type_v: str,
    n_layers: int | None = None,
    n_embd: int | None = None,
    head_dim: int | None = None,
    num_key_value_heads: int | None = None,
) -> dict[str, Any]:
    """Estimate KV cache memory usage in MiB.

    Uses model metadata (n_layers, n_embd, etc.) if provided for exact
    calculation. Falls back to a heuristic based on context_size if
    metadata is unavailable.

    Returns dict with:
      - ok: True
      - estimate_confidence: "exact" or "heuristic"
      - context_size, parallel_slots, effective_context_per_parallel_slot
      - cache_type_k, cache_type_v, baseline_cache_type_k, baseline_cache_type_v
      - estimated_kv_cache_mib: current estimate
      - estimated_kv_cache_f16_mib: baseline f16/f16 estimate
      - estimated_savings_vs_f16_mib: absolute savings
      - estimated_savings_vs_f16_percent: percentage savings
      - warning (optional): if heuristic
    """
    effective_ctx = max(1, context_size // max(1, parallel_slots))

    # Determine byte multipliers
    bytes_k = _CACHE_TYPE_BYTES.get(cache_type_k, _CACHE_TYPE_BYTES["f16"])
    bytes_v = _CACHE_TYPE_BYTES.get(cache_type_v, _CACHE_TYPE_BYTES["f16"])
    baseline_bytes_k = _CACHE_TYPE_BYTES.get("f16", 2.0)
    baseline_bytes_v = _CACHE_TYPE_BYTES.get("f16", 2.0)

    current_bytes_per_element = bytes_k + bytes_v
    baseline_bytes_per_element = baseline_bytes_k + baseline_bytes_v

    if n_layers is not None and n_embd is not None:
        # Exact calculation using model metadata
        # KV cache shape per layer: [2, context, n_kv_heads, head_dim]
        # Total KV cache elements = 2 * n_layers * effective_ctx * (n_kv_heads * head_dim)
        # But n_kv_heads * head_dim = n_embd / n_heads * n_kv_heads ...
        # Simplified: KV cache per layer ≈ 2 * effective_ctx * n_embd (for full attention)
        # For GQA/MQA, KV is smaller, but we use a conservative estimate
        n_kv_heads = num_key_value_heads
        if n_kv_heads is None:
            # Assume MHA (n_kv_heads == n_heads) for conservative estimate
            n_heads_est = max(1, n_embd // (head_dim or 128))
            n_kv_heads = n_heads_est

        # KV cache elements = n_layers * effective_ctx * n_kv_heads * head_dim
        # For MHA: n_kv_heads * head_dim = n_embd
        # KV cache bytes = elements * (bytes_k + bytes_v)
        kv_elements = n_layers * effective_ctx * n_embd
        kv_bytes = kv_elements * current_bytes_per_element
        confidence = "exact"
    else:
        # Heuristic: estimate n_layers from context_size
        # Conservative: assume ~32 layers for a mid-size model
        # KV cache ≈ elements * (bytes_k + bytes_v)
        # We estimate n_layers * n_embd ≈ 32 * 4096 (conservative heuristic)
        est_n_layers = 32
        est_n_embd = 4096
        kv_elements = est_n_layers * effective_ctx * est_n_embd
        kv_bytes = kv_elements * current_bytes_per_element
        confidence = "heuristic"

    # Baseline: f16/f16 = 4.0 bytes per element
    if n_layers is not None and n_embd is not None:
        baseline_elements = n_layers * effective_ctx * n_embd
    else:
        baseline_elements = 32 * effective_ctx * 4096
    baseline_bytes = baseline_elements * _BASELINE_CACHE_BYTES

    # Convert bytes to MiB
    exact_mib = kv_bytes / (1024 * 1024)
    baseline_mib = baseline_bytes / (1024 * 1024)

    savings_mib = max(0, baseline_mib - exact_mib)
    savings_percent = (
        100 * (1 - exact_mib / baseline_mib) if baseline_mib > 0 else 0
    )

    result: dict[str, Any] = {
        "ok": True,
        "estimate_confidence": confidence,
        "context_size": context_size,
        "parallel_slots": parallel_slots,
        "effective_context_per_parallel_slot": effective_ctx,
        "cache_type_k": cache_type_k,
        "cache_type_v": cache_type_v,
        "baseline_cache_type_k": "f16",
        "baseline_cache_type_v": "f16",
        "estimated_mib": round(exact_mib, 2),
        "kv_cache_mib": round(exact_mib, 2),
        "total_mib": round(exact_mib, 2),
        "estimated_kv_cache_mib": round(exact_mib, 2),
        "estimated_kv_cache_f16_mib": round(baseline_mib, 2),
        "estimated_savings_vs_f16_mib": round(savings_mib, 2),
        "estimated_savings_vs_f16_percent": round(savings_percent, 1),
    }

    if confidence == "heuristic":
        result["warning"] = (
            "KV cache estimate is heuristic (model metadata unavailable). "
            "Actual usage may differ."
        )

    return result
