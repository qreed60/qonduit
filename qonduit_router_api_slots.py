"""
Qonduit Router — multi-slot API endpoints (Phases 3-7).

Registers all new slot-aware endpoints on the existing Flask app.
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from flask import Flask, jsonify, request, Response

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
    check_port_available,
    check_container_name_available,
)
from qonduit_docker_helpers import (
    container_exists,
    container_running,
    container_status,
    stop_slot_container,
    remove_slot_container,
    launch_slot_container,
    check_slot_ready,
    fetch_slot_models,
    stream_slot_logs,
    port_is_available,
    docker_name_is_available,
    find_port_conflict,
    find_container_name_conflict,
    collect_gpu_summary,
    compute_auto_tensor_split,
    compute_suggested_tensor_splits,
    resolve_gpu_devices,
    docker_available,
    probe_llama_server_flags,
    estimate_kv_cache_mib,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _json_error(
    code: str,
    detail: str,
    status: int = 400,
) -> tuple[dict[str, Any], int]:
    """Return a consistent JSON error response."""
    return jsonify({"ok": False, "error": code, "detail": detail}), status


def _env_bool(name: str, default: bool = False) -> bool:
    """Read an environment variable as a boolean."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _is_loopback_or_localhost(client_ip: str | None) -> bool:
    """Return True if *client_ip* is a loopback or localhost address."""
    if not client_ip:
        return False
    if client_ip in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(client_ip).is_loopback
    except ValueError:
        return False


def _is_private_lan(client_ip: str | None) -> bool:
    """Return True if *client_ip* is an RFC-1918 private or link-local address."""
    if not client_ip:
        return False
    try:
        ip = ipaddress.ip_address(client_ip)
        return ip.is_private or ip.is_link_local
    except ValueError:
        return False


def _router_access_allowed() -> bool:
    """Return True when the current request should be allowed router access."""
    client_ip = request.remote_addr
    if _is_loopback_or_localhost(client_ip):
        return True
    if _env_bool("QONDUIT_ROUTER_ALLOW_LAN", True) and _is_private_lan(client_ip):
        return True
    return False


def _require_router_access() -> Optional[tuple[dict[str, Any], int]]:
    """Return *None* if access is allowed, else a JSON error response.

    OPTIONS requests are never blocked (CORS/PNA preflight support).
    """
    if request.method == "OPTIONS":
        return None
    if _router_access_allowed():
        return None
    return jsonify({
        "ok": False,
        "error": "router_access_denied",
        "client_ip": request.remote_addr,
        "allow_lan": _env_bool("QONDUIT_ROUTER_ALLOW_LAN", True),
    }), 403


def _get_primary_slot() -> dict[str, Any]:
    """Return the primary slot (or default dict if not found)."""
    slots = load_slots()
    for s in slots:
        if s.get("slot_id") == "primary":
            return slot_to_live_status(s)
    return {
        "slot_id": "primary",
        "display_name": "Primary",
        "running": False,
        "exists": False,
        "ready": False,
        "model": None,
        "context_size": 65536,
        "endpoint_base": f"http://{_QONDUIT_DEFAULT_HOST}:8080",
        "openai_base": f"http://{_QONDUIT_DEFAULT_HOST}:8080/v1",
        "host_port": 8080,
        "internal_port": 8080,
        "gpu_devices": "all",
        "tensor_split": "auto",
        "embeddings_enabled": True,
        "extra_args": [],
        "container_name": "llama_server",
    }


def _slot_from_path(slot_id: str) -> dict[str, Any]:
    """Get a slot by ID from URL path, with validation. Returns (slot, error_resp) tuple."""
    slot_id = slot_id.strip()
    err = None
    if not slot_id:
        return {}, _json_error("invalid_slot_id", "slot_id is required", 400)
    if not re.match(r"^[a-z0-9][a-z0-9_-]*$", slot_id):
        return {}, _json_error(
            "invalid_slot_id",
            "slot_id must contain only lowercase letters, numbers, dash, and underscore",
            400,
        )
    slot_data = get_slot(slot_id)
    if not slot_data:
        return {}, _json_error("slot_not_found", f"slot '{slot_id}' not found", 404)
    return slot_to_live_status(slot_data), None


# ── Phase 4: Multi-slot API endpoints ───────────────────────────────────────

