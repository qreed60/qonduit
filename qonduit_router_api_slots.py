"""
Qonduit Router — multi-slot API endpoints (Phases 3-7).

Registers all new slot-aware endpoints on the existing Flask app.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

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
    collect_gpu_summary,
    compute_auto_tensor_split,
    resolve_gpu_devices,
    docker_available,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _json_error(
    code: str,
    detail: str,
    status: int = 400,
) -> tuple[dict[str, Any], int]:
    """Return a consistent JSON error response."""
    return jsonify({"ok": False, "error": code, "detail": detail}), status


def _check_local() -> Optional[tuple[dict[str, Any], int]]:
    """Check request is from local. Returns error response if not, else None."""
    client_ip = request.remote_addr
    if client_ip not in ("127.0.0.1", "::1", "localhost"):
        return jsonify({"ok": False, "error": "local_only"}), 403
    return None


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
        denied = _check_local()
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
        denied = _check_local()
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
        denied = _check_local()
        if denied:
            return denied

        slot, err = _slot_from_path(slot_id)
        if err:
            return err
        return jsonify({"ok": True, "slot": slot})

    # --- PATCH /api/v1/qonduit-router/slots/<slot_id> ---
    @app.patch("/api/v1/qonduit-router/slots/<slot_id>")
    def slot_update(slot_id: str):
        denied = _check_local()
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
        denied = _check_local()
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
        denied = _check_local()
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
        denied = _check_local()
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
        denied = _check_local()
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
        denied = _check_local()
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
        denied = _check_local()
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
        denied = _check_local()
        if denied:
            return denied

        slot_data = get_slot(slot_id)
        if not slot_data:
            return _json_error("slot_not_found", f"slot '{slot_id}' not found", 404)

        logs_text, error_code = stream_slot_logs(slot_data)
        if error_code != 0:
            return Response(logs_text, mimetype="text/plain", status=503)
        return Response(logs_text, mimetype="text/plain")

    # --- GET /api/v1/qonduit-router/slots/<slot_id>/preflight ---
    @app.post("/api/v1/qonduit-router/slots/<slot_id>/preflight")
    def slot_preflight(slot_id: str):
        denied = _check_local()
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
        tensor_split = payload.get("tensor_split") or slot_data.get("tensor_split", "auto")
        embeddings = payload.get("embeddings_enabled") or slot_data.get("embeddings_enabled", False)

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
        gpu_err = None
        gpu_str = str(gpu_devices).strip()
        if gpu_str != "all":
            if not re.match(r"^[0-9]+(,[0-9]+)*$", gpu_str):
                return _json_error("invalid_gpu_devices", "Invalid GPU device list", 400)

        # Validate tensor split
        ts_str = str(tensor_split).strip()
        if ts_str != "auto":
            ts_parts = ts_str.split(",")
            for p in ts_parts:
                try:
                    float(p.strip())
                except ValueError:
                    return _json_error("invalid_tensor_split", "Invalid tensor_split value", 400)

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
        effective_gpu = resolve_gpu_devices(gpu_str)

        # Embeddings on non-primary
        if embeddings and slot_data.get("purpose") != "primary":
            warnings.append(
                "Embeddings enabled on a non-primary slot. This may increase VRAM usage."
            )

        # Tensor split vs GPU count mismatch
        if ts_str != "auto" and gpu_str != "all":
            gpu_count = len(gpu_str.split(","))
            ts_count = len(ts_str.split(","))
            if ts_count != gpu_count:
                warnings.append(
                    f"tensor_split has {ts_count} values but {gpu_count} GPUs selected. "
                    "Values should match GPU count."
                )

        # Port availability
        host_port = payload.get("host_port") or slot_data.get("host_port", 8080)
        port_available = port_is_available(int(host_port))
        if not port_available:
            warnings.append(f"Port {host_port} is already in use by another slot.")

        # Container name availability
        container_name = payload.get("container_name") or slot_data.get("container_name", "")
        name_available = docker_name_is_available(container_name)
        if not name_available:
            warnings.append(f"Container name '{container_name}' is already in use.")

        return jsonify({
            "ok": True,
            "slot_id": slot_id_str,
            "model": model,
            "context_size": context_size,
            "gpu_devices": gpu_devices,
            "requested_gpu_devices": gpu_str,
            "effective_gpu_devices": effective_gpu,
            "model_size_bytes": model_size,
            "model_size_human": _format_bytes_human(model_size) if model_size > 0 else "N/A",
            "free_vram_mb": free_vram,
            "gpu_summary": gpu_summary.get("gpus", []) if gpu_summary.get("ok") else [],
            "port_available": port_available,
            "container_name_available": name_available,
            "warnings": warnings,
        })

    # --- GET /api/v1/qonduit-router/endpoints ---
    @app.get("/api/v1/qonduit-router/endpoints")
    def endpoints_list():
        denied = _check_local()
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
        denied = _check_local()
        if denied:
            return denied
        return jsonify(collect_gpu_summary())

    # --- GET /api/v1/qonduit-router/slot-templates ---
    @app.get("/api/v1/qonduit-router/slot-templates")
    def slot_templates():
        denied = _check_local()
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
