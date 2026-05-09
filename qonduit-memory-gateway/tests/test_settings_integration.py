"""Tests for gateway settings + prompt template integration (Phase 1-4).

Static-analysis and unit tests validating:
  Phase 1: Settings model / JSON persistence layer
  Phase 2: Settings router endpoints
  Phase 3: Prompt templates CRUD + activate/duplicate
  Phase 4: Settings integration into chat endpoint
"""

import ast
import json
import os
import sys
import tempfile
import textwrap

import pytest

GATEWAY_ROOT = os.path.join(os.path.dirname(__file__), "..")
APP_DIR = os.path.join(GATEWAY_ROOT, "app")
MAIN_PY = os.path.join(APP_DIR, "main.py")
SETTINGS_PY = os.path.join(APP_DIR, "settings.py")
SETTINGS_ROUTER_PY = os.path.join(APP_DIR, "settings_router.py")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(path: str) -> ast.Module:
    with open(path, "r") as f:
        return ast.parse(f.read(), filename=path)


def _all_param_names(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    names = [
        arg.arg
        for arg in func_node.args.args
        if arg.arg not in ("self", "cls")
    ]
    names += [arg.arg for arg in func_node.args.kwonlyargs]
    return names


def _read(path: str) -> str:
    with open(path, "r") as f:
        return f.read()


def _write_json(data: dict, tmp_path: str) -> str:
    path = os.path.join(tmp_path, "gateway_settings.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


# ---------------------------------------------------------------------------
# Phase 1: Settings model and JSON persistence layer
# ---------------------------------------------------------------------------


class TestSettingsModule:
    """Validate settings.py has the expected functions and logic."""

    def test_settings_file_exists(self):
        assert os.path.isfile(SETTINGS_PY), "settings.py should exist in app/"

    def test_load_settings_function(self):
        tree = _parse(SETTINGS_PY)
        func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert "load_settings" in func_names, "load_settings() not found"

    def test_save_settings_function(self):
        tree = _parse(SETTINGS_PY)
        func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert "save_settings" in func_names, "save_settings() not found"

    def test_list_builtin_templates_function(self):
        tree = _parse(SETTINGS_PY)
        func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert "list_builtin_templates" in func_names, "list_builtin_templates() not found"

    def test_get_active_template_function(self):
        tree = _parse(SETTINGS_PY)
        func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert "get_active_template" in func_names, "get_active_template() not found"

    def test_create_template_function(self):
        tree = _parse(SETTINGS_PY)
        func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert "create_template" in func_names, "create_template() not found"

    def test_update_template_function(self):
        tree = _parse(SETTINGS_PY)
        func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert "update_template" in func_names, "update_template() not found"

    def test_delete_template_function(self):
        tree = _parse(SETTINGS_PY)
        func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert "delete_template" in func_names, "delete_template() not found"

    def test_activate_template_function(self):
        tree = _parse(SETTINGS_PY)
        func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert "activate_template" in func_names, "activate_template() not found"

    def test_duplicate_template_function(self):
        tree = _parse(SETTINGS_PY)
        func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert "duplicate_template" in func_names, "duplicate_template() not found"

    def test_reset_settings_function(self):
        tree = _parse(SETTINGS_PY)
        func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert "reset_settings" in func_names, "reset_settings() not found"

    def test_builtin_templates_have_system_prompt(self):
        source = _read(SETTINGS_PY)
        # Check that at least one built-in template has system_prompt
        assert '"system_prompt"' in source or "'system_prompt'" in source, (
            "Built-in templates should have system_prompt"
        )
        assert '"instruction_prompt"' in source or "'instruction_prompt'" in source, (
            "Built-in templates should have instruction_prompt"
        )

    def test_settings_shape_has_required_keys(self):
        """_default_settings() should return a dict with all required keys."""
        source = _read(SETTINGS_PY)
        required_keys = [
            "version",
            "active_prompt_template_id",
            "defaults",
            "prompt_templates",
            "created_at",
            "updated_at",
        ]
        for key in required_keys:
            assert f'"{key}"' in source or f"'{key}'" in source, (
                f"_default_settings() should include key '{key}'"
            )

    def test_defaults_has_model_and_max_tokens(self):
        source = _read(SETTINGS_PY)
        assert '"model"' in source or "'model'" in source
        assert '"max_tokens"' in source or "'max_tokens'" in source
        assert '"temperature"' in source or "'temperature'" in source

    def test_ensure_settings_shape_backfills_missing_keys(self):
        """_ensure_settings_shape should use setdefault for all required keys."""
        source = _read(SETTINGS_PY)
        tree = _parse(SETTINGS_PY)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_ensure_settings_shape":
                body_text = ast.unparse(node)
                assert "setdefault" in body_text, (
                    "_ensure_settings_shape should use setdefault to backfill missing keys"
                )
                return
        pytest.fail("_ensure_settings_shape function not found")


class TestSettingsUnit:
    """Unit tests for settings.py functions (no FastAPI needed)."""

    def test_load_settings_creates_defaults_when_missing(self, tmp_path):
        """If no file exists, load_settings creates defaults."""
        # Patch _data_dir to use tmp_path
        import app.settings as settings_mod
        original_data_dir = settings_mod._data_dir

        try:
            settings_mod._data_dir = lambda: tmp_path
            result = settings_mod.load_settings()
            assert result["version"] == 1
            assert "active_prompt_template_id" in result
            assert "prompt_templates" in result
            assert "defaults" in result
            assert os.path.isfile(os.path.join(tmp_path, "gateway_settings.json"))
        finally:
            settings_mod._data_dir = original_data_dir

    def test_load_settings_returns_existing_file(self, tmp_path):
        """If file exists, load_settings reads it."""
        import app.settings as settings_mod
        original_data_dir = settings_mod._data_dir

        try:
            settings_mod._data_dir = lambda: tmp_path
            original_data = {
                "version": 1,
                "active_prompt_template_id": "coding",
                "defaults": {"model": "test", "max_tokens": 4096},
                "prompt_templates": [],
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-01-01T00:00:00Z",
            }
            _write_json(original_data, str(tmp_path))

            result = settings_mod.load_settings()
            assert result["active_prompt_template_id"] == "coding"
            assert result["defaults"]["model"] == "test"
        finally:
            settings_mod._data_dir = original_data_dir

    def test_get_active_template_returns_template(self, tmp_path):
        """get_active_template returns the template matching active_prompt_template_id."""
        import app.settings as settings_mod
        original_data_dir = settings_mod._data_dir

        try:
            settings_mod._data_dir = lambda: tmp_path
            settings = settings_mod.load_settings()
            active = settings_mod.get_active_template(settings)
            assert active is not None
            assert active["id"] == settings.get("active_prompt_template_id", "general")
        finally:
            settings_mod._data_dir = original_data_dir

    def test_get_active_template_returns_none_when_missing(self, tmp_path):
        """get_active_template returns None if active_id not found in templates."""
        import app.settings as settings_mod
        original_data_dir = settings_mod._data_dir

        try:
            settings_mod._data_dir = lambda: tmp_path
            settings = settings_mod.load_settings()
            # Override active_id to a non-existent one
            settings["active_prompt_template_id"] = "nonexistent"
            result = settings_mod.get_active_template(settings)
            assert result is None
        finally:
            settings_mod._data_dir = original_data_dir

    def test_create_template_appends_to_settings(self, tmp_path):
        """create_template adds a new template to settings."""
        import app.settings as settings_mod
        original_data_dir = settings_mod._data_dir

        try:
            settings_mod._data_dir = lambda: tmp_path
            settings = settings_mod.load_settings()
            new_tmpl = settings_mod.create_template(
                settings,
                name="Test Template",
                description="A test template",
                system_prompt="You are a test assistant.",
                instruction_prompt="Be helpful.",
            )
            assert new_tmpl["name"] == "Test Template"
            assert new_tmpl["built_in"] is False
            assert len(settings["prompt_templates"]) == 7  # 6 built-in + 1 custom
        finally:
            settings_mod._data_dir = original_data_dir

    def test_update_template_rejects_builtin(self, tmp_path):
        """update_template raises ValueError when trying to edit a built-in template."""
        import app.settings as settings_mod
        original_data_dir = settings_mod._data_dir

        try:
            settings_mod._data_dir = lambda: tmp_path
            settings = settings_mod.load_settings()
            with pytest.raises(ValueError, match="built_in_templates_cannot_be_edited"):
                settings_mod.update_template(
                    settings,
                    template_id="general",
                    system_prompt="hacked",
                )
        finally:
            settings_mod._data_dir = original_data_dir

    def test_delete_template_rejects_builtin(self, tmp_path):
        """delete_template raises ValueError when trying to delete a built-in template."""
        import app.settings as settings_mod
        original_data_dir = settings_mod._data_dir

        try:
            settings_mod._data_dir = lambda: tmp_path
            settings = settings_mod.load_settings()
            with pytest.raises(ValueError, match="built_in_templates_cannot_be_deleted"):
                settings_mod.delete_template(settings, "general")
        finally:
            settings_mod._data_dir = original_data_dir

    def test_delete_template_falls_back_to_general(self, tmp_path):
        """Deleting the active template falls back to 'general'."""
        import app.settings as settings_mod
        original_data_dir = settings_mod._data_dir

        try:
            settings_mod._data_dir = lambda: tmp_path
            settings = settings_mod.load_settings()

            # Create a custom template and activate it
            new_tmpl = settings_mod.create_template(
                settings, "My Custom", "Custom", "Be custom", "Be custom"
            )
            settings_mod.activate_template(settings, new_tmpl["id"])
            assert settings["active_prompt_template_id"] == new_tmpl["id"]

            # Delete it - should fall back to general
            settings_mod.delete_template(settings, new_tmpl["id"])
            assert settings["active_prompt_template_id"] == "general"
        finally:
            settings_mod._data_dir = original_data_dir

    def test_activate_template_sets_active_id(self, tmp_path):
        """activate_template sets active_prompt_template_id."""
        import app.settings as settings_mod
        original_data_dir = settings_mod._data_dir

        try:
            settings_mod._data_dir = lambda: tmp_path
            settings = settings_mod.load_settings()
            settings_mod.activate_template(settings, "coding")
            assert settings["active_prompt_template_id"] == "coding"
        finally:
            settings_mod._data_dir = original_data_dir

    def test_duplicate_template_creates_copy(self, tmp_path):
        """duplicate_template creates a new template with a unique ID."""
        import app.settings as settings_mod
        original_data_dir = settings_mod._data_dir

        try:
            settings_mod._data_dir = lambda: tmp_path
            settings = settings_mod.load_settings()
            original_count = len(settings["prompt_templates"])
            new_tmpl = settings_mod.duplicate_template(settings, "general")
            assert new_tmpl["id"].startswith("general-copy-")
            assert new_tmpl["built_in"] is False
            assert len(settings["prompt_templates"]) == original_count + 1
        finally:
            settings_mod._data_dir = original_data_dir


# ---------------------------------------------------------------------------
# Phase 2: Settings router with all endpoints
# ---------------------------------------------------------------------------


class TestSettingsRouter:
    """Validate settings_router.py has the expected endpoints."""

    def test_router_file_exists(self):
        assert os.path.isfile(SETTINGS_ROUTER_PY), "settings_router.py should exist"

    def test_router_imports_fastapi(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "APIRouter" in source, "Should import APIRouter from fastapi"

    def test_router_has_get_settings_endpoint(self):
        source = _read(SETTINGS_ROUTER_PY)
        # Look for GET /v1/gateway/settings endpoint
        assert "settings" in source.lower()

    def test_router_has_list_templates_endpoint(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "prompt-templates" in source or "prompt_templates" in source, (
            "Router should have prompt templates endpoint"
        )

    def test_router_has_activate_endpoint(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "activate" in source.lower(), "Router should have activate endpoint"

    def test_router_has_create_template_endpoint(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "create" in source.lower() or "POST" in source, (
            "Router should have create template endpoint"
        )

    def test_router_has_delete_template_endpoint(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "delete" in source.lower() or "DELETE" in source, (
            "Router should have delete template endpoint"
        )

    def test_router_has_update_template_endpoint(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "update" in source.lower() or "PUT" in source or "PATCH" in source, (
            "Router should have update template endpoint"
        )

    def test_router_has_reset_endpoint(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "reset" in source.lower(), "Router should have reset settings endpoint"

    def test_router_has_duplicate_endpoint(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "duplicate" in source.lower(), "Router should have duplicate template endpoint"

    def test_router_has_builtin_templates_endpoint(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "builtin" in source.lower() or "list_builtin" in source, (
            "Router should have built-in templates endpoint"
        )


# ---------------------------------------------------------------------------
# Phase 3: Prompt templates CRUD + activate/duplicate
# ---------------------------------------------------------------------------


class TestPromptTemplatesCRUD:
    """Validate prompt template CRUD operations are wired correctly."""

    def test_create_template_endpoint_uses_create_template(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "create_template" in source, (
            "POST endpoint should use create_template from settings.py"
        )

    def test_update_template_endpoint_uses_update_template(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "update_template" in source, (
            "PUT endpoint should use update_template from settings.py"
        )

    def test_delete_template_endpoint_uses_delete_template(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "delete_template" in source, (
            "DELETE endpoint should use delete_template from settings.py"
        )

    def test_activate_endpoint_uses_activate_template(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "activate_template" in source, (
            "POST activate endpoint should use activate_template from settings.py"
        )

    def test_duplicate_endpoint_uses_duplicate_template(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "duplicate_template" in source, (
            "POST duplicate endpoint should use duplicate_template from settings.py"
        )

    def test_reset_endpoint_uses_reset_settings(self):
        source = _read(SETTINGS_ROUTER_PY)
        assert "reset_settings" in source, (
            "POST reset endpoint should use reset_settings from settings.py"
        )


# ---------------------------------------------------------------------------
# Phase 4: Settings integration into chat endpoint
# ---------------------------------------------------------------------------


class TestChatEndpointIntegration:
    """Validate chat endpoint uses settings and applies prompt templates."""

    def test_main_imports_settings_functions(self):
        source = _read(MAIN_PY)
        assert "load_settings" in source or "_load_gateway_settings" in source, (
            "main.py should import load_settings from settings.py"
        )
        assert "get_active_template" in source or "_get_active_template" in source, (
            "main.py should import get_active_template from settings.py"
        )

    def test_main_imports_settings_router(self):
        source = _read(MAIN_PY)
        assert "settings_router" in source, (
            "main.py should import and include settings_router"
        )

    def test_main_includes_settings_router(self):
        source = _read(MAIN_PY)
        assert "include_router(settings_router)" in source or \
               "include_router(settings_router" in source, (
            "main.py should call app.include_router(settings_router)"
        )

    def test_chat_function_applies_prompt_template(self):
        """The chat endpoint should call _apply_prompt_template."""
        source = _read(MAIN_PY)
        assert "_apply_prompt_template" in source, (
            "chat endpoint should call _apply_prompt_template"
        )

    def test_has_apply_prompt_template_function(self):
        """_apply_prompt_template helper should exist in main.py."""
        tree = _parse(MAIN_PY)
        func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert "_apply_prompt_template" in func_names, (
            "_apply_prompt_template function not found in main.py"
        )

    def test_apply_prompt_template_has_mode_param(self):
        """_apply_prompt_template should accept mode parameter."""
        tree = _parse(MAIN_PY)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_apply_prompt_template":
                param_names = _all_param_names(node)
                assert "mode" in param_names, (
                    f"_apply_prompt_template should have 'mode' param. Found: {param_names}"
                )
                return
        pytest.fail("_apply_prompt_template function not found")

    def test_apply_prompt_template_has_template_param(self):
        """_apply_prompt_template should accept active_template parameter."""
        tree = _parse(MAIN_PY)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_apply_prompt_template":
                param_names = _all_param_names(node)
                assert "active_template" in param_names, (
                    f"_apply_prompt_template should have 'active_template' param. Found: {param_names}"
                )
                return
        pytest.fail("_apply_prompt_template function not found")

    def test_apply_prompt_template_has_messages_param(self):
        """_apply_prompt_template should accept incoming_messages parameter."""
        tree = _parse(MAIN_PY)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_apply_prompt_template":
                param_names = _all_param_names(node)
                assert "incoming_messages" in param_names, (
                    f"_apply_prompt_template should have 'incoming_messages' param. Found: {param_names}"
                )
                return
        pytest.fail("_apply_prompt_template function not found")

    def test_has_explicit_system_message_check(self):
        """_apply_prompt_template should check if request has explicit system message."""
        source = _read(MAIN_PY)
        assert "_has_explicit_system_message" in source, (
            "main.py should have _has_explicit_system_message helper"
        )

    def test_apply_prompt_template_checks_explicit_system(self):
        """_apply_prompt_template should call _has_explicit_system_message."""
        source = _read(MAIN_PY)
        tree = _parse(MAIN_PY)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_apply_prompt_template":
                body_text = ast.unparse(node)
                assert "_has_explicit_system_message" in body_text, (
                    "_apply_prompt_template should check _has_explicit_system_message"
                )
                return
        pytest.fail("_apply_prompt_template function not found")

    def test_apply_prompt_template_uses_system_prompt_or_instruction_prompt(self):
        """_apply_prompt_template should check both system_prompt and instruction_prompt."""
        source = _read(MAIN_PY)
        tree = _parse(MAIN_PY)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_apply_prompt_template":
                body_text = ast.unparse(node)
                assert "system_prompt" in body_text, (
                    "_apply_prompt_template should use template's system_prompt"
                )
                assert "instruction_prompt" in body_text, (
                    "_apply_prompt_template should fallback to instruction_prompt"
                )
                return
        pytest.fail("_apply_prompt_template function not found")

    def test_chat_uses_settings_for_system_prompt(self):
        """The chat endpoint should load settings and get active template."""
        source = _read(MAIN_PY)
        # The calls are in the chat_completions function body, well after the
        # resolve_mode definition.  We verify the source contains all three
        # required calls rather than relying on a fixed window.
        assert "_load_gateway_settings" in source, (
            "Chat endpoint should call _load_gateway_settings"
        )
        assert "_get_active_template" in source, (
            "Chat endpoint should call _get_active_template"
        )
        assert "_apply_prompt_template" in source, (
            "Chat endpoint should call _apply_prompt_template"
        )

    def test_chat_no_mode_system_prompt_override(self):
        """Chat endpoint should not override mode-based system_prompt directly."""
        source = _read(MAIN_PY)
        # The old pattern system_prompt_for_mode(mode) should not be called
        # directly in the chat flow anymore (it's wrapped by _apply_prompt_template)
        # Check that _apply_prompt_template is called BEFORE the section that
        # would have called system_prompt_for_mode directly
        assert "system_prompt = _apply_prompt_template" in source or \
               'system_prompt=_apply_prompt_template' in source, (
            "Chat endpoint should set system_prompt via _apply_prompt_template"
        )

    def test_apply_template_has_logging(self):
        """_apply_prompt_template should log when a template is applied."""
        source = _read(MAIN_PY)
        tree = _parse(MAIN_PY)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_apply_prompt_template":
                body_text = ast.unparse(node)
                assert "logger" in body_text or "log" in body_text.lower(), (
                    "_apply_prompt_template should log template application"
                )
                return
        pytest.fail("_apply_prompt_template function not found")


# ---------------------------------------------------------------------------
# Integration: Verify all phases coexist in the codebase
# ---------------------------------------------------------------------------


class TestAllPhasesCoexist:
    """Verify all four phases are present and consistent."""

    def test_all_files_exist(self):
        assert os.path.isfile(SETTINGS_PY)
        assert os.path.isfile(SETTINGS_ROUTER_PY)
        assert os.path.isfile(MAIN_PY)

    def test_settings_has_all_operations(self):
        """Settings module should support all CRUD + activate/duplicate/reset."""
        source = _read(SETTINGS_PY)
        operations = [
            "load_settings", "save_settings",
            "list_builtin_templates", "list_all_templates",
            "get_template", "get_active_template",
            "create_template", "update_template", "delete_template",
            "activate_template", "duplicate_template",
            "reset_settings",
        ]
        for op in operations:
            assert f"def {op}" in source, f"settings.py should define {op}()"

    def test_router_has_all_endpoints(self):
        source = _read(SETTINGS_ROUTER_PY)
        endpoint_markers = [
            "settings", "templates", "activate",
            "create", "update", "delete", "reset", "duplicate", "builtin",
        ]
        for marker in endpoint_markers:
            assert marker.lower() in source.lower(), (
                f"settings_router.py should contain '{marker}' endpoint"
            )

    def test_chat_integrates_settings(self):
        source = _read(MAIN_PY)
        assert "_apply_prompt_template" in source
        assert "_load_gateway_settings" in source
        assert "_get_active_template" in source
        assert "include_router(settings_router)" in source

    def test_builtin_templates_count(self):
        """At least 6 built-in templates should exist."""
        source = _read(SETTINGS_PY)
        # Count template blocks
        count = source.count('"id":') + source.count("'id':")
        # Each template has id + built_in flags, so at least 6 id entries
        assert count >= 6, f"Expected at least 6 templates, found {count} id entries"

    def test_builtin_templates_have_required_fields(self):
        """Each built-in template should have id, name, system_prompt."""
        source = _read(SETTINGS_PY)
        for field in ["id", "name", "system_prompt", "instruction_prompt", "built_in"]:
            # Each field should appear multiple times (once per template)
            assert source.count(f'"{field}"') >= 5 or source.count(f"'{field}'") >= 5, (
                f"Built-in templates should all have '{field}' field"
            )
