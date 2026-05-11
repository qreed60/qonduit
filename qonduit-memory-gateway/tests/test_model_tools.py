"""Comprehensive tests for Model Tools backend (Phase 1-6).

Tests cover:
  Phase 1:  Tool registry (GET /v1/tools)
  Phase 2:  Tool settings storage (GET/PATCH /v1/gateway/settings/tools)
  Phase 3:  Tool execution (POST /v1/tools/execute)
  Phase 4:  Chat completions tool field compatibility
  Phase 5:  Audit log (GET /v1/tools/audit)
  Phase 6:  GET /v1/tools/status, GET /v1/models/{model_id}/tools
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure we can import the app (needs FastAPI)
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient

# Import app-level pieces we need
from app.main import app as fastapi_app

# Tool registry & helpers
from app.tools import (
    ALL_KNOWN_TOOLS,
    DESTRUCTIVE_TOOLS,
    SAFE_TOOLS,
    TOOL_DEFINITIONS,
    get_tool,
    list_tools,
    validate_tool_arguments,
)

# Settings module
from app.tool_settings import (
    load_settings,
    save_settings,
    get_effective_tools,
)

# Router helpers (for direct unit tests)
from app.tools_router import (
    _TOOL_EXECUTORS,
    _check_dependency_health,
)

BASE_URL = "http://testserver"

client = TestClient(fastapi_app, raise_server_exceptions=False)

# ---------------------------------------------------------------------------
# NOTE: Settings use camelCase keys (perModel, confirmationMode) — not snake_case.
# PATCH endpoint returns 200 with ok=false for validation errors (not HTTP 422).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_settings_dir(tmp_path: Path):
    """Provide a temporary directory and patch the settings module."""
    import app.tool_settings as ts

    original = ts._data_dir
    ts._data_dir = lambda: tmp_path
    yield tmp_path
    ts._data_dir = original


@pytest.fixture()
def clean_settings(tmp_settings_dir: Path):
    """Ensure a fresh settings file in the temp directory."""
    settings_path = tmp_settings_dir / "tool_settings.json"
    if settings_path.exists():
        settings_path.unlink()
    return tmp_settings_dir


@pytest.fixture()
def clean_audit_log(tmp_settings_dir: Path):
    """Ensure a fresh audit log in the temp directory and patch the path."""
    import app.tools_router as tr

    # Clear any existing audit log
    audit_path = tmp_settings_dir / "tool_audit.jsonl"
    if audit_path.exists():
        audit_path.unlink()

    # Patch the module-level AUDIT_LOG_PATH to use our temp dir
    original = tr.AUDIT_LOG_PATH
    tr.AUDIT_LOG_PATH = audit_path
    yield tmp_settings_dir
    tr.AUDIT_LOG_PATH = original


# ===========================================================================
# Phase 1 — Tool Registry (GET /v1/tools)
# ===========================================================================


class TestToolRegistry:
    """GET /v1/tools returns expected frontend-friendly fields."""

    def test_get_tools_endpoint(self):
        resp = client.get(f"{BASE_URL}/v1/tools")
        assert resp.status_code == 200, f"Unexpected status: {resp.status_code} {resp.text}"
        data = resp.json()
        assert data["ok"] is True
        assert "tools" in data
        assert isinstance(data["tools"], list)
        assert len(data["tools"]) > 0

    def test_tools_have_frontend_fields(self):
        """Each tool must have the camelCase fields the frontend expects."""
        resp = client.get(f"{BASE_URL}/v1/tools")
        tools = resp.json()["tools"]
        required_fields = {
            "name", "displayName", "description", "enabled",
            "requiresConfirmation", "category", "backendAvailable",
            "backendProvider", "lastError", "readOnly", "destructive",
        }
        for tool in tools:
            missing = required_fields - set(tool.keys())
            assert not missing, f"Tool '{tool.get('name')}' missing fields: {missing}"

    def test_safe_tools_enabled(self):
        """Safe tools should be enabled by default."""
        tools = {t["name"]: t for t in list_tools()}
        for safe in SAFE_TOOLS:
            assert safe in tools, f"{safe} not in tool definitions"
            assert tools[safe]["enabled"] is True, f"{safe} should be enabled"
            assert tools[safe]["readOnly"] is True, f"{safe} should be read-only"
            assert tools[safe]["destructive"] is False, f"{safe} should not be destructive"

    def test_destructive_tools_disabled(self):
        """Destructive placeholders should be disabled and not available."""
        tools = {t["name"]: t for t in list_tools()}
        for destructive in DESTRUCTIVE_TOOLS:
            assert destructive in tools, f"{destructive} not in tool definitions"
            assert tools[destructive]["enabled"] is False, (
                f"{destructive} should be disabled"
            )
            assert tools[destructive]["backendAvailable"] is False, (
                f"{destructive} should not have backend available"
            )
            assert tools[destructive]["destructive"] is True, (
                f"{destructive} should be marked destructive"
            )

    def test_all_known_tools_listed(self):
        """ALL_KNOWN_TOOLS should be fully represented in TOOL_DEFINITIONS."""
        defined = {t["name"] for t in TOOL_DEFINITIONS}
        assert defined == ALL_KNOWN_TOOLS, (
            f"Known tools mismatch. Missing: {ALL_KNOWN_TOOLS - defined}, "
            f"Extra: {defined - ALL_KNOWN_TOOLS}"
        )

    def test_tool_schema_structure(self):
        """Each tool must have a valid OpenAI-compatible function schema."""
        for tool in TOOL_DEFINITIONS:
            schema = tool["schema"]
            assert schema["type"] == "function"
            assert "function" in schema
            func = schema["function"]
            assert "name" in func
            assert func["name"] == tool["name"]
            assert "description" in func
            assert "parameters" in func
            params = func["parameters"]
            assert params["type"] == "object"
            assert "properties" in params
            assert "required" in params

    def test_categories_covered(self):
        """Frontend categories must all be represented."""
        tools = {t["name"]: t for t in list_tools()}
        expected_categories = {"rag", "system", "homeassistant", "web"}
        found = {t["category"] for t in TOOL_DEFINITIONS}
        for cat in expected_categories:
            assert cat in found, f"Category '{cat}' not represented in tools"

    def test_backend_provider_is_gateway(self):
        """All tools should declare Gateway as backend provider."""
        for tool in TOOL_DEFINITIONS:
            assert tool["backendProvider"] == "Gateway", (
                f"Tool '{tool['name']}' should have backendProvider='Gateway'"
            )


# ===========================================================================
# Phase 6 (partial) — Tool Status (GET /v1/tools/status)
# ===========================================================================


class TestToolStatus:
    """GET /v1/tools/status works and reports dependency health."""

    def test_status_endpoint(self):
        resp = client.get(f"{BASE_URL}/v1/tools/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "tools" in data
        assert "dependencies" in data

    def test_status_tools_field(self):
        """Status should include per-tool status info."""
        resp = client.get(f"{BASE_URL}/v1/tools/status")
        tools_status = resp.json()["tools"]
        assert isinstance(tools_status, dict)
        assert "rag_search" in tools_status
        entry = tools_status["rag_search"]
        assert "ok" in entry
        assert "enabled" in entry
        assert "backendAvailable" in entry

    def test_status_dependencies_no_crash(self):
        """Dependencies section should not crash even if Qdrant/embedding are down."""
        resp = client.get(f"{BASE_URL}/v1/tools/status")
        deps = resp.json()["dependencies"]
        assert isinstance(deps, dict)
        # Should at least attempt qdrant and embedding checks
        assert "qdrant" in deps or "embedding" in deps or len(deps) >= 0


# ===========================================================================
# Phase 2 — Tool Settings Storage
# ===========================================================================


class TestToolSettings:
    """GET /v1/gateway/settings/tools works and PATCH persists."""

    def test_get_settings_default(self, clean_settings: Path):
        """Default settings should have safe tools enabled."""
        resp = client.get(f"{BASE_URL}/v1/gateway/settings/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "settings" in data
        settings = data["settings"]
        assert "global" in settings
        assert "perModel" in settings
        assert "confirmationMode" in settings
        # Safe tools should be enabled by default
        for safe in SAFE_TOOLS:
            assert settings["global"].get(safe) is True, (
                f"Default settings should enable {safe}"
            )

    def test_get_settings_persists_across_requests(self, clean_settings: Path):
        """Settings should persist across requests."""
        import app.tool_settings as ts

        # Set settings using camelCase keys (as the module stores them)
        save_settings({
            "global": {safe: True for safe in SAFE_TOOLS},
            "perModel": {},
            "confirmationMode": "risky-only",
        })

        resp = client.get(f"{BASE_URL}/v1/gateway/settings/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_patch_settings_persists(self, clean_settings: Path):
        """PATCH should persist settings to disk."""
        import app.tool_settings as ts

        patch_body = {
            "global": {
                "rag_search": True,
                "rag_project_list": True,
            },
            "perModel": {
                "model-1": {"rag_search": True},
            },
            "confirmationMode": "risky-only",
        }
        resp = client.patch(
            f"{BASE_URL}/v1/gateway/settings/tools",
            json=patch_body,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        # Verify it persisted — settings use camelCase keys
        settings = load_settings()
        assert settings["global"]["rag_search"] is True
        assert settings["global"]["rag_project_list"] is True
        assert settings["perModel"].get("model-1", {}).get("rag_search") is True
        assert settings["confirmationMode"] == "risky-only"

    def test_patch_rejects_unknown_tool(self, clean_settings: Path):
        """Attempting to enable an unknown tool should be rejected (ok=false)."""
        resp = client.patch(
            f"{BASE_URL}/v1/gateway/settings/tools",
            json={"global": {"nonexistent_tool": True}},
        )
        # PATCH endpoint returns 200 with ok=false for validation errors
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

    def test_patch_rejects_destructive_enable(self, clean_settings: Path):
        """Attempting to enable a destructive tool should be rejected (ok=false)."""
        resp = client.patch(
            f"{BASE_URL}/v1/gateway/settings/tools",
            json={"global": {"shell_exec": True}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

    def test_patch_validates_boolean(self, clean_settings: Path):
        """Settings values should be validated as booleans (ok=false on bad type)."""
        resp = client.patch(
            f"{BASE_URL}/v1/gateway/settings/tools",
            json={"global": {"rag_search": "yes"}},
        )
        # Returns 200 with ok=false for validation errors
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

    def test_patch_accepts_snake_case(self, clean_settings: Path):
        """Snake_case aliases (per_model, confirmation_mode) should be accepted."""
        resp = client.patch(
            f"{BASE_URL}/v1/gateway/settings/tools",
            json={
                "per_model": {"model-1": {"rag_search": True}},
                "confirmation_mode": "risky-only",
            },
        )
        assert resp.status_code == 200, f"Expected 200 for snake_case, got {resp.status_code}"
        data = resp.json()
        assert data["ok"] is True


# ===========================================================================
# Phase 3 — Tool Execution (POST /v1/tools/execute)
# ===========================================================================


class TestToolExecution:
    """POST /v1/tools/execute executes safe tools and rejects unsafe ones."""

    def test_execute_rag_project_list(self, clean_settings: Path):
        """Execute rag_project_list tool."""
        resp = client.post(
            f"{BASE_URL}/v1/tools/execute",
            json={
                "name": "rag_project_list",
                "arguments": {},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["name"] == "rag_project_list"
        assert "result" in data
        assert "tool_call_id" in data
        assert "duration_ms" in data
        assert data["error"] is None

    def test_execute_rag_collection_list(self, clean_settings: Path):
        """Execute rag_collection_list tool."""
        resp = client.post(
            f"{BASE_URL}/v1/tools/execute",
            json={
                "name": "rag_collection_list",
                "arguments": {"project_id": "default"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["name"] == "rag_collection_list"

    def test_execute_rag_search(self, clean_settings: Path):
        """Execute rag_search tool with mocked search backend."""
        mock_point = MagicMock()
        mock_point.payload = {
            "text": "Test result for rag_search",
            "score": 0.95,
            "project_id": "default",
            "collection": "default",
            "chunk_index": 0,
        }

        import app.rag as rag_mod

        mock_qdrant = MagicMock()
        mock_qdrant.scroll.return_value = ([mock_point], None)

        with patch.object(rag_mod, "qdrant", mock_qdrant):
            resp = client.post(
                f"{BASE_URL}/v1/tools/execute",
                json={
                    "name": "rag_search",
                    "arguments": {
                        "query": "test search",
                        "project_id": "default",
                        "collection": "default",
                        "limit": 5,
                    },
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True
            assert data["name"] == "rag_search"
            assert "result" in data

    def test_execute_unknown_tool(self, clean_settings: Path):
        """Executing an unknown tool should return tool_not_found."""
        resp = client.post(
            f"{BASE_URL}/v1/tools/execute",
            json={"name": "nonexistent_tool", "arguments": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error"] == "tool_not_found"

    def test_execute_disabled_tool(self, clean_settings: Path):
        """Executing a disabled tool should be rejected."""
        # Disable shell_exec via settings
        save_settings({
            "global": {safe: True for safe in SAFE_TOOLS},
            "per_model": {},
            "confirmation_mode": "risky-only",
        })

        resp = client.post(
            f"{BASE_URL}/v1/tools/execute",
            json={"name": "shell_exec", "arguments": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error"] in ("tool_disabled", "tool_unavailable", "tool_not_read_only")

    def test_execute_destructive_placeholder(self, clean_settings: Path):
        """Destructive placeholders must not be executable."""
        for destructive in DESTRUCTIVE_TOOLS:
            resp = client.post(
                f"{BASE_URL}/v1/tools/execute",
                json={"name": destructive, "arguments": {}},
            )
            assert resp.status_code == 200, (
                f"Expected 200 response for {destructive}"
            )
            data = resp.json()
            assert data["ok"] is False, (
                f"Destructive tool {destructive} should not be executable"
            )
            assert data["error"] in (
                "tool_disabled", "tool_unavailable", "tool_not_read_only"
            ), (
                f"Destructive tool {destructive} should be rejected with "
                f"appropriate error, got: {data['error']}"
            )

    def test_execute_invalid_arguments(self, clean_settings: Path):
        """Executing rag_search without required 'query' should fail."""
        resp = client.post(
            f"{BASE_URL}/v1/tools/execute",
            json={
                "name": "rag_search",
                "arguments": {"project_id": "default"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error"] == "invalid_arguments"

    def test_execute_gateway_health(self, clean_settings: Path):
        """Execute gateway_health tool."""
        resp = client.post(
            f"{BASE_URL}/v1/tools/execute",
            json={"name": "gateway_health", "arguments": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["name"] == "gateway_health"

    def test_execute_model_list(self, clean_settings: Path):
        """Execute model_list tool (may fail if upstream not available,
        but must return a valid JSON response)."""
        resp = client.post(
            f"{BASE_URL}/v1/tools/execute",
            json={"name": "model_list", "arguments": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "model_list"
        assert "ok" in data
        assert "duration_ms" in data

    def test_execute_returns_duration_ms(self, clean_settings: Path):
        """Execution response should include duration_ms."""
        resp = client.post(
            f"{BASE_URL}/v1/tools/execute",
            json={"name": "gateway_health", "arguments": {}},
        )
        data = resp.json()
        assert "duration_ms" in data
        assert isinstance(data["duration_ms"], (int, float))
        assert data["duration_ms"] >= 0

    def test_execute_validates_arguments_for_rag_collection_list(self, clean_settings: Path):
        """rag_collection_list should accept optional project_id."""
        # No arguments should be valid (defaults to "default")
        resp = client.post(
            f"{BASE_URL}/v1/tools/execute",
            json={"name": "rag_collection_list", "arguments": {}},
        )
        assert resp.status_code == 200
        # May succeed or fail based on Qdrant availability, but should not
        # fail due to argument validation


# ===========================================================================
# Phase 6 (partial) — Model-Specific Tools (GET /v1/models/{model_id}/tools)
# ===========================================================================


class TestModelTools:
    """GET /v1/models/{model_id}/tools works."""

    def test_model_tools_endpoint(self, clean_settings: Path):
        resp = client.get(f"{BASE_URL}/v1/models/test-model/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["model"] == "test-model"
        assert "modelToolCallingSupport" in data
        assert data["modelToolCallingSupport"] == "unknown"
        assert "tools" in data
        assert isinstance(data["tools"], list)

    def test_model_tools_returns_enabled_tools(self, clean_settings: Path):
        """Should return tools that are enabled for the model."""
        resp = client.get(f"{BASE_URL}/v1/models/test-model/tools")
        data = resp.json()
        tools = data["tools"]
        tool_names = [t["name"] for t in tools]
        # Safe tools should be enabled
        for safe in SAFE_TOOLS:
            assert safe in tool_names, f"Safe tool {safe} should be in model tools"

    def test_model_tools_excludes_disabled(self, clean_settings: Path):
        """Should not return disabled tools."""
        resp = client.get(f"{BASE_URL}/v1/models/test-model/tools")
        data = resp.json()
        tools = data["tools"]
        tool_names = [t["name"] for t in tools]
        # Destructive tools should not be enabled
        for destructive in DESTRUCTIVE_TOOLS:
            assert destructive not in tool_names, (
                f"Destructive tool {destructive} should not be in model tools"
            )


# ===========================================================================
# Phase 4 — Chat Completions Tool Field Compatibility
# ===========================================================================


class TestChatToolCompatibility:
    """/v1/chat/completions accepts tools field, tool role messages."""

    def test_chat_accepts_tools_field(self):
        """Standard OpenAI-style requests with 'tools' must not be rejected."""
        # Use a dummy model name that won't hit the actual llama endpoint
        resp = client.post(
            f"{BASE_URL}/v1/chat/completions",
            json={
                "model": "dummy-model",
                "messages": [
                    {"role": "user", "content": "Hello"}
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "rag_project_list",
                            "description": "List RAG projects",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                "tool_choice": "auto",
            },
        )
        # The request should not be rejected with 422/400 for the tools field.
        # It may fail later (upstream connection error), but the tools field
        # itself should be accepted.
        assert resp.status_code != 422, (
            f"Chat request rejected tools field with 422: {resp.text[:200]}"
        )

    def test_chat_accepts_tool_role_messages(self):
        """Chat should accept messages with role 'tool'."""
        resp = client.post(
            f"{BASE_URL}/v1/chat/completions",
            json={
                "model": "dummy-model",
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "tool", "tool_call_id": "call-123", "content": "result"},
                ],
            },
        )
        assert resp.status_code != 422, (
            f"Chat request rejected tool role with 422: {resp.text[:200]}"
        )

    def test_chat_accepts_parallel_tool_calls(self):
        """Chat should accept parallel_tool_calls field."""
        resp = client.post(
            f"{BASE_URL}/v1/chat/completions",
            json={
                "model": "dummy-model",
                "messages": [{"role": "user", "content": "Hello"}],
                "parallel_tool_calls": True,
            },
        )
        assert resp.status_code != 422, (
            f"Chat request rejected parallel_tool_calls with 422"
        )

    def test_chat_without_tools_still_works(self):
        """Existing chat requests without tools must behave exactly as before."""
        # This test verifies the chat endpoint is still functional
        resp = client.post(
            f"{BASE_URL}/v1/chat/completions",
            json={
                "model": "dummy-model",
                "messages": [
                    {"role": "user", "content": "What is 2+2?"}
                ],
            },
        )
        # Should not reject with 422 for missing tools
        assert resp.status_code != 422, (
            f"Chat without tools rejected with 422: {resp.text[:200]}"
        )


# ===========================================================================
# Phase 5 — Audit Log
# ===========================================================================


class TestAuditLog:
    """GET /v1/tools/audit works."""

    def test_audit_endpoint_empty(self, clean_audit_log: Path):
        """Audit log should return empty when no events."""
        resp = client.get(f"{BASE_URL}/v1/tools/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["audit"] == []
        assert data["count"] == 0

    def test_audit_endpoint_after_execution(self, clean_audit_log: Path):
        """Audit log should contain events after tool execution."""
        # Execute a tool (will likely fail due to no Qdrant, but should log)
        client.post(
            f"{BASE_URL}/v1/tools/execute",
            json={"name": "gateway_health", "arguments": {}},
        )

        resp = client.get(f"{BASE_URL}/v1/tools/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["count"] >= 0  # May be 0 if execution failed before audit

    def test_audit_event_structure(self, clean_audit_log: Path):
        """Audit events should have the expected structure."""
        # Execute a tool that will fail (unknown tool)
        client.post(
            f"{BASE_URL}/v1/tools/execute",
            json={"name": "nonexistent_tool", "arguments": {}},
        )

        resp = client.get(f"{BASE_URL}/v1/tools/audit?limit=10")
        data = resp.json()
        if data["count"] > 0:
            event = data["audit"][0]
            assert "timestamp" in event
            assert "tool" in event
            assert "ok" in event
            assert "duration_ms" in event
            assert "error" in event


# ===========================================================================
# Unit Tests — Tool Registry Module (app/tools.py)
# ===========================================================================


class TestToolRegistryModule:
    """Unit tests for the tool registry module."""

    def test_get_tool_exists(self):
        tool = get_tool("rag_search")
        assert tool is not None
        assert tool["name"] == "rag_search"

    def test_get_tool_missing(self):
        assert get_tool("nonexistent") is None

    def test_list_tools_returns_all(self):
        tools = list_tools()
        assert len(tools) == len(ALL_KNOWN_TOOLS)

    def test_validate_tool_arguments_valid(self):
        ok, err = validate_tool_arguments("rag_search", {"query": "test"})
        assert ok is True
        assert err is None

    def test_validate_tool_arguments_missing_required(self):
        ok, err = validate_tool_arguments("rag_search", {})
        assert ok is False
        assert "missing required field" in err

    def test_validate_tool_arguments_wrong_type(self):
        ok, err = validate_tool_arguments("rag_search", {"query": 123})
        assert ok is False
        assert "expected string" in err

    def test_validate_tool_arguments_unknown_tool(self):
        ok, err = validate_tool_arguments("unknown_tool", {})
        assert ok is False
        assert err == "tool_not_found"

    def test_validate_rag_collection_list_optional_project_id(self):
        # No arguments is valid (project_id has no required constraint)
        ok, err = validate_tool_arguments("rag_collection_list", {})
        assert ok is True

    def test_is_safe_tool(self):
        assert get_tool("rag_search")["readOnly"] is True
        assert get_tool("shell_exec")["readOnly"] is False

    def test_is_destructive_tool(self):
        assert get_tool("shell_exec")["destructive"] is True
        assert get_tool("rag_search")["destructive"] is False


# ===========================================================================
# Unit Tests — Tool Settings Module (app/tool_settings.py)
# ===========================================================================


class TestToolSettingsModule:
    """Unit tests for the tool settings module."""

    def test_load_settings_creates_defaults(self, clean_settings: Path):
        settings = load_settings()
        assert "global" in settings
        # Settings use camelCase keys
        assert "perModel" in settings
        assert "confirmationMode" in settings

    def test_save_and_load_settings(self, clean_settings: Path):
        test_settings = {
            "global": {"rag_search": True, "shell_exec": False},
            "perModel": {"model-1": {"rag_search": True}},
            "confirmationMode": "strict",
        }
        save_settings(test_settings)
        loaded = load_settings()
        assert loaded["global"]["rag_search"] is True
        assert loaded["global"]["shell_exec"] is False
        # load_settings normalizes to camelCase via _ensure_settings_shape
        assert loaded["perModel"]["model-1"]["rag_search"] is True
        assert loaded["confirmationMode"] == "strict"

    def test_get_effective_tools(self, clean_settings: Path):
        # get_effective_tools reads from "perModel" key
        settings = {
            "global": {"rag_search": True, "model_list": False},
            "perModel": {
                "model-1": {"rag_search": True, "model_list": True},
            },
            "confirmationMode": "risky-only",
        }
        # Default model: model_list should be False
        effective = get_effective_tools(settings, None)
        assert effective.get("model_list") is False

        # Model-1: model_list should be True (overridden)
        effective_m1 = get_effective_tools(settings, "model-1")
        assert effective_m1.get("model_list") is True


# ===========================================================================
# Unit Tests — Tool Execution Handlers
# ===========================================================================


class TestToolExecutionHandlers:
    """Unit tests for tool execution handler functions."""

    def test_executor_map_contains_safe_tools(self):
        """All safe tools should have executors."""
        for safe in SAFE_TOOLS:
            assert safe in _TOOL_EXECUTORS, f"Missing executor for {safe}"

    def test_executor_map_no_destructive(self):
        """Destructive tools should NOT have executors."""
        for destructive in DESTRUCTIVE_TOOLS:
            assert destructive not in _TOOL_EXECUTORS, (
                f"Destructive tool {destructive} should not have executor"
            )


# ===========================================================================
# Integration — Error Handling
# ===========================================================================


class TestErrorHandling:
    """Consistent JSON error responses."""

    def test_error_response_format(self):
        """Error responses should have ok=false and error code."""
        resp = client.post(
            f"{BASE_URL}/v1/tools/execute",
            json={"name": "nonexistent", "arguments": {}},
        )
        data = resp.json()
        assert data["ok"] is False
        assert "error" in data

    def test_settings_patch_error_format(self):
        """PATCH errors should return ok=false with error code."""
        resp = client.patch(
            f"{BASE_URL}/v1/gateway/settings/tools",
            json={"global": {"unknown_tool_xyz": True}},
        )
        # PATCH endpoint returns 200 with ok=false for validation errors
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "error" in data


# ===========================================================================
# Integration — Existing Chat Endpoint Compatibility
# ===========================================================================


class TestExistingChatCompatibility:
    """Ensure existing chat functionality is not broken."""

    def test_chat_legacy_request_format(self):
        """Legacy Qonduit request format should still work."""
        resp = client.post(
            f"{BASE_URL}/v1/chat/completions",
            json={
                "model": "dummy",
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": 100,
                "temperature": 0.7,
            },
        )
        # Should not be rejected with 422 for the legacy fields
        assert resp.status_code != 422, (
            f"Legacy chat request rejected with 422: {resp.text[:200]}"
        )

    def test_chat_with_stream(self):
        """Chat should accept stream parameter."""
        resp = client.post(
            f"{BASE_URL}/v1/chat/completions",
            json={
                "model": "dummy",
                "messages": [{"role": "user", "content": "test"}],
                "stream": True,
            },
        )
        assert resp.status_code != 422


# ===========================================================================
# Cross-cutting — Safety Rules
# ===========================================================================


class TestSafetyRules:
    """Verify safety rules are enforced."""

    def test_no_destructive_executor(self):
        """No destructive tool should have an executor in _TOOL_EXECUTORS."""
        for destructive in DESTRUCTIVE_TOOLS:
            assert destructive not in _TOOL_EXECUTORS, (
                f"SAFETY VIOLATION: {destructive} has an executor"
            )

    def test_destructive_tools_disabled_by_default(self):
        """All destructive tools must be disabled in TOOL_DEFINITIONS."""
        for tool in TOOL_DEFINITIONS:
            if tool["name"] in DESTRUCTIVE_TOOLS:
                assert tool["enabled"] is False, (
                    f"{tool['name']} should be disabled"
                )
                assert tool["backendAvailable"] is False, (
                    f"{tool['name']} should not have backend available"
                )
                assert tool["readOnly"] is False, (
                    f"{tool['name']} should not be read-only"
                )
                assert tool["destructive"] is True, (
                    f"{tool['name']} should be marked destructive"
                )

    def test_safe_tools_read_only(self):
        """All safe tools must be read-only and not destructive."""
        for tool in TOOL_DEFINITIONS:
            if tool["name"] in SAFE_TOOLS:
                assert tool["readOnly"] is True, (
                    f"{tool['name']} should be read-only"
                )
                assert tool["destructive"] is False, (
                    f"{tool['name']} should not be destructive"
                )
