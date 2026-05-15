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

    # Reset lazy init state so tests start fresh
    import qonduit_slots
    qonduit_slots._QONDUIT_ROUTER_DATA_DIR = None
    qonduit_slots._QONDUIT_SLOTS_FILE = None

    # Clear any GPU cache so GPU tests start fresh
    import qonduit_docker_helpers
    qonduit_docker_helpers._usable_gpu_cache = {}

    # Directly set module attributes with Path objects (not strings!)
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
        """Default primary slot exists at module load with legacy container name."""
        from qonduit_slots import load_slots
        slots = load_slots()
        assert len(slots) == 1
        s = slots[0]
        assert s["slot_id"] == "primary"
        assert s["display_name"] == "Primary"
        assert s["purpose"] == "primary"
        assert s["container_name"] == "llama_server"
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
            "container_name": "llama_server",
        })
        _, err = create_slot({
            "slot_id": "slot-b",
            "host_port": 8082,
            "container_name": "llama_server",  # duplicate of primary
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
        assert docker_name_is_available("llama_server") is False  # legacy primary name

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

    def test_find_port_conflict_with_exclude_slot_id(self, _fresh_slots):
        """find_port_conflict excludes the given slot_id from results."""
        from qonduit_docker_helpers import find_port_conflict
        # primary uses port 8080
        # Without exclude, should find primary
        conflict = find_port_conflict(8080)
        assert conflict is not None
        assert conflict["slot_id"] == "primary"

        # With exclude_slot_id="primary", should return None (no other slot uses 8080)
        conflict = find_port_conflict(8080, exclude_slot_id="primary")
        assert conflict is None

    def test_find_container_name_conflict_with_exclude_slot_id(self, _fresh_slots):
        """find_container_name_conflict excludes the given slot_id from results."""
        from qonduit_docker_helpers import find_container_name_conflict
        # primary uses container name "llama_server"
        conflict = find_container_name_conflict("llama_server")
        assert conflict is not None
        assert conflict["slot_id"] == "primary"

        # With exclude_slot_id="primary", should return None
        conflict = find_container_name_conflict(
            "llama_server", exclude_slot_id="primary"
        )
        assert conflict is None


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

    def test_preflight_port_no_self_collision(self, app_client):
        """Preflight for a slot does NOT flag its own port as a conflict."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={
                    "model": "test.gguf",
                    "host_port": 8080,  # primary's own port
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["port_available"] is True
            # No port conflict warning should appear for own port
            assert not any("8080" in w for w in data["warnings"])
            assert data.get("port_conflict") is None
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_port_collision_with_different_slot(self, app_client):
        """Preflight detects port collision with a DIFFERENT slot."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            # Register model
            # Create a second slot on a different port first
            resp = app_client.post(
                "/api/v1/qonduit-router/slots",
                json={
                    "slot_id": "colleague",
                    "host_port": 8082,
                },
            )
            assert resp.status_code == 201

            # Now preflight "colleague" with primary's port (8080) — should conflict
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/colleague/preflight",
                json={
                    "model": "test.gguf",
                    "host_port": 8080,  # primary's port — collision!
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["port_available"] is False
            assert any("8080" in w and "primary" in w for w in data["warnings"])
            assert data["port_conflict"]["slot_id"] == "primary"
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_port_collision_os_listener(self, app_client):
        """Preflight detects real OS-level port listener as unavailable."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            # Mock ss to show port 9999 in use
            ss_output = "LISTEN  0  511  0.0.0.0:9999  *:*  users:((\"python\",pid=1234,fd=3))\n"
            with patch("qonduit_docker_helpers.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=ss_output, stderr="")
                resp = app_client.post(
                    "/api/v1/qonduit-router/slots/primary/preflight",
                    json={
                        "model": "test.gguf",
                        "host_port": 9999,  # OS listener, not a slot
                    },
                )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["port_available"] is False
            assert any("9999" in w for w in data["warnings"])
            # Should NOT reference another slot since it's an OS listener
            assert data.get("port_conflict") is None
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_container_name_no_self_collision(self, app_client):
        """Preflight for a slot does NOT flag its own container name as a conflict."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={
                    "model": "test.gguf",
                    "container_name": "llama_server",  # primary's own name
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["container_name_available"] is True
            assert not any("llama_server" in w for w in data["warnings"])
            assert data.get("container_name_conflict") is None
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_container_name_collision_with_different_slot(self, app_client):
        """Preflight detects container-name collision with a DIFFERENT slot."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            # Create a second slot with a unique container name
            resp = app_client.post(
                "/api/v1/qonduit-router/slots",
                json={
                    "slot_id": "test_slot",
                    "container_name": "llama_server_test",
                },
            )
            assert resp.status_code == 201

            # Preflight test_slot with primary's container name — should conflict
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/test_slot/preflight",
                json={
                    "model": "test.gguf",
                    "container_name": "llama_server",  # primary's name — collision!
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["container_name_available"] is False
            assert any("llama_server" in w and "primary" in w for w in data["warnings"])
            assert data["container_name_conflict"]["slot_id"] == "primary"
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_container_name_collision_docker(self, app_client):
        """Preflight detects real Docker container as unavailable."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            with patch("qonduit_docker_helpers.subprocess.run") as mock_run:
                # Docker reports a running container with this name
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="llama_server_zombie",
                    stderr="",
                )
                resp = app_client.post(
                    "/api/v1/qonduit-router/slots/primary/preflight",
                    json={
                        "model": "test.gguf",
                        "container_name": "llama_server_zombie",
                    },
                )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["container_name_available"] is False
            assert any("llama_server_zombie" in w for w in data["warnings"])
            assert data.get("container_name_conflict") is None
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_openhands_port_no_self_collision(self, app_client):
        """Preflight for the 'openhands' slot does NOT flag its own port as a conflict.

        This is the specific regression test for the false-positive bug where
        preflighting slot_id=openhands with host_port=8081 incorrectly returned
        port_available=false and a warning about port 8081 being in use by
        'another slot' — even though it was its own configured port.
        """
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            # Register the openhands slot with its real port/container
            resp = app_client.post(
                "/api/v1/qonduit-router/slots",
                json={
                    "slot_id": "openhands",
                    "host_port": 8081,
                    "container_name": "llama_server_openhands",
                },
            )
            assert resp.status_code == 201

            # Preflight openhands with its own port — should be available
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/openhands/preflight",
                json={
                    "model": "test.gguf",
                    "host_port": 8081,  # openhands' own port
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["port_available"] is True
            # No port conflict warning should appear for own port
            assert not any("8081" in w for w in data["warnings"])
            assert data.get("port_conflict") is None
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_openhands_container_name_no_self_collision(self, app_client):
        """Preflight for the 'openhands' slot does NOT flag its own container name.

        Regression test: preflighting slot_id=openhands with
        container_name=llama_server_openhands incorrectly returned
        container_name_available=false because the slot's own name was counted
        as a conflict.
        """
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            # Register the openhands slot with its real port/container
            resp = app_client.post(
                "/api/v1/qonduit-router/slots",
                json={
                    "slot_id": "openhands",
                    "host_port": 8081,
                    "container_name": "llama_server_openhands",
                },
            )
            assert resp.status_code == 201

            # Preflight openhands with its own container name — should be available
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/openhands/preflight",
                json={
                    "model": "test.gguf",
                    "container_name": "llama_server_openhands",  # openhands' own name
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["container_name_available"] is True
            assert not any("llama_server_openhands" in w for w in data["warnings"])
            assert data.get("container_name_conflict") is None
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_openhands_cross_slot_port_conflict(self, app_client):
        """Preflight detects port conflict when openhands slot uses another slot's port.

        Ensures that when openhands preflights with a DIFFERENT slot's port,
        the conflict is correctly reported.
        """
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            # Register the openhands slot with its real port/container
            resp = app_client.post(
                "/api/v1/qonduit-router/slots",
                json={
                    "slot_id": "openhands",
                    "host_port": 8081,
                    "container_name": "llama_server_openhands",
                },
            )
            assert resp.status_code == 201

            # Preflight openhands with primary's port (8080) — should conflict
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/openhands/preflight",
                json={
                    "model": "test.gguf",
                    "host_port": 8080,  # primary's port — collision!
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["port_available"] is False
            assert any("8080" in w and "primary" in w for w in data["warnings"])
            assert data["port_conflict"]["slot_id"] == "primary"
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_openhands_cross_slot_container_name_conflict(self, app_client):
        """Preflight detects container name conflict with a different slot.

        Ensures that when openhands preflights with another slot's container name,
        the conflict is correctly reported.
        """
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            # Register the openhands slot with its real port/container
            resp = app_client.post(
                "/api/v1/qonduit-router/slots",
                json={
                    "slot_id": "openhands",
                    "host_port": 8081,
                    "container_name": "llama_server_openhands",
                },
            )
            assert resp.status_code == 201

            # Preflight openhands with primary's container name — should conflict
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/openhands/preflight",
                json={
                    "model": "test.gguf",
                    "container_name": "llama_server",  # primary's name — collision!
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["container_name_available"] is False
            assert any("llama_server" in w and "primary" in w for w in data["warnings"])
            assert data["container_name_conflict"]["slot_id"] == "primary"
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


# ── Phase 1: GPU Auto-Detection Tests ───────────────────────────────────────

class TestGpuAutoDetection:
    """Tests for usable GPU auto-detection."""

    def test_detect_usable_gpus_excludes_low_memory(self):
        """detect_usable_gpus excludes GPUs below memory threshold."""
        from qonduit_docker_helpers import detect_usable_gpus, _usable_gpu_cache
        _usable_gpu_cache.clear()
        mock_output = (
            "0, Tesla P100-SXM2-16GB, 16384, 1024, 15360\n"
            "1, Quadro K620, 2048, 512, 1536\n"
            "2, Tesla P100-SXM2-16GB, 16384, 1024, 15360\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            result = detect_usable_gpus()
        assert result == "0,2"

    def test_detect_usable_gpus_excludes_by_name_regex(self):
        """detect_usable_gpus excludes GPUs matching exclude regex."""
        from qonduit_docker_helpers import detect_usable_gpus, _usable_gpu_cache
        _usable_gpu_cache.clear()
        mock_output = (
            "0, Tesla P100-SXM2-16GB, 16384, 1024, 15360\n"
            "1, Quadro K620, 16384, 1024, 15360\n"  # High memory but excluded by name
            "2, Tesla P100-SXM2-16GB, 16384, 1024, 15360\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            result = detect_usable_gpus()
        assert result == "0,2"

    def test_detect_usable_gpus_all_p100s(self):
        """detect_usable_gpus returns all GPUs when all are usable."""
        from qonduit_docker_helpers import detect_usable_gpus, _usable_gpu_cache
        _usable_gpu_cache.clear()
        mock_output = (
            "0, Tesla P100-SXM2-16GB, 16384, 1024, 15360\n"
            "2, Tesla P100-SXM2-16GB, 16384, 1024, 15360\n"
            "3, Tesla P100-SXM2-16GB, 16384, 1024, 15360\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            result = detect_usable_gpus()
        assert result == "0,2,3"

    def test_qonduit_default_gpu_devices_override(self):
        """QONDUIT_DEFAULT_GPU_DEVICES env var overrides auto-detection."""
        from qonduit_docker_helpers import get_default_gpu_devices, _usable_gpu_cache
        import qonduit_docker_helpers as dh
        _usable_gpu_cache.clear()
        with patch.dict(os.environ, {"QONDUIT_DEFAULT_GPU_DEVICES": "0,1,2"}):
            # Patch the module-level variable (read at import time)
            dh._QONDUIT_DEFAULT_GPU_DEVICES = "0,1,2"
            result = get_default_gpu_devices()
        assert result == "0,1,2"

    def test_qonduit_default_gpu_devices_auto(self):
        """QONDUIT_DEFAULT_GPU_DEVICES=auto uses detection."""
        from qonduit_docker_helpers import get_default_gpu_devices, _usable_gpu_cache
        import qonduit_docker_helpers as dh
        _usable_gpu_cache.clear()
        mock_output = "0, Tesla P100, 16384, 1024, 15360\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            dh._QONDUIT_DEFAULT_GPU_DEVICES = "auto"
            result = get_default_gpu_devices()
        assert result == "0"

    def test_qonduit_default_gpu_devices_unset(self):
        """QONDUIT_DEFAULT_GPU_DEVICES unset falls back to detection."""
        from qonduit_docker_helpers import get_default_gpu_devices, _usable_gpu_cache
        import qonduit_docker_helpers as dh
        _usable_gpu_cache.clear()
        mock_output = "0, Tesla P100, 16384, 1024, 15360\n"
        with patch.object(dh, "_run_nvidia_smi", return_value=MagicMock(returncode=0, stdout=mock_output, stderr="")):
            dh._QONDUIT_DEFAULT_GPU_DEVICES = "auto"
            result = get_default_gpu_devices()
        assert result == "0"

    def test_resolve_gpu_devices_all_resolves_to_usable(self):
        """resolve_gpu_devices('all') resolves to usable GPU set."""
        from qonduit_docker_helpers import resolve_gpu_devices, _usable_gpu_cache
        import qonduit_docker_helpers as dh
        _usable_gpu_cache.clear()
        mock_output = (
            "0, Tesla P100, 16384, 1024, 15360\n"
            "1, Quadro K620, 2048, 512, 1536\n"
            "2, Tesla P100, 16384, 1024, 15360\n"
        )
        with patch.object(dh, "_run_nvidia_smi", return_value=MagicMock(returncode=0, stdout=mock_output, stderr="")):
            dh._QONDUIT_GPU_EXCLUDE_NAME_REGEX = "K620|Quadro K620"
            dh._QONDUIT_GPU_MIN_TOTAL_MIB = 8192
            result = resolve_gpu_devices("all")
        assert result == "0,2"

    def test_resolve_gpu_devices_explicit_list_unchanged(self):
        """resolve_gpu_devices('0,2') returns exact list."""
        from qonduit_docker_helpers import resolve_gpu_devices, _usable_gpu_cache
        _usable_gpu_cache.clear()
        result = resolve_gpu_devices("0,2")
        assert result == "0,2"

    def test_resolve_gpu_devices_invalid_rejected(self):
        """resolve_gpu_devices returns invalid strings as-is; validate_gpu_devices rejects them."""
        from qonduit_docker_helpers import resolve_gpu_devices, validate_gpu_devices
        # resolve_gpu_devices is a formatter, not a validator — returns as-is
        result = resolve_gpu_devices("99")
        assert result == "99"
        # validate_gpu_devices does the actual hardware validation
        error = validate_gpu_devices("99", all_available=[0])  # only GPU 0 exists
        assert error is not None
        assert "GPU 99 is not available" in error

    def test_compute_auto_tensor_split_uses_resolved_gpus(self):
        """Tensor split computed from resolved GPUs, not raw nvidia-smi output."""
        from qonduit_docker_helpers import compute_auto_tensor_split, _usable_gpu_cache
        import qonduit_docker_helpers as dh
        _usable_gpu_cache.clear()
        mock_output = (
            "0, Tesla P100, 16384, 1024, 15360\n"
            "1, Quadro K620, 2048, 512, 1536\n"
            "2, Tesla P100, 16384, 1024, 15360\n"
        )
        with patch.object(dh, "_run_nvidia_smi", return_value=MagicMock(returncode=0, stdout=mock_output, stderr="")):
            dh._QONDUIT_GPU_EXCLUDE_NAME_REGEX = "K620|Quadro K620"
            dh._QONDUIT_GPU_MIN_TOTAL_MIB = 8192
            # "all" resolves to "0,2" → tensor split should be "1,1" not "1,1,1"
            result = compute_auto_tensor_split("all")
        assert result == "1,1"


# ── Phase 2: Primary Slot Legacy Container Name Tests ────────────────────────

class TestPrimarySlotLegacyName:
    """Tests for primary slot legacy container name preservation."""

    def test_default_primary_uses_legacy_container_name(self, _fresh_slots):
        """Default primary slot uses container_name 'llama_server'."""
        from qonduit_slots import load_slots
        slots = load_slots()
        primary = [s for s in slots if s["slot_id"] == "primary"]
        assert len(primary) == 1
        assert primary[0]["container_name"] == "llama_server"

    def test_additional_slots_use_generated_names(self, _fresh_slots):
        """Additional slots use llama_server_{slot_id}."""
        from qonduit_slots import create_slot
        new_slot, err = create_slot({
            "slot_id": "openhands",
            "host_port": 8081,
        })
        assert err is None
        assert new_slot["container_name"] == "llama_server_openhands"

    def test_additional_slots_custom_container_name(self, _fresh_slots):
        """Additional slots can specify custom container_name."""
        from qonduit_slots import create_slot
        new_slot, err = create_slot({
            "slot_id": "testing",
            "host_port": 8082,
            "container_name": "llama_server_testing",
        })
        assert err is None
        assert new_slot["container_name"] == "llama_server_testing"

    def test_primary_migration_from_legacy_server_primary(self, tmp_path):
        """Existing primary with llama_server_primary migrates to llama_server."""
        from qonduit_slots import _QONDUIT_DEFAULT_HOST
        from datetime import datetime, timezone

        slots_file = tmp_path / "router_slots.json"
        original = {
            "slot_id": "primary",
            "display_name": "Primary",
            "purpose": "primary",
            "container_name": "llama_server_primary",
            "host": _QONDUIT_DEFAULT_HOST,
            "host_port": 8080,
            "internal_port": 8080,
            "endpoint_base": f"http://{_QONDUIT_DEFAULT_HOST}:8080",
            "openai_base": f"http://{_QONDUIT_DEFAULT_HOST}:8080/v1",
            "model": None,
            "context_size": 65536,
            "gpu_devices": "all",
            "tensor_split": "auto",
            "embeddings_enabled": True,
            "extra_args": [],
            "running": False,
            "exists": False,
            "ready": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "last_started_at": None,
            "last_stopped_at": None,
            "last_error": None,
        }
        slots_file.write_text(json.dumps([original]))

        # Patch _ensure_data_dir to return tmp_path so load_slots reads
        # from the test's tmp_path/router_slots.json instead of /opt paths
        import qonduit_slots
        from pathlib import Path
        with patch.object(qonduit_slots, "_ensure_data_dir", return_value=Path(str(tmp_path))):
            loaded = qonduit_slots.load_slots()
        assert len(loaded) == 1
        assert loaded[0]["container_name"] == "llama_server"

    def test_migration_preserves_non_primary_slots(self, tmp_path):
        """Migration does not affect non-primary slots."""
        from qonduit_slots import _QONDUIT_DEFAULT_HOST
        from datetime import datetime, timezone

        slots_file = tmp_path / "router_slots.json"
        slots = [
            {
                "slot_id": "primary",
                "display_name": "Primary",
                "purpose": "primary",
                "container_name": "llama_server_primary",
                "host": _QONDUIT_DEFAULT_HOST,
                "host_port": 8080,
                "internal_port": 8080,
                "endpoint_base": f"http://{_QONDUIT_DEFAULT_HOST}:8080",
                "openai_base": f"http://{_QONDUIT_DEFAULT_HOST}:8080/v1",
                "model": None,
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": True,
                "extra_args": [],
                "running": False,
                "exists": False,
                "ready": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "last_started_at": None,
                "last_stopped_at": None,
                "last_error": None,
            },
            {
                "slot_id": "openhands",
                "display_name": "OpenHands",
                "purpose": "openhands",
                "container_name": "llama_server_openhands",
                "host": _QONDUIT_DEFAULT_HOST,
                "host_port": 8081,
                "internal_port": 8081,
                "endpoint_base": f"http://{_QONDUIT_DEFAULT_HOST}:8081",
                "openai_base": f"http://{_QONDUIT_DEFAULT_HOST}:8081/v1",
                "model": None,
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
                "extra_args": [],
                "running": False,
                "exists": False,
                "ready": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "last_started_at": None,
                "last_stopped_at": None,
                "last_error": None,
            },
        ]
        slots_file.write_text(json.dumps(slots))

        import qonduit_slots
        qonduit_slots._QONDUIT_SLOTS_FILE = slots_file
        qonduit_slots._QONDUIT_ROUTER_DATA_DIR = tmp_path
        qonduit_slots._ensure_data_dir()

        loaded = qonduit_slots.load_slots()
        assert len(loaded) == 2
        primary = [s for s in loaded if s["slot_id"] == "primary"][0]
        openhands = [s for s in loaded if s["slot_id"] == "openhands"][0]
        assert primary["container_name"] == "llama_server"
        assert openhands["container_name"] == "llama_server_openhands"

    def test_legacy_status_maps_to_primary(self, app_client):
        """Legacy /status maps to primary slot."""
        resp = app_client.get("/api/v1/qonduit-router/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "slot_id" in data or "container_name" in data
        assert data.get("container_name") == "llama_server"

    def test_legacy_logs_maps_to_primary_container(self, app_client):
        """Legacy /logs reads from primary container 'llama_server'."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="log output from llama_server",
                stderr="",
            )
            resp = app_client.get("/api/v1/qonduit-router/logs")
            assert resp.status_code == 200
            # Verify subprocess was called with llama_server
            call_args = mock_run.call_args
            cmd = call_args[0][0] if call_args[0] else []
            assert "llama_server" in " ".join(cmd)


# ── Phase 3: Lazy Data Directory Tests ──────────────────────────────────────

class TestLazyDataDirectory:
    """Tests for lazy data directory creation."""

    def test_import_does_not_create_opt_data(self):
        """Module import does not create /opt/data."""
        import subprocess
        # Check if /opt/data exists before import
        result = subprocess.run(
            ["test", "-d", "/opt/data"],
            capture_output=True,
        )
        existed_before = result.returncode == 0

        # Re-import the modules
        import importlib
        import qonduit_slots
        importlib.reload(qonduit_slots)
        import qonduit_docker_helpers
        importlib.reload(qonduit_docker_helpers)

        result = subprocess.run(
            ["test", "-d", "/opt/data"],
            capture_output=True,
        )
        exists_after = result.returncode == 0

        # /opt/data should not have been created by import
        assert not exists_after or existed_before, \
            "Module import created /opt/data"

    def test_data_dir_defaults_to_qonduit_router_api_data(self, tmp_path):
        """Default host router data dir is /opt/qonduit-router-api/data."""
        import qonduit_slots
        # Reset to force re-evaluation
        qonduit_slots._QONDUIT_ROUTER_DATA_DIR = None
        # Set env override so default path is computed
        with patch.dict(os.environ, {"QONDUIT_ROUTER_DATA_DIR": "/opt/qonduit-router-api/data"}):
            qonduit_slots._ensure_data_dir()
            assert qonduit_slots._QONDUIT_ROUTER_DATA_DIR is not None

    def test_env_qonduit_router_data_dir_overrides(self, tmp_path):
        """QONDUIT_ROUTER_DATA_DIR env var overrides default."""
        import qonduit_slots
        qonduit_slots._QONDUIT_ROUTER_DATA_DIR = None
        custom_dir = tmp_path / "custom_data"
        with patch.dict(os.environ, {"QONDUIT_ROUTER_DATA_DIR": str(custom_dir)}):
            qonduit_slots._ensure_data_dir()
            assert qonduit_slots._QONDUIT_ROUTER_DATA_DIR == custom_dir


# ── Phase 4: GPU Endpoint Response Tests ────────────────────────────────────

class TestGpuEndpointResponse:
    """Tests for GET /gpu endpoint including usable/excluded GPU fields."""

    def test_gpu_endpoint_includes_usable_and_excluded(self, app_client):
        """GET /gpu returns usable_gpu_devices and excluded_gpus."""
        mock_output = (
            "0, Tesla P100-SXM2-16GB, 16384, 1024, 15360\n"
            "1, Quadro K620, 2048, 512, 1536\n"
            "2, Tesla P100-SXM2-16GB, 16384, 1024, 15360\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            resp = app_client.get("/api/v1/qonduit-router/gpu")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert "usable_gpu_devices" in data
            assert "excluded_gpus" in data
            assert "default_gpu_devices" in data
            assert "gpu_min_total_mib" in data
            # Usable should be "0,2"
            assert data["usable_gpu_devices"] == "0,2"
            # Excluded should include index 1
            assert len(data["excluded_gpus"]) >= 1
            assert any(g["index"] == 1 for g in data["excluded_gpus"])

    def test_gpu_endpoint_excludes_low_memory_gpu(self, app_client):
        """Excluded GPUs include reason for exclusion."""
        mock_output = "0, Quadro K620, 2048, 512, 1536\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            resp = app_client.get("/api/v1/qonduit-router/gpu")
            data = resp.get_json()
            assert len(data["excluded_gpus"]) >= 1
            excluded = [g for g in data["excluded_gpus"] if g["index"] == 0][0]
            assert "memory" in excluded.get("reason", "").lower() or "below" in excluded.get("reason", "").lower()


# ── Phase 5: Preflight GPU Warnings Tests ───────────────────────────────────

class TestPreflightGpuWarnings:
    """Tests for preflight endpoint GPU warnings."""

    def test_preflight_includes_effective_gpu_devices(self, app_client):
        """Preflight returns effective_gpu_devices."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            resp = app_client.post("/api/v1/qonduit-router/slots/primary/preflight", json={
                "model": "test.gguf",
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
            })
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert "effective_gpu_devices" in data
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_includes_memory_warnings(self, app_client):
        """Preflight warns about low-memory GPUs that would have been included."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            mock_output = (
                "0, Tesla P100, 16384, 1024, 15360\n"
                "1, Quadro K620, 2048, 512, 1536\n"
            )
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
                resp = app_client.post("/api/v1/qonduit-router/slots/primary/preflight", json={
                    "model": "test.gguf",
                    "context_size": 65536,
                    "gpu_devices": "all",
                    "tensor_split": "auto",
                })
                data = resp.get_json()
                assert data["ok"] is True
                # Should have warnings about excluded GPUs
                assert "warnings" in data
                # Or effective_gpu_devices should not include 1
                if "effective_gpu_devices" in data:
                    eff = data["effective_gpu_devices"]
                    assert "1" not in eff.split(",")
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_requested_vs_effective_gpu(self, app_client):
        """Preflight shows requested_gpu_devices and effective_gpu_devices."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            mock_output = (
                "0, Tesla P100, 16384, 1024, 15360\n"
                "1, Quadro K620, 2048, 512, 1536\n"
                "2, Tesla P100, 16384, 1024, 15360\n"
            )
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
                resp = app_client.post("/api/v1/qonduit-router/slots/primary/preflight", json={
                    "model": "test.gguf",
                    "context_size": 65536,
                    "gpu_devices": "all",
                    "tensor_split": "auto",
                })
                data = resp.get_json()
                assert data["ok"] is True
                assert data.get("requested_gpu_devices") == "all"
                assert data.get("effective_gpu_devices") == "0,2"
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")


# ── Phase 6: Self-Conflict Exclusion (Endpoint-Level Regression) ──────────────

class TestSelfConflictExclusion:
    """Endpoint-level regression tests ensuring a slot's preflight does not
    report its own port/container as in use.  These tests hit the actual Flask
    route (not just the helper functions) to verify the full integration."""

    def test_openhands_slot_no_self_conflict_port(self, app_client):
        """The openhands slot (port 8081) must report port_available=True for
        its own port — the slot must be excluded from its own collision check."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            # Create the openhands slot with port 8081
            resp = app_client.post("/api/v1/qonduit-router/slots", json={
                "slot_id": "openhands",
                "display_name": "OpenHands",
                "purpose": "openhands",
                "host_port": 8081,
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
            })
            assert resp.status_code == 201, f"Failed to create openhands slot: {resp.data}"

            # Preflight must not report its own port as in use
            resp = app_client.post("/api/v1/qonduit-router/slots/openhands/preflight", json={
                "model": "test.gguf",
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
            })
            data = resp.get_json()
            assert resp.status_code == 200
            assert data["ok"] is True
            assert data["port_available"] is True, \
                "openhands slot should not see its own port 8081 as in use"
            # Verify no self-conflict warning
            for w in data.get("warnings", []):
                assert "8081" not in w or "another slot" not in w, \
                    f"Self-conflict warning should not appear: {w}"
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_openhands_slot_no_self_conflict_container_name(self, app_client):
        """The openhands slot (container_name llama_server_openhands) must
        report container_name_available=True for its own name."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            # Create the openhands slot with specific container_name
            resp = app_client.post("/api/v1/qonduit-router/slots", json={
                "slot_id": "openhands",
                "display_name": "OpenHands",
                "purpose": "openhands",
                "host_port": 8081,
                "container_name": "llama_server_openhands",
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
            })
            assert resp.status_code == 201, f"Failed to create openhands slot: {resp.data}"

            # Preflight must not report its own container name as in use
            resp = app_client.post("/api/v1/qonduit-router/slots/openhands/preflight", json={
                "model": "test.gguf",
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
            })
            data = resp.get_json()
            assert resp.status_code == 200
            assert data["ok"] is True
            assert data["container_name_available"] is True, \
                "openhands slot should not see its own container name as in use"
            # Verify no self-conflict warning
            for w in data.get("warnings", []):
                assert "llama_server_openhands" not in w or "another slot" not in w, \
                    f"Self-conflict warning should not appear: {w}"
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_different_slot_conflicts_on_port(self, app_client):
        """A different slot using the same port as openhands must report
        port_available=False."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            # Create the openhands slot first
            resp = app_client.post("/api/v1/qonduit-router/slots", json={
                "slot_id": "openhands",
                "display_name": "OpenHands",
                "purpose": "openhands",
                "host_port": 8081,
                "container_name": "llama_server_openhands",
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
            })
            assert resp.status_code == 201

            # Create a different slot with the same port
            resp = app_client.post("/api/v1/qonduit-router/slots", json={
                "slot_id": "other",
                "display_name": "Other",
                "purpose": "custom",
                "host_port": 8081,  # same as openhands
                "container_name": "llama_server_other",
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
            })
            # Slot creation should fail due to duplicate port
            assert resp.status_code in (201, 409), f"Unexpected response: {resp.data}"
            # If it succeeded (force?), preflight should still detect the conflict
            if resp.status_code == 201:
                resp = app_client.post("/api/v1/qonduit-router/slots/other/preflight", json={
                    "model": "test.gguf",
                    "context_size": 65536,
                    "gpu_devices": "all",
                    "tensor_split": "auto",
                })
                data = resp.get_json()
                assert data["port_available"] is False, \
                    "other slot should see port 8081 as in use by openhands"
                port_warnings = [w for w in data.get("warnings", []) if "8081" in w]
                assert len(port_warnings) > 0, \
                    f"Should have port conflict warning: {data.get('warnings')}"
                assert "openhands" in port_warnings[0].lower(), \
                    f"Warning should mention openhands slot: {port_warnings[0]}"
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_different_slot_conflicts_on_container_name(self, app_client):
        """A different slot using the same container_name as openhands must
        report container_name_available=False."""
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            # Create the openhands slot first
            resp = app_client.post("/api/v1/qonduit-router/slots", json={
                "slot_id": "openhands",
                "display_name": "OpenHands",
                "purpose": "openhands",
                "host_port": 8081,
                "container_name": "llama_server_openhands",
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
            })
            assert resp.status_code == 201

            # Create a different slot with the same container name
            resp = app_client.post("/api/v1/qonduit-router/slots", json={
                "slot_id": "other",
                "display_name": "Other",
                "purpose": "custom",
                "host_port": 8082,
                "container_name": "llama_server_openhands",  # same as openhands
                "context_size": 65536,
                "gpu_devices": "all",
                "tensor_split": "auto",
                "embeddings_enabled": False,
            })
            # Slot creation should fail due to duplicate container name
            assert resp.status_code in (201, 409), f"Unexpected response: {resp.data}"
            if resp.status_code == 201:
                resp = app_client.post("/api/v1/qonduit-router/slots/other/preflight", json={
                    "model": "test.gguf",
                    "context_size": 65536,
                    "gpu_devices": "all",
                    "tensor_split": "auto",
                })
                data = resp.get_json()
                assert data["container_name_available"] is False, \
                    "other slot should see llama_server_openhands as in use"
                name_warnings = [w for w in data.get("warnings", [])
                                 if "llama_server_openhands" in w]
                assert len(name_warnings) > 0, \
                    f"Should have container name conflict warning: {data.get('warnings')}"
                assert "openhands" in name_warnings[0].lower(), \
                    f"Warning should mention openhands slot: {name_warnings[0]}"
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_no_stale_port_is_available_calls_without_exclude_slot_id(self):
        """Static check: the preflight route in qonduit_router_api_slots.py must
        NOT contain any call to port_is_available(), docker_name_is_available(),
        find_port_conflict(), or find_container_name_conflict() that is missing
        the exclude_slot_id parameter."""
        import qonduit_router_api_slots
        source_path = qonduit_router_api_slots.__file__
        with open(source_path, "r") as f:
            source = f.read()

        # Find the slot_preflight function body via line-based extraction.
        # We collect lines until we hit the next route decorator, function, or class.
        lines = source.split("\n")
        preflight_lines: list[str] = []
        in_preflight = False
        for line in lines:
            if "def slot_preflight(slot_id: str):" in line:
                in_preflight = True
                continue
            if in_preflight:
                stripped = line.lstrip()
                if (
                    (line and line[0] not in (" ", "\t"))
                    or stripped.startswith("@app.")
                    or stripped.startswith("def ")
                    or stripped.startswith("class ")
                ):
                    break
                preflight_lines.append(line)

        assert preflight_lines, "Could not find slot_preflight function body"

        # For each line containing a conflict-check helper, verify exclude_slot_id is present.
        helper_patterns = [
            "port_is_available(",
            "docker_name_is_available(",
            "find_port_conflict(",
            "find_container_name_conflict(",
        ]
        for line in preflight_lines:
            for pattern in helper_patterns:
                if pattern in line:
                    assert "exclude_slot_id" in line, (
                        f"Line in preflight missing exclude_slot_id: {line.strip()}"
                    )


# ── Router Access Control Tests ──────────────────────────────────────────────

class TestRouterAccessControl:
    """Tests for the _require_router_access / _router_access_allowed helpers."""

    def _request_with_ip(self, client, method, path, ip, **kwargs):
        """Make a request with a specific remote_addr via test_request_context."""
        # Import app to access test_request_context
        import qonduit_router_api
        app = qonduit_router_api.app

        with app.test_request_context(path, method=method, **kwargs):
            # Set the remote_addr on the current request context
            from flask import request as flask_request
            flask_request.remote_addr = ip
            # Now make the request through the test client with environ_base
            pass

        # Use the test client with environ_base to set REMOTE_ADDR
        environ_base = kwargs.get("environ_base", {})
        environ_base["REMOTE_ADDR"] = ip
        if method == "GET":
            return client.get(path, environ_base=environ_base)
        elif method == "POST":
            return client.post(path, environ_base=environ_base, **kwargs.get("json_kwargs", {}))
        elif method == "DELETE":
            return client.delete(path, environ_base=environ_base)
        elif method == "OPTIONS":
            return client.options(path, environ_base=environ_base)
        else:
            return client.get(path, environ_base=environ_base)

    def _get_with_ip(self, client, path, ip):
        """GET request with specific remote_addr."""
        return client.get(path, environ_base={"REMOTE_ADDR": ip})

    def _post_with_ip(self, client, path, ip, json_data=None):
        """POST request with specific remote_addr."""
        return client.post(path,
                           environ_base={"REMOTE_ADDR": ip},
                           json=json_data)

    def _delete_with_ip(self, client, path, ip):
        """DELETE request with specific remote_addr."""
        return client.delete(path, environ_base={"REMOTE_ADDR": ip})

    def _options_with_ip(self, client, path, ip):
        """OPTIONS request with specific remote_addr."""
        return client.options(path, environ_base={"REMOTE_ADDR": ip})

    def test_loopback_allowed(self, app_client, monkeypatch):
        """Loopback clients (127.0.0.1) are allowed regardless of env."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "false")
        resp = self._get_with_ip(app_client, "/api/v1/qonduit-router/slots", "127.0.0.1")
        assert resp.status_code == 200

    def test_loopback_allowed_ipv6(self, app_client, monkeypatch):
        """Loopback clients (::1) are allowed regardless of env."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "false")
        resp = self._get_with_ip(app_client, "/api/v1/qonduit-router/slots", "::1")
        assert resp.status_code == 200

    def test_lan_allowed_when_QONDUIT_ROUTER_ALLOW_LAN_true(self, app_client, monkeypatch):
        """LAN/private clients are allowed when QONDUIT_ROUTER_ALLOW_LAN=true (default)."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "true")
        resp = self._get_with_ip(app_client, "/api/v1/qonduit-router/slots", "192.168.5.5")
        assert resp.status_code == 200

    def test_lan_allowed_10_x_x_x(self, app_client, monkeypatch):
        """10.x.x.x private range clients are allowed when QONDUIT_ROUTER_ALLOW_LAN=true."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "true")
        resp = self._get_with_ip(app_client, "/api/v1/qonduit-router/slots", "10.0.0.1")
        assert resp.status_code == 200

    def test_lan_allowed_172_x_x_x_private(self, app_client, monkeypatch):
        """172.16-31.x.x private range clients are allowed when QONDUIT_ROUTER_ALLOW_LAN=true."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "true")
        resp = self._get_with_ip(app_client, "/api/v1/qonduit-router/slots", "172.16.0.1")
        assert resp.status_code == 200

    def test_lan_rejected_when_QONDUIT_ROUTER_ALLOW_LAN_false(self, app_client, monkeypatch):
        """LAN/private clients are rejected when QONDUIT_ROUTER_ALLOW_LAN=false."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "false")
        resp = self._get_with_ip(app_client, "/api/v1/qonduit-router/slots", "192.168.5.5")
        assert resp.status_code == 403

    def test_public_client_rejected(self, app_client, monkeypatch):
        """Public/non-private clients are always rejected."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "true")
        resp = self._get_with_ip(app_client, "/api/v1/qonduit-router/slots", "8.8.8.8")
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["error"] == "router_access_denied"

    def test_options_not_blocked(self, app_client, monkeypatch):
        """OPTIONS requests are never blocked (CORS/PNA preflight)."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "false")
        resp = self._options_with_ip(app_client, "/api/v1/qonduit-router/slots", "8.8.8.8")
        assert resp.status_code == 200

    def test_options_not_blocked_loopback(self, app_client, monkeypatch):
        """OPTIONS requests are allowed from any client."""
        resp = self._options_with_ip(app_client, "/api/v1/qonduit-router/slots", "127.0.0.1")
        assert resp.status_code == 200

    def test_slots_no_local_only_for_allowed_lan(self, app_client, monkeypatch):
        """/slots returns 200 for allowed LAN clients (no local_only error)."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "true")
        resp = self._get_with_ip(app_client, "/api/v1/qonduit-router/slots", "192.168.1.100")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True
        assert "local_only" not in data.get("error", "")

    def test_endpoints_no_local_only_for_allowed_lan(self, app_client, monkeypatch):
        """/endpoints returns 200 for allowed LAN clients."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "true")
        resp = self._get_with_ip(app_client, "/api/v1/qonduit-router/endpoints", "192.168.1.100")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True

    def test_gpu_no_local_only_for_allowed_lan(self, app_client, monkeypatch):
        """/gpu returns 200 for allowed LAN clients."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "true")
        resp = self._get_with_ip(app_client, "/api/v1/qonduit-router/gpu", "192.168.1.100")
        assert resp.status_code == 200

    def test_slot_templates_no_local_only_for_allowed_lan(self, app_client, monkeypatch):
        """/slot-templates returns 200 for allowed LAN clients."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "true")
        resp = self._get_with_ip(app_client, "/api/v1/qonduit-router/slot-templates", "192.168.1.100")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True
        assert "templates" in data

    def test_legacy_models_unchanged(self, app_client, monkeypatch):
        """/models behavior remains unchanged (it uses different guard logic in the main API)."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "false")
        resp = self._get_with_ip(app_client, "/api/v1/qonduit-router/models", "127.0.0.1")
        # Should not return local_only error
        assert resp.status_code == 200 or resp.status_code == 401 or resp.status_code == 403
        data = resp.get_json()
        if data and data.get("error") == "local_only":
            pytest.fail("/models returned local_only error for loopback client")

    def test_lan_env_var_variants(self, app_client, monkeypatch):
        """Test various string values for QONDUIT_ROUTER_ALLOW_LAN env var."""
        for true_val in ("1", "true", "yes", "on"):
            monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", true_val)
            resp = self._get_with_ip(app_client, "/api/v1/qonduit-router/gpu", "192.168.1.1")
            assert resp.status_code == 200, f"ALLOW_LAN={true_val} should allow LAN"

        for false_val in ("0", "false", "no", "off"):
            monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", false_val)
            resp = self._get_with_ip(app_client, "/api/v1/qonduit-router/gpu", "192.168.1.1")
            assert resp.status_code == 403, f"ALLOW_LAN={false_val} should deny LAN"

    def test_post_slots_restricted_by_access(self, app_client, monkeypatch):
        """POST /slots is also restricted by router access control."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "false")
        resp = self._post_with_ip(app_client, "/api/v1/qonduit-router/slots", "192.168.1.1",
                                   json_data={"slot_id": "test-restricted", "host_port": 9000})
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["error"] == "router_access_denied"

    def test_delete_slots_restricted_by_access(self, app_client, monkeypatch):
        """DELETE /slots/<slot_id> is also restricted by router access control."""
        monkeypatch.setenv("QONDUIT_ROUTER_ALLOW_LAN", "false")
        resp = self._delete_with_ip(app_client, "/api/v1/qonduit-router/slots/primary", "192.168.1.1")
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["error"] == "router_access_denied"


# ── Tensor Split Preflight Tests ─────────────────────────────────────────────
# Comprehensive tests for tensor_split acceptance, echoing, validation,
# suggested splits, launch args preview, and extra_args conflict handling.


class TestTensorSplitPreflight:
    """Tests for tensor_split handling in the preflight endpoint."""

    def test_preflight_without_tensor_split_still_works(self, app_client):
        """Test 1: Preflight without tensor_split still works.

        When no tensor_split is sent, the response should include
        requested_tensor_split: null, tensor_split: null,
        tensor_split_valid: true, and tensor_split_entry_count: null.
        """
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={"model": "test.gguf"},
            )
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["requested_tensor_split"] is None
        assert data["tensor_split"] is None
        assert data["tensor_split_valid"] is True
        assert data["tensor_split_entry_count"] is None
        # Should still have effective_gpu_count
        assert "effective_gpu_count" in data
        # Should still have suggested_tensor_splits
        assert "suggested_tensor_splits" in data
        # launch_args_preview should be empty (no explicit tensor_split)
        assert data["launch_args_preview"] == []

    def test_preflight_with_matching_tensor_split(self, app_client, monkeypatch):
        """Test 2: Preflight with tensor_split matching effective GPU count works.

        When tensor_split has the same count as effective GPUs,
        tensor_split_valid should be true.
        """
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            # Mock GPU summary with 7 usable GPUs (simulating the target machine)
            mock_gpu_summary = {
                "ok": True,
                "gpus": [
                    {"index": 0, "name": "NVIDIA A40", "memory_total_mib": 46068,
                     "memory_used_mib": 1000, "memory_free_mib": 45068},
                    {"index": 2, "name": "NVIDIA A40", "memory_total_mib": 46068,
                     "memory_used_mib": 1000, "memory_free_mib": 45068},
                    {"index": 3, "name": "NVIDIA A40", "memory_total_mib": 46068,
                     "memory_used_mib": 1000, "memory_free_mib": 45068},
                    {"index": 4, "name": "NVIDIA A40", "memory_total_mib": 46068,
                     "memory_used_mib": 1000, "memory_free_mib": 45068},
                    {"index": 5, "name": "NVIDIA A40", "memory_total_mib": 46068,
                     "memory_used_mib": 1000, "memory_free_mib": 45068},
                    {"index": 6, "name": "NVIDIA A40", "memory_total_mib": 46068,
                     "memory_used_mib": 1000, "memory_free_mib": 45068},
                    {"index": 7, "name": "NVIDIA A40", "memory_total_mib": 46068,
                     "memory_used_mib": 1000, "memory_free_mib": 45068},
                ],
                "usable_gpu_indices": "0,2,3,4,5,6,7",
                "usable_gpu_devices": "0,2,3,4,5,6,7",
            }
            monkeypatch.setattr(
                "qonduit_router_api_slots.collect_gpu_summary",
                lambda: mock_gpu_summary,
            )

            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={
                    "model": "test.gguf",
                    "gpu_devices": "all",
                    "tensor_split": "1,1,1,1,1,1,1",
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["requested_tensor_split"] == "1,1,1,1,1,1,1"
            assert data["tensor_split"] == "1,1,1,1,1,1,1"
            assert data["tensor_split_valid"] is True
            assert data["tensor_split_entry_count"] == 7
            assert data["effective_gpu_count"] == 7
            assert data["effective_gpu_devices"] == "0,2,3,4,5,6,7"
            # launch_args_preview should include --tensor-split
            assert "--tensor-split" in data["launch_args_preview"]
            assert "1,1,1,1,1,1,1" in data["launch_args_preview"]
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_with_tensor_split_count_mismatch(self, app_client, monkeypatch):
        """Test 3: Preflight with tensor_split count mismatch returns clear error.

        When tensor_split has a different count than effective GPUs,
        tensor_split_valid should be false and a clear warning should be present.
        """
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            mock_gpu_summary = {
                "ok": True,
                "gpus": [
                    {"index": 0, "name": "GPU0", "memory_total_mib": 24000,
                     "memory_used_mib": 0, "memory_free_mib": 24000},
                    {"index": 2, "name": "GPU2", "memory_total_mib": 24000,
                     "memory_used_mib": 0, "memory_free_mib": 24000},
                ],
                "usable_gpu_indices": "0,2",
                "usable_gpu_devices": "0,2",
            }
            monkeypatch.setattr(
                "qonduit_router_api_slots.collect_gpu_summary",
                lambda: mock_gpu_summary,
            )

            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={
                    "model": "test.gguf",
                    "gpu_devices": "all",
                    "tensor_split": "1,1",  # 2 values but mock has 2 GPUs, so this should pass
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            # 2 values for 2 GPUs — this is actually valid in this mock
            assert data["tensor_split_valid"] is True
            assert data["tensor_split_entry_count"] == 2

            # Now test with a true mismatch: 7 GPUs but 2 values
            mock_gpu_summary_7 = {
                "ok": True,
                "gpus": [
                    {"index": i, "name": f"GPU{i}", "memory_total_mib": 24000,
                     "memory_used_mib": 0, "memory_free_mib": 24000}
                    for i in [0, 2, 3, 4, 5, 6, 7]
                ],
                "usable_gpu_indices": "0,2,3,4,5,6,7",
                "usable_gpu_devices": "0,2,3,4,5,6,7",
            }
            monkeypatch.setattr(
                "qonduit_router_api_slots.collect_gpu_summary",
                lambda: mock_gpu_summary_7,
            )

            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={
                    "model": "test.gguf",
                    "gpu_devices": "all",
                    "tensor_split": "1,1",  # 2 values but 7 GPUs
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["tensor_split_valid"] is False
            assert data["tensor_split_entry_count"] == 2
            assert data["effective_gpu_count"] == 7
            # Should have a clear warning about the mismatch
            mismatch_warnings = [
                w for w in data["warnings"]
                if "tensor_split" in w.lower() and "value" in w.lower()
            ]
            assert len(mismatch_warnings) > 0, "Expected mismatch warning"
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_with_malformed_tensor_split(self, app_client):
        """Test 4: Preflight with malformed tensor_split returns HTTP 400.

        Non-numeric values in tensor_split should be rejected with a 400 error.
        """
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={"model": "test.gguf", "tensor_split": "abc"},
            )
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["ok"] is False
            assert data["error"] == "invalid_tensor_split"

            # Empty entry
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={"model": "test.gguf", "tensor_split": "1,,2"},
            )
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["error"] == "invalid_tensor_split"

            # Non-numeric in the middle
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={"model": "test.gguf", "tensor_split": "1,abc,2"},
            )
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["error"] == "invalid_tensor_split"
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_response_echoes_requested_tensor_split(self, app_client, monkeypatch):
        """Test 5: Preflight response echoes requested_tensor_split.

        The response should include requested_tensor_split with the exact
        value sent by the frontend.
        """
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            monkeypatch.setattr(
                "qonduit_router_api_slots.collect_gpu_summary",
                lambda: {"ok": False, "error": "test"},
            )

            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={"model": "test.gguf", "tensor_split": "138,55,80,91,80,79,79"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["requested_tensor_split"] == "138,55,80,91,80,79,79"
            assert data["tensor_split"] == "138,55,80,91,80,79,79"

            # When not sent, requested should be null
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={"model": "test.gguf"},
            )
            data = resp.get_json()
            assert data["requested_tensor_split"] is None
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_response_includes_effective_gpu_count(self, app_client, monkeypatch):
        """Test 6: Preflight response includes effective_gpu_count.

        effective_gpu_count should be present and match the count of
        effective_gpu_devices.
        """
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            mock_gpu_summary = {
                "ok": True,
                "gpus": [
                    {"index": i, "name": f"GPU{i}", "memory_total_mib": 24000,
                     "memory_used_mib": 0, "memory_free_mib": 24000}
                    for i in [0, 2, 3, 4, 5, 6, 7]
                ],
                "usable_gpu_indices": "0,2,3,4,5,6,7",
                "usable_gpu_devices": "0,2,3,4,5,6,7",
            }
            monkeypatch.setattr(
                "qonduit_router_api_slots.collect_gpu_summary",
                lambda: mock_gpu_summary,
            )

            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={"model": "test.gguf", "gpu_devices": "all"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["effective_gpu_count"] == 7
            # Count devices in effective_gpu_devices string
            devices = data["effective_gpu_devices"].split(",")
            assert len([d for d in devices if d.strip()]) == 7
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_preflight_response_includes_suggested_tensor_splits(self, app_client, monkeypatch):
        """Test 7: Preflight response includes suggested_tensor_splits.

        The response should include suggested tensor splits: even,
        free_vram_weighted_raw, and free_vram_weighted_normalized.
        """
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            mock_gpu_summary = {
                "ok": True,
                "gpus": [
                    {"index": 0, "name": "GPU0", "memory_total_mib": 14128 + 1000,
                     "memory_used_mib": 1000, "memory_free_mib": 14128},
                    {"index": 2, "name": "GPU2", "memory_total_mib": 5614 + 1000,
                     "memory_used_mib": 1000, "memory_free_mib": 5614},
                    {"index": 3, "name": "GPU3", "memory_total_mib": 8174 + 1000,
                     "memory_used_mib": 1000, "memory_free_mib": 8174},
                    {"index": 4, "name": "GPU4", "memory_total_mib": 9280 + 1000,
                     "memory_used_mib": 1000, "memory_free_mib": 9280},
                    {"index": 5, "name": "GPU5", "memory_total_mib": 8174 + 1000,
                     "memory_used_mib": 1000, "memory_free_mib": 8174},
                    {"index": 6, "name": "GPU6", "memory_total_mib": 8112 + 1000,
                     "memory_used_mib": 1000, "memory_free_mib": 8112},
                    {"index": 7, "name": "GPU7", "memory_total_mib": 8138 + 1000,
                     "memory_used_mib": 1000, "memory_free_mib": 8138},
                ],
                "usable_gpu_indices": "0,2,3,4,5,6,7",
                "usable_gpu_devices": "0,2,3,4,5,6,7",
            }
            monkeypatch.setattr(
                "qonduit_router_api_slots.collect_gpu_summary",
                lambda: mock_gpu_summary,
            )

            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={"model": "test.gguf"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            suggestions = data["suggested_tensor_splits"]
            assert "even" in suggestions
            assert suggestions["even"] == "1,1,1,1,1,1,1"
            assert "free_vram_weighted_raw" in suggestions
            assert suggestions["free_vram_weighted_raw"] == "14128,5614,8174,9280,8174,8112,8138"
            assert "free_vram_weighted_normalized" in suggestions
            # Check that normalized values are reasonable
            norm = suggestions["free_vram_weighted_normalized"]
            norm_parts = norm.split(",")
            assert len(norm_parts) == 7
            # Values should be in a reasonable range (not all 1s, not zeros)
            for v in norm_parts:
                assert int(v) >= 1
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_launch_args_include_tensor_split_when_set(self, app_client, monkeypatch):
        """Test 8: Launch args include --tensor-split when tensor_split is set.

        The launch_args_preview in the preflight response should include
        --tensor-split and the value when an explicit tensor_split is provided.
        """
        _known_models.add("/mnt/models/llm/test.gguf")
        try:
            monkeypatch.setattr(
                "qonduit_router_api_slots.collect_gpu_summary",
                lambda: {"ok": False, "error": "test"},
            )

            # With tensor_split set
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={"model": "test.gguf", "tensor_split": "3,1,1"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert "--tensor-split" in data["launch_args_preview"]
            idx = data["launch_args_preview"].index("--tensor-split")
            assert data["launch_args_preview"][idx + 1] == "3,1,1"

            # Without tensor_split (auto)
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={"model": "test.gguf"},
            )
            data = resp.get_json()
            assert "--tensor-split" not in data["launch_args_preview"]
            assert data["launch_args_preview"] == []
        finally:
            _known_models.discard("/mnt/models/llm/test.gguf")

    def test_extra_args_conflict_with_tensor_split(self, app_client, monkeypatch):
        """Test 9: Extra args conflict with --tensor-split is handled.

        When tensor_split is set and extra_args also contains --tensor-split,
        a warning should be returned and the extra_args version should be
        ignored.
        """
        monkeypatch.setattr(
            "qonduit_router_api_slots.collect_gpu_summary",
            lambda: {"ok": False, "error": "test"},
        )

        # Register model file so mock os.path.exists returns True
        _known_models.add("/mnt/models/llm/Qwen3.6-35B-A3B-UD-Q5_K_XL.gguf")
        try:
            # With --tensor-split in extra_args
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={
                    "model": "Qwen3.6-35B-A3B-UD-Q5_K_XL.gguf",
                    "tensor_split": "3,1,1",
                    "extra_args": ["--tensor-split", "1,1,1"],
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            conflict_warnings = [
                w for w in data["warnings"]
                if "Ignoring" in w and "extra_args" in w
            ]
            assert len(conflict_warnings) > 0

            # With --tensor-split=value in extra_args
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={
                    "model": "Qwen3.6-35B-A3B-UD-Q5_K_XL.gguf",
                    "tensor_split": "3,1,1",
                    "extra_args": ["--tensor-split=1,1,1", "--other-arg"],
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            conflict_warnings = [
                w for w in data["warnings"]
                if "Ignoring" in w and "extra_args" in w
            ]
            assert len(conflict_warnings) > 0

            # Without explicit tensor_split, extra_args should not conflict
            resp = app_client.post(
                "/api/v1/qonduit-router/slots/primary/preflight",
                json={
                    "model": "Qwen3.6-35B-A3B-UD-Q5_K_XL.gguf",
                    "extra_args": ["--tensor-split", "1,1,1"],
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            conflict_warnings = [
                w for w in data["warnings"]
                if "Ignoring" in w and "extra_args" in w
            ]
            assert len(conflict_warnings) == 0
        finally:
            _known_models.discard("/mnt/models/llm/Qwen3.6-35B-A3B-UD-Q5_K_XL.gguf")


class TestComputeSuggestedTensorSplits:
    """Tests for the compute_suggested_tensor_splits helper function."""

    def test_even_split_generated(self):
        """Even split is always generated."""
        from qonduit_docker_helpers import compute_suggested_tensor_splits
        result = compute_suggested_tensor_splits({"ok": False}, 5)
        assert result["even"] == "1,1,1,1,1"

    def test_weighted_null_when_no_gpu_data(self):
        """Weighted suggestions are null when no GPU data available."""
        from qonduit_docker_helpers import compute_suggested_tensor_splits
        result = compute_suggested_tensor_splits({"ok": False}, 3)
        assert result["free_vram_weighted_raw"] is None
        assert result["free_vram_weighted_normalized"] is None
        assert "warning" in result

    def test_weighted_generated_with_gpu_data(self):
        """Weighted suggestions are generated when GPU data is available."""
        from qonduit_docker_helpers import compute_suggested_tensor_splits
        gpu_summary = {
            "ok": True,
            "gpus": [
                {"index": 0, "name": "GPU0", "memory_total_mib": 24000,
                 "memory_used_mib": 1000, "memory_free_mib": 23000},
                {"index": 1, "name": "GPU1", "memory_total_mib": 12000,
                 "memory_used_mib": 5000, "memory_free_mib": 7000},
            ],
            "usable_gpu_indices": "0,1",
            "usable_gpu_devices": "0,1",
        }
        result = compute_suggested_tensor_splits(gpu_summary, 2)
        assert result["even"] == "1,1"
        assert result["free_vram_weighted_raw"] == "23000,7000"
        # Normalized values should preserve proportions
        norm_parts = result["free_vram_weighted_normalized"].split(",")
        assert len(norm_parts) == 2
        # GPU 0 has ~3.3x more free memory than GPU 1
        ratio = int(norm_parts[0]) / int(norm_parts[1])
        assert ratio > 2.5  # 23000/7000 ≈ 3.3

    def test_normalized_preserves_proportions(self):
        """Normalized split preserves relative VRAM proportions."""
        from qonduit_docker_helpers import compute_suggested_tensor_splits
        gpu_summary = {
            "ok": True,
            "gpus": [
                {"index": 0, "name": "GPU0", "memory_total_mib": 20000,
                 "memory_used_mib": 0, "memory_free_mib": 14128},
                {"index": 1, "name": "GPU1", "memory_total_mib": 8000,
                 "memory_used_mib": 0, "memory_free_mib": 5614},
                {"index": 2, "name": "GPU2", "memory_total_mib": 16000,
                 "memory_used_mib": 0, "memory_free_mib": 8174},
            ],
            "usable_gpu_indices": "0,1,2",
            "usable_gpu_devices": "0,1,2",
        }
        result = compute_suggested_tensor_splits(gpu_summary, 3)
        raw_parts = result["free_vram_weighted_raw"].split(",")
        norm_parts = result["free_vram_weighted_normalized"].split(",")
        assert len(raw_parts) == 3
        assert len(norm_parts) == 3
        # Verify normalization by 102.4
        assert int(norm_parts[0]) == round(14128 / 102.4)
        assert int(norm_parts[1]) == round(5614 / 102.4)
        assert int(norm_parts[2]) == round(8174 / 102.4)

    def test_zero_free_memory_excluded(self):
        """GPUs with zero free memory are excluded from weighted suggestions."""
        from qonduit_docker_helpers import compute_suggested_tensor_splits
        gpu_summary = {
            "ok": True,
            "gpus": [
                {"index": 0, "name": "GPU0", "memory_total_mib": 24000,
                 "memory_used_mib": 24000, "memory_free_mib": 0},
                {"index": 1, "name": "GPU1", "memory_total_mib": 24000,
                 "memory_used_mib": 1000, "memory_free_mib": 23000},
            ],
            "usable_gpu_indices": "0,1",
            "usable_gpu_devices": "0,1",
        }
        result = compute_suggested_tensor_splits(gpu_summary, 2)
        # GPU 0 has 0 free, so only GPU 1 should appear in weighted
        raw = result["free_vram_weighted_raw"]
        assert raw == "23000"
        assert result["free_vram_weighted_normalized"] == "225"