def register_slot_routes(app: Flask) -> None:
    """Register all slot-aware endpoints on the given Flask app."""

    # --- GET /api/v1/qonduit-router/slots ---
    @app.get("/api/v1/qonduit-router/slots")
    def slots_list():
        denied = _require_router_access()
        if denied:
            return denied
        slots = load_slots()
        result = []
        for s in slots:
            result.append(slot_to_live_status(s))
        return jsonify({"ok": True, "slots": result})

    # --- POST /api/v1/qonduit-router/slots ---
    @app.post("/api/v1/qonduit-router/slots")
    def slots_create():
        denied = _require_router_access()
        if denied:
            return denied

        payload = request.get_json(silent=True) or {}
        if not payload:
            return _json_error("invalid_body", "request body is required", 400)

        new_slot, err = create_slot(payload)
        if err:
            if err == "duplicate_slot":
                return _json_error("duplicate_slot", "slot_id already exists", 409)
            if err == "duplicate_container_name":
                return _json_error("duplicate_container_name", "container_name already exists", 409)
            if err == "duplicate_port":
                return _json_error("duplicate_port", "host_port already exists", 409)
            return _json_error("validation_failed", err, 400)

        return jsonify({"ok": True, "slot": slot_to_live_status(new_slot)}), 201

    # --- GET /api/v1/qonduit-router/slots/<slot_id> ---
    @app.get("/api/v1/qonduit-router/slots/<slot_id>")
    def slot_get(slot_id: str):
        denied = _require_router_access()
        if denied:
            return denied

        slot, err = _slot_from_path(slot_id)
        if err:
            return err
        return jsonify({"ok": True, "slot": slot})

    # --- PATCH /api/v1/qonduit-router/slots/<slot_id> ---
    @app.patch("/api/v1/qonduit-router/slots/<slot_id>")
    def slot_update(slot_id: str):
        denied = _require_router_access()
        if denied:
            return denied

        payload = request.get_json(silent=True) or {}
        if not payload:
            return _json_error("invalid_body", "request body is required", 400)

        force = str(payload.get("force") or request.args.get("force", "false")).lower() == "true"

        slot, err = _slot_from_path(slot_id)
        if err:
            return err

        # Cannot change slot_id
        if "slot_id" in payload:
            return _json_error("invalid_field", "slot_id cannot be changed", 400)

        # Remove force from payload before updating
        payload_clean = {k: v for k, v in payload.items() if k != "force"}

        updated, err = update_slot(slot_id, payload_clean, force=force)
        if err:
            if err == "slot_running_requires_force":
                return _json_error(
                    "slot_running_requires_force",
                    "Cannot change immutable fields while slot is running. Use force=true.",
                    409,
                )
            return _json_error("update_failed", err, 400)

        return jsonify({"ok": True, "slot": updated})

    # --- DELETE /api/v1/qonduit-router/slots/<slot_id> ---
    @app.delete("/api/v1/qonduit-router/slots/<slot_id>")
    def slot_delete(slot_id: str):
        denied = _require_router_access()
        if denied:
            return denied

        force = str(request.args.get("force", "false")).lower() == "true"
        remove_container = str(
            request.args.get("remove_container", "false"),
        ).lower() == "true"

        slot_data = get_slot(slot_id)
        if not slot_data:
            return _json_error("slot_not_found", f"slot '{slot_id}' not found", 404)

        # Validate slot_id format
        if not re.match(r"^[a-z0-9][a-z0-9_-]*$", slot_id):
            return _json_error(
                "invalid_slot_id",
                "slot_id must contain only lowercase letters, numbers, dash, and underscore",
                400,
            )

        ok, err = delete_slot(slot_id, remove_container=remove_container, force=force)
        if not ok:
            if err == "primary_slot_delete_requires_force":
                return _json_error(
                    "primary_slot_delete_requires_force",
                    "Cannot delete primary slot without force=true",
                    409,
                )
            if err == "slot_running_requires_force":
                return _json_error(
                    "slot_running_requires_force",
                    "Cannot delete running slot without force=true",
                    409,
                )
            return _json_error("delete_failed", err, 400)

        return jsonify({"ok": True, "deleted": slot_id})

    # --- POST /api/v1/qonduit-router/slots/<slot_id>/launch ---
    @app.post("/api/v1/qonduit-router/slots/<slot_id>/launch")
    def slot_launch(slot_id: str):
        denied = _require_router_access()
        if denied:
            return denied

        slot, err = _slot_from_path(slot_id)
        if err:
            return err

        payload = request.get_json(silent=True) or {}
        model = (payload.get("model") or slot.get("model") or "").strip()
        if not model:
            return _json_error("model_required", "model is required", 400)

        # Check model exists
        model_path = f"/mnt/models/llm/{model}"
        if not os.path.exists(model_path):
            return _json_error("model_not_found", f"model file not found: {model}", 404)

        # Check port availability (exclude this slot from collision check)
        host_port = slot.get("host_port", 8080)
        if not port_is_available(host_port, exclude_slot_id=slot_id):
            return _json_error("port_in_use", f"port {host_port} is already in use", 409)

        # Check container name availability (exclude this slot)
        container_name = slot.get("container_name", "")
        if not docker_name_is_available(container_name, exclude_slot_id=slot_id):
            return _json_error("duplicate_container_name", "container_name already in use", 409)

        # Launch
        ok, message, error = launch_slot_container(slot, payload)
        if not ok:
            if error == "model_required":
                return _json_error("model_required", "model is required", 400)
            if error == "model_not_found":
                return _json_error("model_not_found", f"model file not found: {model}", 404)
            return _json_error("launch_failed", error or "docker failed", 500)

        # Refresh slot status
        refreshed = get_slot(slot_id)
        if refreshed:
            refreshed = slot_to_live_status(refreshed)
        else:
            refreshed = slot

        return jsonify({
            "ok": True,
            "message": message,
            "slot": refreshed,
            "endpoint_base": refreshed.get("endpoint_base", ""),
            "openai_base": refreshed.get("openai_base", ""),
        })

    # --- POST /api/v1/qonduit-router/slots/<slot_id>/stop ---
    @app.post("/api/v1/qonduit-router/slots/<slot_id>/stop")
    def slot_stop(slot_id: str):
        denied = _require_router_access()
        if denied:
            return denied

        slot, err = _slot_from_path(slot_id)
        if err:
            return err

        ok, error = stop_slot_container(slot)
        if not ok:
            return _json_error("stop_failed", error or "docker stop failed", 500)

        # Update slot status
        slots = load_slots()
        for s in slots:
            if s["slot_id"] == slot_id:
                s["running"] = False
                s["last_stopped_at"] = datetime.now(timezone.utc).isoformat()
        save_slots(slots)

        return jsonify({"ok": True, "stopped": slot_id})

    # --- POST /api/v1/qonduit-router/slots/<slot_id>/restart ---
    @app.post("/api/v1/qonduit-router/slots/<slot_id>/restart")
    def slot_restart(slot_id: str):
        denied = _require_router_access()
        if denied:
            return denied

        slot, err = _slot_from_path(slot_id)
        if err:
            return err

        # Must have a model configured
        model = slot.get("model")
        if not model:
            return _json_error("model_required", "No model configured for this slot. Launch with a model first.", 400)

        # Stop then launch
        stop_slot_container(slot)

        ok, message, error = launch_slot_container(slot, {})
        if not ok:
            return _json_error("restart_failed", error or "docker failed", 500)

        refreshed = get_slot(slot_id)
        if refreshed:
            refreshed = slot_to_live_status(refreshed)
        else:
            refreshed = slot

        return jsonify({"ok": True, "message": message, "slot": refreshed})

    # --- GET /api/v1/qonduit-router/slots/<slot_id>/ready ---
    @app.get("/api/v1/qonduit-router/slots/<slot_id>/ready")
    def slot_ready(slot_id: str):
        denied = _require_router_access()
        if denied:
            return denied

        slot, err = _slot_from_path(slot_id)
        if err:
            return err

        is_ready = check_slot_ready(slot)
        return jsonify({"ok": True, "ready": is_ready})

    # --- GET /api/v1/qonduit-router/slots/<slot_id>/models ---
    @app.get("/api/v1/qonduit-router/slots/<slot_id>/models")
    def slot_models(slot_id: str):
        denied = _require_router_access()
        if denied:
            return denied

        slot, err = _slot_from_path(slot_id)
        if err:
            return err

        data = fetch_slot_models(slot)
        if data is None:
            return _json_error("ready_check_failed", "slot is not ready or unavailable", 503)
        return jsonify(data)

    # --- GET /api/v1/qonduit-router/slots/<slot_id>/logs ---
    @app.get("/api/v1/qonduit-router/slots/<slot_id>/logs")
    def slot_logs(slot_id: str):
        denied = _require_router_access()
        if denied:
            return denied

        slot_data = get_slot(slot_id)
        if not slot_data:
            return _json_error("slot_not_found", f"slot '{slot_id}' not found", 404)

        logs_text, error_code = stream_slot_logs(slot_data)
        if error_code != 0:
            return Response(logs_text, mimetype="text/plain", status=503)
        return Response(logs_text, mimetype="text/plain")

    # --- POST /api/v1/qonduit-router/slots/<slot_id>/preflight ---
    @app.post("/api/v1/qonduit-router/slots/<slot_id>/preflight")
    def slot_preflight(slot_id: str):
        denied = _require_router_access()
        if denied:
            return denied

        slot_data = get_slot(slot_id)
        if not slot_data:
            return _json_error("slot_not_found", f"slot '{slot_id}' not found", 404)

        payload = request.get_json(silent=True) or {}

        model = (payload.get("model") or slot_data.get("model") or "").strip()
        context_size = int(
            payload.get("context_size") or slot_data.get("context_size", 65536),
        )
        gpu_devices = payload.get("gpu_devices") or slot_data.get("gpu_devices", "all")
        # tensor_split may be None if not provided in payload or slot — that's OK
        tensor_split = payload.get("tensor_split") or slot_data.get("tensor_split") or "auto"
        embeddings = payload.get("embeddings_enabled") or slot_data.get("embeddings_enabled", False)
        extra_args = payload.get("extra_args") or slot_data.get("extra_args", [])

        # ── New fields: parallel_slots, cache_type_k, cache_type_v ───────────
        allowed_cache_types = {
            "f32", "f16", "bf16", "q8_0", "q4_0",
            "q4_1", "iq4_nl", "q5_0", "q5_1",
        }

        raw_parallel_slots = payload.get(
            "parallel_slots",
            slot_data.get("parallel_slots", 1),
        )
        if raw_parallel_slots is None or (
            isinstance(raw_parallel_slots, str)
            and not raw_parallel_slots.strip()
        ):
            parallel_slots = 1
        elif isinstance(raw_parallel_slots, bool):
            return _json_error(
                "invalid_parallel_slots",
                "parallel_slots must be a positive integer",
                400,
            )
        else:
            try:
                parallel_slots = int(raw_parallel_slots)
            except (ValueError, TypeError):
                return _json_error(
                    "invalid_parallel_slots",
                    "parallel_slots must be a positive integer",
                    400,
                )
        if parallel_slots <= 0:
            return _json_error(
                "invalid_parallel_slots",
                "parallel_slots must be a positive integer",
                400,
            )

        cache_type_k = payload.get(
            "cache_type_k",
            slot_data.get("cache_type_k", "f16"),
        )
        cache_type_v = payload.get(
            "cache_type_v",
            slot_data.get("cache_type_v", "f16"),
        )

        # Defaults for omitted cache type values
        if not cache_type_k or str(cache_type_k).strip() == "":
            cache_type_k = "f16"
        else:
            cache_type_k = str(cache_type_k).strip().lower()
        if not cache_type_v or str(cache_type_v).strip() == "":
            cache_type_v = "f16"
        else:
            cache_type_v = str(cache_type_v).strip().lower()
        if cache_type_k not in allowed_cache_types:
            return _json_error(
                "invalid_cache_type_k",
                "cache_type_k must be one of the allowed cache types",
                400,
            )
        if cache_type_v not in allowed_cache_types:
            return _json_error(
                "invalid_cache_type_v",
                "cache_type_v must be one of the allowed cache types",
                400,
            )

        # ── Batch size fields: batch_size, ubatch_size ───────────────────────
        batch_size_default = 8192
        ubatch_size_default = 2048

        # Resolve batch_size from payload or slot, with defaults.
        raw_batch_size = payload.get("batch_size", slot_data.get("batch_size"))
        if raw_batch_size is None or (
            isinstance(raw_batch_size, str) and not raw_batch_size.strip()
        ):
            batch_size = batch_size_default
        elif isinstance(raw_batch_size, bool):
            return _json_error(
                "invalid_batch_size",
                "batch_size must be a positive integer",
                400,
            )
        else:
            try:
                batch_size = int(raw_batch_size)
            except (ValueError, TypeError):
                return _json_error(
                    "invalid_batch_size",
                    "batch_size must be a positive integer",
                    400,
                )
        if batch_size <= 0:
            return _json_error(
                "invalid_batch_size",
                "batch_size must be a positive integer",
                400,
            )

        # Resolve ubatch_size from payload or slot, with defaults.
        raw_ubatch_size = payload.get("ubatch_size", slot_data.get("ubatch_size"))
        if raw_ubatch_size is None or (
            isinstance(raw_ubatch_size, str) and not raw_ubatch_size.strip()
        ):
            ubatch_size = ubatch_size_default
        elif isinstance(raw_ubatch_size, bool):
            return _json_error(
                "invalid_ubatch_size",
                "ubatch_size must be a positive integer",
                400,
            )
        else:
            try:
                ubatch_size = int(raw_ubatch_size)
            except (ValueError, TypeError):
                return _json_error(
                    "invalid_ubatch_size",
                    "ubatch_size must be a positive integer",
                    400,
                )
        if ubatch_size <= 0:
            return _json_error(
                "invalid_ubatch_size",
                "ubatch_size must be a positive integer",
                400,
            )

        # Validate ubatch_size <= batch_size
        if ubatch_size > batch_size:
            return _json_error(
                "invalid_ubatch_size",
                f"ubatch_size ({ubatch_size}) must not exceed batch_size ({batch_size})",
                400,
            )

        warnings: list[str] = []
        slot_id_str = slot_id

        # Validate model
        model_path = f"/mnt/models/llm/{model}"
        model_size = 0
        if model:
            if not os.path.exists(model_path):
                return _json_error("model_not_found", f"model file not found: {model}", 404)
            try:
                model_size = os.path.getsize(model_path)
            except OSError:
                pass
        else:
            warnings.append("No model specified in preflight; using slot default.")

        # Validate GPU devices
        gpu_str = str(gpu_devices).strip()
        if gpu_str != "all":
            if not re.match(r"^[0-9]+(,[0-9]+)*$", gpu_str):
                return _json_error("invalid_gpu_devices", "Invalid GPU device list", 400)

        # ── Parse and validate tensor_split ──────────────────────────────────
        # If tensor_split is None (not provided by frontend and not in slot),
        # treat as "auto" — valid but not echoed.
        requested_tensor_split = payload.get("tensor_split", None)
        ts_str = str(tensor_split).strip()
        tensor_split_valid = True
        ts_count = 0

        if ts_str == "auto":
            # Auto mode — always valid, no count to check
            tensor_split_valid = True
            ts_count = 0
        elif ts_str:
            # Parse as comma-separated numeric values
            ts_parts = [p.strip() for p in ts_str.split(",")]
            parsed_ts: list[float] = []
            for p in ts_parts:
                if not p:
                    # Empty entry in comma-separated list
                    return _json_error(
                        "invalid_tensor_split",
                        "tensor_split contains an empty value",
                        400,
                    )
                try:
                    parsed_ts.append(float(p))
                except ValueError:
                    return _json_error(
                        "invalid_tensor_split",
                        f"tensor_split contains non-numeric value: '{p}'",
                        400,
                    )
            ts_count = len(parsed_ts)
            tensor_split_valid = True
        else:
            # Empty string — treat as auto
            ts_str = "auto"
            ts_count = 0

        # ── Extra args conflict handling ─────────────────────────────────────
        # If tensor_split is explicitly set (not "auto"), check for conflicting
        # --tensor-split or -ts in extra_args.
        tensor_split_conflict_warning = None
        if ts_str != "auto" and isinstance(extra_args, list):
            for arg in extra_args:
                arg_str = str(arg).strip()
                if arg_str in ("--tensor-split", "-ts"):
                    tensor_split_conflict_warning = (
                        f"Ignoring {arg_str} from extra_args because tensor_split field is set"
                    )
                    warnings.append(tensor_split_conflict_warning)
                    break
                # Handle --tensor-split=value form
                if arg_str.startswith("--tensor-split="):
                    tensor_split_conflict_warning = (
                        "Ignoring --tensor-split from extra_args because tensor_split field is set"
                    )
                    warnings.append(tensor_split_conflict_warning)
                    break

        # If batch_size or ubatch_size is set (non-default), check for conflicting
        # --batch-size/-b/--ubatch-size/-ub in extra_args.
        batch_conflict_warnings: list[str] = []
        if isinstance(extra_args, list):
            for arg in extra_args:
                arg_str = str(arg).strip()
                # --batch-size conflicts
                if arg_str in ("--batch-size", "-b"):
                    batch_conflict_warnings.append(
                        f"Ignoring {arg_str} from extra_args because batch_size is set"
                    )
                elif arg_str.startswith("--batch-size="):
                    batch_conflict_warnings.append(
                        "Ignoring --batch-size from extra_args because batch_size is set"
                    )
                # --ubatch-size conflicts
                elif arg_str in ("--ubatch-size", "-ub"):
                    batch_conflict_warnings.append(
                        f"Ignoring {arg_str} from extra_args because ubatch_size is set"
                    )
                elif arg_str.startswith("--ubatch-size="):
                    batch_conflict_warnings.append(
                        "Ignoring --ubatch-size from extra_args because ubatch_size is set"
                    )
        for _w in batch_conflict_warnings:
            warnings.append(_w)

        # GPU summary
        gpu_summary = collect_gpu_summary()

        # Free VRAM estimate
        free_vram = gpu_summary.get("memory_free_mib", 0) if gpu_summary.get("ok") else 0

        # Context size warnings
        if context_size >= 262144:
            warnings.append(
                "Context size is very large (>= 262144); KV cache may consume significant VRAM."
            )
        elif context_size >= 131072:
            warnings.append(
                "Context size is large (>= 131072); KV cache may consume noticeable VRAM."
            )

        # Model size warning
        if model_size > 0 and free_vram > 0:
            model_gb = model_size / (1024 ** 3)
            vram_gb = free_vram / 1024
            if model_gb > vram_gb * 0.8:
                warnings.append(
                    f"Model size ({_format_bytes_human(model_size)}) is large relative to "
                    f"available VRAM ({_format_bytes_human(free_vram * 1024 * 1024)}). "
                    "Launch may fail if VRAM is insufficient."
                )

        # Multiple containers warning
        slots = load_slots()
        running_count = sum(1 for s in slots if container_running(s))
        if running_count >= 2:
            warnings.append(
                f"Multiple llama.cpp containers already running ({running_count}). "
                "Each container loads its own copy of model weights, consuming additional VRAM."
            )

        # GPU memory warning
        if gpu_summary.get("ok") and gpu_summary.get("gpus"):
            for g in gpu_summary["gpus"]:
                if g.get("memory_used_mib", 0) > g.get("memory_total_mib", 1) * 0.8:
                    warnings.append(
                        f"GPU {g['index']} ({g['name']}) has high memory usage "
                        f"({g['memory_used_mib']}/{g['memory_total_mib']} MiB). "
                        "Insufficient VRAM may cause launch failure."
                    )

        # GPU exclusion warnings
        excluded = gpu_summary.get("excluded_gpus", [])
        if excluded:
            for ex in excluded:
                warnings.append(
                    f"GPU {ex['index']} ({ex['name']}) excluded: {ex['reason']}"
                )

        # Resolve effective GPU devices
        # When gpu_devices="all", derive from gpu_summary if available
        # (avoids calling detect_usable_gpus() directly, which would bypass mocks)
        if gpu_str.lower() == "all":
            effective_gpu = gpu_summary.get("usable_gpu_devices", "") or ""
            if not effective_gpu:
                # Fallback: try the system-level resolver
                effective_gpu = resolve_gpu_devices(gpu_str)
        else:
            effective_gpu = resolve_gpu_devices(gpu_str)

        effective_gpu_count = len([d for d in effective_gpu.split(",") if d.strip()]) if effective_gpu else 0

        # Embeddings on non-primary
        if embeddings and slot_data.get("purpose") != "primary":
            warnings.append(
                "Embeddings enabled on a non-primary slot. This may increase VRAM usage."
            )

        # Parallel slots context sharing warning
        effective_context_per_slot = max(1, context_size // max(1, parallel_slots))
        if parallel_slots > 1:
            warnings.append(
                f"Context is shared across parallel slots. Effective guaranteed "
                f"context per slot is approximately context_size / parallel_slots "
                f"({effective_context_per_slot} tokens)."
            )

        # KV cache estimate
        kv_cache_estimate = estimate_kv_cache_mib(
            context_size=context_size,
            parallel_slots=parallel_slots,
            cache_type_k=cache_type_k,
            cache_type_v=cache_type_v,
        )

        # Tensor split vs effective GPU count validation
        tensor_split_ok = True
        if ts_str != "auto" and ts_count > 0:
            # When gpu_devices is "all", effective_gpu_count reflects usable GPUs
            # When gpu_devices is explicit, count those GPUs
            if gpu_str == "all":
                check_count = effective_gpu_count
            else:
                check_count = len([d for d in gpu_str.split(",") if d.strip()])

            if ts_count != check_count:
                warnings.append(
                    f"tensor_split has {ts_count} values but {check_count} GPU(s) selected. "
                    "Values should match GPU count."
                )
                # For explicit count mismatch, mark as not OK but still return
                # the preflight with ok=true and a clear warning.
                # The frontend should not silently accept this.
                tensor_split_ok = False

        # ── Build launch_args_preview ────────────────────────────────────────
        # Pre-assemble args that would be passed to llama.cpp on launch.
        launch_args_preview: list[str] = []
        if ts_str != "auto" and ts_count > 0:
            launch_args_preview.extend(["--tensor-split", ts_str])

        # Parallel flag
        parallel_flag = "--parallel"
        probe_cache = probe_llama_server_flags()
        detected_parallel = probe_cache.get("parallel_flag", "--parallel")
        if detected_parallel == "-np":
            parallel_flag = "-np"
        elif detected_parallel == "--parallel":
            parallel_flag = "--parallel"
        if parallel_slots > 1:
            launch_args_preview.extend([parallel_flag, str(parallel_slots)])

        # Cache type flags
        launch_args_preview.extend(["--cache-type-k", cache_type_k])
        launch_args_preview.extend(["--cache-type-v", cache_type_v])

        # Batch size flags
        launch_args_preview.extend(["--batch-size", str(batch_size)])
        launch_args_preview.extend(["--ubatch-size", str(ubatch_size)])

        # ── Build suggested_tensor_splits ────────────────────────────────────
        suggested_splits = compute_suggested_tensor_splits(gpu_summary, effective_gpu_count)

        # ── Port / container availability ────────────────────────────────────
        host_port = payload.get("host_port") or slot_data.get("host_port", 8080)
        host_port_int = int(host_port)
        port_available = port_is_available(host_port_int, exclude_slot_id=slot_id)
        port_conflict = None
        if not port_available:
            conflict_info = find_port_conflict(host_port_int, exclude_slot_id=slot_id)
            if conflict_info:
                conflict_slot_id = conflict_info.get("slot_id", "unknown")
                warnings.append(
                    f"Port {host_port} conflicts with slot '{conflict_slot_id}'."
                )
                port_conflict = conflict_info
            else:
                warnings.append(f"Port {host_port} is already in use.")

        container_name = payload.get("container_name") or slot_data.get("container_name", "")
        name_available = docker_name_is_available(container_name, exclude_slot_id=slot_id)
        container_name_conflict = None
        if not name_available:
            conflict_info = find_container_name_conflict(container_name, exclude_slot_id=slot_id)
            if conflict_info:
                conflict_slot_id = conflict_info.get("slot_id", "unknown")
                warnings.append(
                    f"Container name '{container_name}' conflicts with slot '{conflict_slot_id}'."
                )
                container_name_conflict = conflict_info
            else:
                warnings.append(f"Container name '{container_name}' is already in use.")

        # ── Build response ───────────────────────────────────────────────────
        # Echo requested_tensor_split only if the frontend explicitly sent it.
        resp: dict[str, Any] = {
            "ok": True,
            "slot_id": slot_id_str,
            "model": model,
            "context_size": context_size,
            "gpu_devices": gpu_devices,
            "requested_gpu_devices": gpu_str,
            "effective_gpu_devices": effective_gpu,
            "effective_gpu_count": effective_gpu_count,
            "model_size_bytes": model_size,
            "model_size_human": _format_bytes_human(model_size) if model_size > 0 else "N/A",
            "free_vram_mb": free_vram,
            "gpu_summary": gpu_summary.get("gpus", []) if gpu_summary.get("ok") else [],
            "port_available": port_available,
            "container_name_available": name_available,
            "warnings": warnings,
            # ── tensor_split echo ──────────────────────────────────────────
            "requested_tensor_split": requested_tensor_split,
            "tensor_split": ts_str if ts_str != "auto" else None,
            "tensor_split_valid": tensor_split_valid and tensor_split_ok,
            "tensor_split_entry_count": ts_count if ts_str != "auto" else None,
            # ── parallel / cache type echo ─────────────────────────────────
            "requested_parallel_slots": payload.get("parallel_slots", None),
            "parallel_slots": parallel_slots,
            "cache_type_k": cache_type_k,
            "cache_type_v": cache_type_v,
            "effective_context_per_parallel_slot": effective_context_per_slot,
            # ── batch size echo ────────────────────────────────────────────
            "requested_batch_size": payload.get("batch_size", None),
            "requested_ubatch_size": payload.get("ubatch_size", None),
            "batch_size": batch_size,
            "ubatch_size": ubatch_size,
            # ── KV cache estimate ──────────────────────────────────────────
            "kv_cache_estimate": kv_cache_estimate,
            # ── launch args preview ────────────────────────────────────────
            "launch_args_preview": launch_args_preview,
            # ── suggested tensor splits ────────────────────────────────────
            "suggested_tensor_splits": suggested_splits,
            # ── conflict info ──────────────────────────────────────────────
            "port_conflict": port_conflict,
            "container_name_conflict": container_name_conflict,
        }

        return jsonify(resp)

    # --- GET /api/v1/qonduit-router/slot-options ---
    @app.get("/api/v1/qonduit-router/slot-options")
    def slot_options():
        denied = _require_router_access()
        if denied:
            return denied

        probe_cache = probe_llama_server_flags()
        detected_parallel = probe_cache.get("parallel_flag", "--parallel")

        return jsonify({
            "ok": True,
            "parallel": {
                "field": "parallel_slots",
                "default": 1,
                "min": 1,
                "max": 16,
                "preferred_flag": "--parallel",
                "detected_flag": detected_parallel,
                "fallback_flags": ["-np"],
                "context_semantics": (
                    "context_size is shared across parallel slots; "
                    "effective_context_per_parallel_slot = floor(context_size / parallel_slots)"
                ),
            },
            "cache_types": {
                "allowed": [
                    "f32", "f16", "bf16", "q8_0", "q4_0",
                    "q4_1", "iq4_nl", "q5_0", "q5_1",
                ],
                "default_k": "f16",
                "default_v": "f16",
                "cache_type_k_flag": "--cache-type-k",
                "cache_type_v_flag": "--cache-type-v",
            },
            "batch": {
                "batch_size_default": 8192,
                "ubatch_size_default": 2048,
                "batch_size_options": [512, 1024, 2048, 4096, 8192],
                "ubatch_size_options": [256, 512, 1024, 2048],
                "batch_size_flag": "--batch-size",
                "ubatch_size_flag": "--ubatch-size",
                "notes": (
                    "batch_size controls prompt processing batch size; "
                    "ubatch_size controls physical/micro batch size."
                ),
            },
        })

    # --- GET /api/v1/qonduit-router/endpoints ---
    @app.get("/api/v1/qonduit-router/endpoints")
    def endpoints_list():
        denied = _require_router_access()
        if denied:
            return denied

        slots = load_slots()
        result = []
        for s in slots:
            ls = slot_to_live_status(s)
            result.append({
                "slot_id": ls["slot_id"],
                "display_name": ls.get("display_name", ls["slot_id"]),
                "purpose": ls.get("purpose", "custom"),
                "openai_base": ls.get("openai_base", ""),
                "endpoint_base": ls.get("endpoint_base", ""),
                "model": ls.get("model"),
                "context_size": ls.get("context_size"),
                "running": ls.get("running", False),
                "ready": ls.get("ready", False),
            })
        return jsonify({"ok": True, "endpoints": result})

    # --- GET /api/v1/qonduit-router/gpu ---
    @app.get("/api/v1/qonduit-router/gpu")
    def gpu_info():
        denied = _require_router_access()
        if denied:
            return denied
        return jsonify(collect_gpu_summary())

    # --- GET /api/v1/qonduit-router/slot-templates ---
    @app.get("/api/v1/qonduit-router/slot-templates")
    def slot_templates():
        denied = _require_router_access()
        if denied:
            return denied

        templates = [
            {
                "name": "OpenHands",
                "slot_id": "openhands",
                "display_name": "OpenHands",
                "purpose": "openhands",
                "host_port": 8081,
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
            },
            {
                "name": "Dyad",
                "slot_id": "dyad",
                "display_name": "Dyad",
                "purpose": "dyad",
                "host_port": 8082,
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
            },
            {
                "name": "Utility 7B",
                "slot_id": "utility-7b",
                "display_name": "Utility 7B",
                "purpose": "utility",
                "host_port": 8083,
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
            },
            {
                "name": "Utility 13B",
                "slot_id": "utility-13b",
                "display_name": "Utility 13B",
                "purpose": "utility",
                "host_port": 8084,
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
            },
            {
                "name": "Testing",
                "slot_id": "testing",
                "display_name": "Testing",
                "purpose": "testing",
                "host_port": 8085,
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
            },
            {
                "name": "Embeddings",
                "slot_id": "embeddings",
                "display_name": "Embeddings",
                "purpose": "embeddings",
                "host_port": 8086,
                "context_size": 8192,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": True,
            },
        ]
        return jsonify({"ok": True, "templates": templates})
