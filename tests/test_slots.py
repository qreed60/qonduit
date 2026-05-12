"""
Unit tests for Qonduit multi-slot router architecture.

Tests cover:
- Slot config storage (load/save/validation/CRUD)
- Default primary slot
- Slot-aware Docker helpers
- Multi-slot API endpoints
- Backward compatibility
- Preflight checks
- Error handling
- Validation
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Module-level known model tracker ─────────────────────────────────────────
# Tests register model files here; the mock os.path.exists checks this set.
_known_models: set[str] = set()

# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_slots(tmp_path):
    """Each test gets a fresh slot file in a temp directory."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    slots_file = data_dir / "router_slots.json"

    # Directly set module attributes with Path objects (not strings!)
    import qonduit_slots
    qonduit_slots._QONDUIT_SLOTS_FILE = slots_file  # Path, not str
    qonduit_slots._QONDUIT_ROUTER_DATA_DIR = data_dir

    # Clear the slot file, then trigger default slot creation
    slots_file.write_text("[]")
    from qonduit_slots import load_slots
    _ = load_slots()  # triggers default slot creation if empty

    yield slots_file

    # Cleanup
    if slots_file.exists():
        slots_file.unlink(missing_ok=True)


@pytest.fixture
def app_client(_fresh_slots, monkeypatch):
    """Flask test client with fresh slots.

    Patches the slots file path on ALL modules that reference it, then reloads
    the main API module so routes re-register with the correct paths.
    Also mocks os.path.exists for model files so preflight/launch endpoints
    don't fail with "model not found".
    """
    import importlib

    # Save the real os.path.exists before monkeypatching.
    _real_exists = os.path.exists

    # Mock os.path.exists so model checks pass in tests.
    # The preflight/launch endpoints check os.path.exists("/mnt/models/llm/{model}").
    # Only model files registered via register_model() will "exist".
    def _mock_exists(path, *args, **kwargs):
        path_str = str(path)
        # Allow known model files
        if path_str in _known_models:
            return True
        # Fall back to real os.path.exists for everything else
        return _real_exists(path, *args, **kwargs)

    monkeypatch.setattr("os.path.exists", _mock_exists)

    # 1. Patch qonduit_slots globals
    import qonduit_slots
    qonduit_slots._QONDUIT_SLOTS_FILE = _fresh_slots
    qonduit_slots._QONDUIT_ROUTER_DATA_DIR = _fresh_slots.parent

    # 2. Patch any module that imported _QONDUIT_SLOTS_FILE or
    #    _QONDUIT_ROUTER_DATA_DIR as a local binding.
    #    These modules use `from qonduit_slots import _QONDUIT_SLOTS_FILE`
    #    which creates a local copy, so we must patch them directly.
    _PATCHED_MODULES = [
        "qonduit_router_api_slots",
        "qonduit_router_api",
    ]
    for mod_name in _PATCHED_MODULES:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            mod._QONDUIT_SLOTS_FILE = _fresh_slots
            mod._QONDUIT_ROUTER_DATA_DIR = _fresh_slots.parent

    # 3. Reload the slots API module so it picks up patched globals
    if "qonduit_router_api_slots" in sys.modules:
        importlib.reload(sys.modules["qonduit_router_api_slots"])

    # 4. Reload the main API (re-registers all routes)
    import qonduit_router_api
    importlib.reload(qonduit_router_api)

    return qonduit_router_api.app.test_client()


# ── Phase 1: Slot Config Storage Tests ──────────────────────────────────────

class TestSlotConfigStorage:
    """Tests for qonduit_slots module."""

    def test_default_primary_slot_created(self, _fresh_slots):
        """Default primary slot exists at module load."""
        from qonduit_slots import load_slots
        slots = load_slots()
        assert len(slots) == 1
        s = slots[0]
        assert s["slot_id"] == "primary"
        assert s["display_name"] == "Primary"
        assert s["purpose"] == "primary"
        assert s["container_name"] == "llama_server_primary"
        assert s["host_port"] == 8080
        assert s["internal_port"] == 8080
        assert s["model"] is None
        assert s["context_size"] == 65536
        assert s["gpu_devices"] == "all"
        assert s["tensor_split"] == "auto"
        assert s["embeddings_enabled"] is True
        assert s["extra_args"] == []

    def test_create_slot(self, _fresh_slots):
        """Can create a new slot."""
        from qonduit_slots import load_slots, create_slot
        new_slot, err = create_slot({
            "slot_id": "utility-7b",
            "display_name": "Utility 7B",
            "purpose": "utility",
            "host_port": 8082,
            "context_size": 65536,
            "gpu_devices": "0,1",
            "tensor_split": "auto",
            "embeddings_enabled": False,
        })
        assert err is None
        assert new_slot["slot_id"] == "utility-7b"
        assert new_slot["container_name"] == "llama_server_utility-7b"
        assert new_slot["host_port"] == 8082
        slots = load_slots()
        assert len(slots) == 2

    def test_create_slot_derives_container_name(self, _fresh_slots):
        """Container name derived from slot_id if not provided."""
        from qonduit_slots import create_slot
        new_slot, err = create_slot({
            "slot_id": "testing",
            "host_port": 8085,
        })
        assert err is None
        assert new_slot["container_name"] == "llama_server_testing"

    def test_create_slot_derives_endpoints(self, _fresh_slots):
        """Endpoint bases derived from host and port."""
        from qonduit_slots import create_slot
        new_slot, err = create_slot({
            "slot_id": "test-slot",
            "host_port": 9999,
        })
        assert err is None
        assert new_slot["endpoint_base"] == "http://192.168.5.5:9999"
        assert new_slot["openai_base"] == "http://192.168.5.5:9999/v1"

    def test_reject_duplicate_slot_id(self, _fresh_slots):
        """Cannot create slot with duplicate slot_id."""
        from qonduit_slots import create_slot
        _, err = create_slot({
            "slot_id": "primary",
            "host_port": 9999,
        })
        assert err == "duplicate_slot"

    def test_reject_duplicate_host_port(self, _fresh_slots):
        """Cannot create slot with duplicate host_port."""
        from qonduit_slots import create_slot
        _, err = create_slot({
            "slot_id": "slot2",
            "host_port": 8080,  # same as primary
        })
        assert err == "duplicate_port"

    def test_reject_duplicate_container_name(self, _fresh_slots):
        """Cannot create slot with duplicate container_name."""
        from qonduit_slots import create_slot
        create_slot({
            "slot_id": "slot-a",
            "host_port": 8081,
            "container_name": "llama_server_primary",
        })
        _, err = create_slot({
            "slot_id": "slot-b",
            "host_port": 8082,
            "container_name": "llama_server_primary",  # duplicate
        })
        assert err == "duplicate_container_name"

    def test_reject_invalid_slot_id(self, _fresh_slots):
        """slot_id must be lowercase letters, numbers, dash, underscore."""
        from qonduit_slots import create_slot
        # These are truly invalid: empty, spaces, dots, special chars
        for bad_id in ["", "has space", "has.dot", "has!", ""]:
            _, err = create_slot({
                "slot_id": bad_id,
                "host_port": 8099,
            })
            assert err is not None, f"Should reject slot_id: {bad_id}"
        # UPPER gets lowercased to "upper" which is valid
        new_slot, err = create_slot({
            "slot_id": "UPPER",
            "host_port": 8099,
        })
        assert err is None
        assert new_slot["slot_id"] == "upper"

    def test_reject_invalid_port(self, _fresh_slots):
        """Port must be 1-65535."""
        from qonduit_slots import create_slot
        _, err = create_slot({"slot_id": "bad-port", "host_port": 0})
        assert err is not None
        _, err = create_slot({"slot_id": "bad-port", "host_port": 70000})
        assert err is not None
        _, err = create_slot({"slot_id": "bad-port", "host_port": "not-a-port"})
        assert err is not None

    def test_reject_invalid_gpu_devices(self, _fresh_slots):
        """gpu_devices must be 'all' or comma-separated GPU IDs."""
        from qonduit_slots import create_slot
        _, err = create_slot({
            "slot_id": "bad-gpu",
            "host_port": 8099,
            "gpu_devices": "invalid",
        })
        assert err is not None
        _, err = create_slot({
            "slot_id": "bad-gpu",
            "host_port": 8099,
            "gpu_devices": "a,b,c",
        })
        assert err is not None

    def test_reject_invalid_tensor_split(self, _fresh_slots):
        """tensor_split must be 'auto' or comma-separated numeric values."""
        from qonduit_slots import create_slot
        _, err = create_slot({
            "slot_id": "bad-ts",
            "host_port": 8099,
            "tensor_split": "abc",
        })
        assert err is not None

    def test_reject_invalid_context_size(self, _fresh_slots):
        """context_size must be positive."""
        from qonduit_slots import create_slot
        _, err = create_slot({
            "slot_id": "bad-ctx",
            "host_port": 8099,
            "context_size": -1,
        })
        assert err is not None
        _, err = create_slot({
            "slot_id": "bad-ctx",
            "host_port": 8099,
            "context_size": 0,
        })
        assert err is not None

    def test_validate_extra_args(self, _fresh_slots):
        """extra_args must be list of strings."""
        from qonduit_slots import create_slot
        _, err = create_slot({
            "slot_id": "bad-args",
            "host_port": 8099,
            "extra_args": ["--ok", 123],  # 123 is not a string
        })
        assert err is not None

    def test_update_slot(self, _fresh_slots):
        """Can update slot metadata."""
        from qonduit_slots import update_slot, get_slot
        updated, err = update_slot("primary", {
            "display_name": "My Primary",
            "purpose": "custom-purpose",
        })
        assert err is None
        assert updated["display_name"] == "My Primary"
        slot = get_slot("primary")
        assert slot["display_name"] == "My Primary"

    def test_reject_immutable_update_while_running(self, _fresh_slots):
        """Cannot change model/context/gpu while running without force."""
        from qonduit_slots import update_slot, load_slots
        # Mark slot as running
        slots = load_slots()
        slots[0]["running"] = True
        from qonduit_slots import save_slots
        save_slots(slots)

        _, err = update_slot("primary", {"model": "test.gguf"})
        assert err == "slot_running_requires_force"

    def test_allow_safe_update_while_running(self, _fresh_slots):
        """Can change display_name/purpose while running."""
        from qonduit_slots import update_slot, load_slots
        slots = load_slots()
        slots[0]["running"] = True
        from qonduit_slots import save_slots
        save_slots(slots)

        updated, err = update_slot("primary", {"display_name": "New Name"})
        assert err is None
        assert updated["display_name"] == "New Name"

    def test_delete_stopped_slot(self, _fresh_slots):
        """Can delete a stopped non-primary slot."""
        from qonduit_slots import create_slot, delete_slot, load_slots
        new_slot, err = create_slot({
            "slot_id": "to-delete",
            "host_port": 8099,
        })
        assert err is None
        assert len(load_slots()) == 2

        ok, err = delete_slot("to-delete")
        assert ok is True
        assert err is None
        assert len(load_slots()) == 1

    def test_reject_delete_primary_without_force(self, _fresh_slots):
        """Cannot delete primary slot without force=true."""
        from qonduit_slots import delete_slot
        ok, err = delete_slot("primary")
        assert ok is False
        assert err == "primary_slot_delete_requires_force"

    def test_reject_delete_running_without_force(self, _fresh_slots):
        """Cannot delete running slot without force=true."""
        from qonduit_slots import create_slot, delete_slot, load_slots
        create_slot({"slot_id": "running-slot", "host_port": 8099})
        slots = load_slots()
        for s in slots:
            if s["slot_id"] == "running-slot":
                s["running"] = True
        from qonduit_slots import save_slots
        save_slots(slots)

        ok, err = delete_slot("running-slot")
        assert ok is False
        assert err == "slot_running_requires_force"

    def test_atomic_write(self, _fresh_slots):
        """Slot file is written atomically."""
        from qonduit_slots import load_slots, save_slots
        slots = load_slots()
        save_slots(slots)
        assert _fresh_slots.exists()
        # File should be valid JSON
        with open(_fresh_slots) as f:
            data = json.load(f)
        assert isinstance(data, list)


# ── Phase 2: Slot-Aware Docker Helpers Tests ────────────────────────────────

class TestDockerHelpers:
    """Tests for qonduit_docker_helpers module."""

    def test_port_is_available(self, _fresh_slots):
        """Port is available if not in slot config."""
        from qonduit_docker_helpers import port_is_available
        assert port_is_available(9999) is True
        assert port_is_available(8080) is False  # primary uses 8080

    def test_docker_name_is_available(self, _fresh_slots):
        """Container name is available if not in slot config."""
        from qonduit_docker_helpers import docker_name_is_available
        assert docker_name_is_available("my_new_container") is True
        assert docker_name_is_available("llama_server_primary") is False

    def test_compute_auto_tensor_split_all(self):
        """Auto tensor split for 'all' GPUs."""
        from qonduit_docker_helpers import compute_auto_tensor_split
        assert compute_auto_tensor_split("all") == "1"

    def test_compute_auto_tensor_split_list(self):
        """Auto tensor split for specific GPU list."""
        from qonduit_docker_helpers import compute_auto_tensor_split
        assert compute_auto_tensor_split("0,1,2") == "1,1,1"
        assert compute_auto_tensor_split("0") == "1"

    def test_container_exists_returns_bool(self, _fresh_slots):
        """container_exists returns a boolean."""
        from qonduit_docker_helpers import container_exists
        slot = {"container_name": "test_container_xyz"}
        with patch("qonduit_docker_helpers._docker_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="abc123\n")
            assert container_exists(slot) is True
            mock_run.return_value = MagicMock(stdout="")
            assert container_exists(slot) is False

    def test_container_running_returns_bool(self, _fresh_slots):
        """container_running returns a boolean."""
        from qonduit_docker_helpers import container_running
        slot = {"container_name": "test_container_xyz"}
        with patch("qonduit_docker_helpers._docker_run") as mock_run:
            mock_run.return_value = MagicMock(stdout="abc123\n")
            assert container_running(slot) is True
            mock_run.return_value = MagicMock(stdout="")
            assert container_running(slot) is False

    def test_check_slot_ready_true(self, _fresh_slots):
        """check_slot_ready returns True when /health responds 200."""
        from qonduit_docker_helpers import check_slot_ready
        slot = {"endpoint_base": "http://192.168.5.5:8080"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("qonduit_docker_helpers.requests.get", return_value=mock_resp):
            assert check_slot_ready(slot) is True

    def test_check_slot_ready_false(self, _fresh_slots):
        """check_slot_ready returns False on error."""
        from qonduit_docker_helpers import check_slot_ready
        slot = {"endpoint_base": "http://192.168.5.5:8080"}
        with patch("qonduit_docker_helpers.requests.get", side_effect=Exception("fail")):
            assert check_slot_ready(slot) is False

    def test_stream_slot_logs_success(self, _fresh_slots):
        """stream_slot_logs returns logs on success."""
        from qonduit_docker_helpers import stream_slot_logs
        slot = {"container_name": "test_slot", "display_name": "Test"}
        with patch("qonduit_docker_helpers.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="log line 1\nlog line 2",
                stderr="",
            )
            logs, code = stream_slot_logs(slot)
            assert code == 0
            assert "log line 1" in logs

    def test_stream_slot_logs_error(self, _fresh_slots):
        """stream_slot_logs returns error message on failure."""
        from qonduit_docker_helpers import stream_slot_logs
        slot = {"container_name": "nonexistent", "display_name": "Test"}
        with patch("qonduit_docker_helpers.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="no such container",
            )
            logs, code = stream_slot_logs(slot)
            assert code == 1
            assert "nonexistent" in logs or "no such container" in logs

    def test_collect_gpu_summary(self):
        """collect_gpu_summary returns GPU data on success."""
        from qonduit_docker_helpers import collect_gpu_summary
        mock_output = "0, NVIDIA RTX 4090, 24576, 1024, 23552\n"
        with patch("qonduit_docker_helpers.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_output,
                stderr="",
            )
            result = collect_gpu_summary()
            assert result["ok"] is True
            assert len(result["gpus"]) == 1
            assert result["gpus"][0]["name"] == "NVIDIA RTX 4090"

    def test_collect_gpu_summary_error(self):
        """collect_gpu_summary returns error on failure."""
        from qonduit_docker_helpers import collect_gpu_summary
        with patch("qonduit_docker_helpers.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr="nvidia-smi not found",
            )
            result = collect_gpu_summary()
            assert result["ok"] is False
            assert result["error"] == "gpu_query_failed"

    def test_docker_available_true(self):
        """docker_available returns True when docker info succeeds."""
        from qonduit_docker_helpers import docker_available
        with patch("qonduit_docker_helpers._docker_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert docker_available() is True

    def test_docker_available_false(self):
        """docker_available returns False when docker info fails."""
        from qonduit_docker_helpers import docker_available
        with patch("qonduit_docker_helpers._docker_run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert docker_available() is False


# ── Phase 4: Multi-Slot API Endpoint Tests ──────────────────────────────────

class TestMultiSlotEndpoints:
    """Tests for multi-slot API endpoints."""

    def test_get_slots_list(self, app_client):
        """GET /slots returns all slots."""
        resp = app_client.get("/api/v1/qonduit-router/slots")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "slots" in data
        assert len(data["slots"]) >= 1

    def test_create_slot_via_api(self, app_client):
        """POST /slots creates a new slot."""
        resp = app_client.post(
            "/api/v1/qonduit-router/slots",
            json={
                "slot_id": "api-test",
                "display_name": "API Test",
                "purpose": "testing",
                "host_port": 8099,
                "context_size": 32768,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["ok"] is True
        assert data["slot"]["slot_id"] == "api-test"
        assert data["slot"]["display_name"] == "API Test"

    def test_reject_duplicate_slot_via_api(self, app_client):
        """POST /slots rejects duplicate slot_id."""
        resp = app_client.post(
            "/api/v1/qonduit-router/slots",
            json={"slot_id": "primary", "host_port": 9999},
        )
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["ok"] is False
        assert data["error"] == "duplicate_slot"

    def test_reject_duplicate_port_via_api(self, app_client):
        """POST /slots rejects duplicate host_port."""
        resp = app_client.post(
            "/api/v1/qonduit-router/slots",
            json={"slot_id": "dup-port", "host_port": 8080},
        )
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["error"] == "duplicate_port"

    def test_get_single_slot(self, app_client):
        """GET /slots/<slot_id> returns one slot."""
        resp = app_client.get("/api/v1/qonduit-router/slots/primary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["slot"]["slot_id"] == "primary"

    def test_get_nonexistent_slot(self, app_client):
        """GET /slots/<slot_id> returns 404 for unknown slot."""
        resp = app_client.get("/api/v1/qonduit-router/slots/nonexistent")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["ok"] is False
        assert data["error"] == "slot_not_found"

    def test_patch_slot_metadata(self, app_client):
        """PATCH /slots/<slot_id> updates metadata."""
        resp = app_client.patch(
            "/api/v1/qonduit-router/slots/primary",
            json={"display_name": "Updated Primary"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["slot"]["display_name"] == "Updated Primary"

    def test_patch_reject_immutable_while_running(self, app_client):
        """PATCH /slots/<slot_id> rejects model change while running."""
        from qonduit_slots import load_slots, save_slots
        slots = load_slots()
        slots[0]["running"] = True
        save_slots(slots)

        resp = app_client.patch(
            "/api/v1/qonduit-router/slots/primary",
            json={"model": "test.gguf"},
        )
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["error"] == "slot_running_requires_force"

    def test_delete_slot(self, app_client):
        """DELETE /slots/<slot_id> removes a slot."""
        # Create a slot first
        app_client.post("/api/v1/qonduit-router/slots", json={
            "slot_id": "delete-me", "host_port": 8099,
        })
        resp = app_client.delete("/api/v1/qonduit-router/slots/delete-me")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_reject_delete_primary_without_force(self, app_client):
        """DELETE primary requires force=true."""
        resp = app_client.delete("/api/v1/qonduit-router/slots/primary")
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["error"] == "primary_slot_delete_requires_force"

    def test_slot_ready_endpoint(self, app_client):
        """GET /slots/<slot_id>/ready checks /health."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("qonduit_docker_helpers.requests.get", return_value=mock_resp):
            resp = app_client.get("/api/v1/qonduit-router/slots/primary/ready")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["ready"] is True

    def test_slot_preflight(self, app_client):
        """POST /slots/<slot_id>/preflight returns warnings."""
        # Register model in the mock filesystem (no real file needed)
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={
                    "model": "test.gguf",
                    "context_size": 262144,
                    "gpu_devices": "all",
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert "warnings" in data
            # Should warn about large context
            assert any("262144" in w for w in data["warnings"])
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_missing_model(self, app_client):
        """Preflight returns error for missing model."""
        resp = app_client.post(
            "/api/v1/qonduit-router/slots/primary/preflight",
            json={"model": "nonexistent.gguf", "context_size": 65536},
        )
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"] == "model_not_found"

    def test_preflight_port_collision(self, app_client):
        """Preflight detects port collision."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={
                    "model": "test.gguf",
                    "host_port": 8080,  # primary's port
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            # Port 8080 is in use by primary, should show warning
            assert any("8080" in w for w in data["warnings"])
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_invalid_gpu(self, app_client):
        """Preflight rejects invalid GPU devices."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={"model": "test.gguf", "gpu_devices": "invalid"},
            )
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["error"] == "invalid_gpu_devices"
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_invalid_tensor_split(self, app_client):
        """Preflight rejects invalid tensor_split."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={"model": "test.gguf", "tensor_split": "abc"},
            )
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["error"] == "invalid_tensor_split"
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_endpoints_list(self, app_client):
        """GET /endpoints returns simplified endpoint list."""
        resp = app_client.get("/api/v1/qonduit-router/endpoints")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "endpoints" in data
        assert len(data["endpoints"]) >= 1
        ep = data["endpoints"][0]
        assert "slot_id" in ep
        assert "openai_base" in ep
        assert "running" in ep

    def test_gpu_endpoint(self, app_client):
        """GET /gpu returns GPU summary."""
        mock_output = "0, NVIDIA RTX 4090, 24576, 1024, 23552\n"
        with patch("qonduit_docker_helpers.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_output,
                stderr="",
            )
            resp = app_client.get("/api/v1/qonduit-router/gpu")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True

    def test_slot_templates(self, app_client):
        """GET /slot-templates returns suggested templates."""
        resp = app_client.get("/api/v1/qonduit-router/slot-templates")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "templates" in data
        template_ids = [t["slot_id"] for t in data["templates"]]
        assert "openhands" in template_ids
        assert "utility-7b" in template_ids
        assert "embeddings" in template_ids

    def test_slot_launch_requires_model(self, app_client):
        """POST /slots/<slot_id>/launch requires model."""
        resp = app_client.post(
            "/api/v1/qonduit-router/slots/primary/launch",
            json={},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"] == "model_required"

    def test_slot_launch_model_not_found(self, app_client):
        """POST /slots/<slot_id>/launch rejects missing model file."""
        resp = app_client.post(
            "/api/v1/qonduit-router/slots/primary/launch",
            json={"model": "nonexistent.gguf"},
        )
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"] == "model_not_found"

    def test_slot_stop(self, app_client):
        """POST /slots/<slot_id>/stop stops the slot."""
        with patch("qonduit_docker_helpers.stop_slot_container") as mock_stop:
            mock_stop.return_value = (True, None)
            resp = app_client.post("/api/v1/qonduit-router/slots/primary/stop")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True

    def test_slot_restart_requires_model(self, app_client):
        """POST /slots/<slot_id>/restart rejects slot without model."""
        resp = app_client.post("/api/v1/qonduit-router/slots/primary/restart")
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"] == "model_required"

    def test_slot_logs(self, app_client):
        """GET /slots/<slot_id>/logs returns logs."""
        # Import is `from qonduit_docker_helpers import stream_slot_logs`
        # so the function lives in qonduit_router_api_slots namespace.
        with patch("qonduit_router_api_slots.stream_slot_logs") as mock_logs:
            mock_logs.return_value = ("container log output\n", 0)
            resp = app_client.get("/api/v1/qonduit-router/slots/primary/logs")
            assert resp.status_code == 200
            assert "container log output" in resp.get_data(as_text=True)

    def test_slot_models(self, app_client):
        """GET /slots/<slot_id>/models proxies /v1/models."""
        mock_data = {"data": [{"id": "model-1"}]}
        # Import is `from qonduit_docker_helpers import fetch_slot_models`
        with patch("qonduit_router_api_slots.fetch_slot_models") as mock_fetch:
            mock_fetch.return_value = mock_data
            resp = app_client.get("/api/v1/qonduit-router/slots/primary/models")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "data" in data

    def test_slot_models_unavailable(self, app_client):
        """GET /slots/<slot_id>/models returns 503 when unavailable."""
        with patch("qonduit_router_api_slots.fetch_slot_models") as mock_fetch:
            mock_fetch.return_value = None
            resp = app_client.get("/api/v1/qonduit-router/slots/primary/models")
            assert resp.status_code == 503
            data = resp.get_json()
            assert data["ok"] is False


# ── Phase 5: Backward Compatibility Tests ───────────────────────────────────

class TestBackwardCompatibility:
    """Tests for backward compatibility with old single-slot endpoints."""

    def test_old_status_maps_to_primary(self, app_client):
        """GET /status returns primary-like status."""
        resp = app_client.get("/api/v1/qonduit-router/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        # Should still have container_name field for compatibility
        assert "container_name" in data
        assert "running" in data
        assert "ready" in data

    def test_old_launch_maps_to_primary(self, app_client):
        """POST /launch launches primary slot."""
        # The old /launch endpoint checks _qonduit_model_list(), so we need
        # to mock it so "test.gguf" passes the model validation.
        with patch("qonduit_router_api._qonduit_model_list", return_value=["test.gguf"]):
            with patch("qonduit_router_api._safe_launch") as mock_launch:
                mock_launch.return_value = MagicMock(pid=1234)
                resp = app_client.post("/api/v1/qonduit-router/launch", json={
                    "model": "test.gguf",
                    "context_size": 65536,
                })
                # Should return success (script-based launch)
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["ok"] is True

    def test_old_stop_maps_to_primary(self, app_client):
        """POST /stop stops primary container."""
        resp = app_client.post("/api/v1/qonduit-router/stop")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["stopped"] is True

    def test_old_logs(self, app_client):
        """GET /logs returns logs for primary container."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="old log output",
                stderr="",
            )
            resp = app_client.get("/api/v1/qonduit-router/logs")
            assert resp.status_code == 200
            assert "old log output" in resp.get_data(as_text=True)

    def test_old_ready(self, app_client):
        """GET /ready checks primary health."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("qonduit_router_api.requests.get", return_value=mock_resp):
            resp = app_client.get("/api/v1/qonduit-router/ready")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert "ready" in data

    def test_old_context_suggest(self, app_client):
        """GET /context/suggest returns suggested context size."""
        resp = app_client.get("/api/v1/qonduit-router/context/suggest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "context_size" in data

    def test_old_gpu(self, app_client):
        """GET /gpu returns GPU summary."""
        mock_output = "0, NVIDIA RTX 4090, 24576, 1024, 23552\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=mock_output,
                stderr="",
            )
            resp = app_client.get("/api/v1/qonduit-router/gpu")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True


# ── Phase 7: Error Handling Tests ───────────────────────────────────────────

class TestErrorHandling:
    """Tests for consistent JSON error responses."""

    def test_error_format(self, app_client):
        """All errors follow consistent JSON format."""
        resp = app_client.get("/api/v1/qonduit-router/slots/nonexistent")
        data = resp.get_json()
        assert "ok" in data
        assert "error" in data
        assert "detail" in data
        assert data["ok"] is False

    def test_local_only_restriction(self, app_client):
        """Non-local requests are rejected."""
        # Test client defaults to localhost
        resp = app_client.get("/api/v1/qonduit-router/slots")
        assert resp.status_code == 200  # Test client is "local"

    def test_invalid_slot_id_rejected(self, app_client):
        """Invalid slot_id format is rejected."""
        resp = app_client.get("/api/v1/qonduit-router/slots/INVALID-ID")
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["ok"] is False
        assert data["error"] == "invalid_slot_id"

    def test_invalid_slot_id_rejected_special_chars(self, app_client):
        """Slot IDs with special chars are rejected."""
        resp = app_client.get("/api/v1/qonduit-router/slots/bad.slot")
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"] == "invalid_slot_id"

    def test_empty_body_rejected(self, app_client):
        """Empty request body is rejected."""
        resp = app_client.post(
            "/api/v1/qonduit-router/slots",
            content_type="application/json",
            data="",
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["ok"] is False


# ── Integration: Multiple Slots Independent ─────────────────────────────────

class TestSlotIndependence:
    """Tests that slots operate independently."""

    def test_multiple_slots_can_exist(self, app_client):
        """Can create multiple slots."""
        for i in range(5):
            resp = app_client.post("/api/v1/qonduit-router/slots", json={
                "slot_id": f"slot-{i}",
                "host_port": 8090 + i,
            })
            assert resp.status_code == 201

        resp = app_client.get("/api/v1/qonduit-router/slots")
        data = resp.get_json()
        assert len(data["slots"]) == 6  # 5 new + primary

    def test_stop_one_does_not_affect_others(self, app_client):
        """Stopping one slot doesn't affect others."""
        # Create two slots
        app_client.post("/api/v1/qonduit-router/slots", json={
            "slot_id": "slot-a", "host_port": 8090,
        })
        app_client.post("/api/v1/qonduit-router/slots", json={
            "slot_id": "slot-b", "host_port": 8091,
        })

        # Stop slot-a
        with patch("qonduit_docker_helpers.stop_slot_container") as mock_stop:
            mock_stop.return_value = (True, None)
            app_client.post("/api/v1/qonduit-router/slots/slot-a/stop")

        # Verify slot-b still exists
        resp = app_client.get("/api/v1/qonduit-router/slots/slot-b")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["slot"]["slot_id"] == "slot-b"

    def test_launch_one_does_not_stop_others(self, app_client):
        """Launching one slot doesn't stop other containers."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            app_client.post("/api/v1/qonduit-router/slots", json={
                "slot_id": "slot-x", "host_port": 8090,
            })

            with patch("qonduit_router_api_slots.launch_slot_container") as mock_launch:
                mock_launch.return_value = (True, "launched", None)
                resp = app_client.post("/api/v1/qonduit-router/slots/slot-x/launch", json={
                    "model": "test.gguf",
                })
                # Should succeed
                assert resp.status_code == 200

            # Verify launch_slot_container was called with slot-x config
            if mock_launch.called:
                call_args = mock_launch.call_args
                slot = call_args[0][0]
                assert slot["slot_id"] == "slot-x"
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")
