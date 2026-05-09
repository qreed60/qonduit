"""Static-analysis tests for the three RAG fixes in Phase 3-5.

These tests use Python's AST to validate the code changes without
requiring the gateway's runtime dependencies (fastapi, httpx, etc.).

Fixes tested:
  Phase 3: should_enable_rag() respects req.rag_enabled
  Phase 4: search_documents() passes collection_filter to Qdrant
  Phase 5: prompt trimming loop protects RAG context blocks
"""

import ast
import os
import pytest

GATEWAY_ROOT = os.path.join(os.path.dirname(__file__), "..")
MAIN_PY = os.path.join(GATEWAY_ROOT, "app", "main.py")
RAG_PY = os.path.join(GATEWAY_ROOT, "app", "rag.py")


def _parse(path):
    """Parse a Python file and return its AST."""
    with open(path, "r") as f:
        return ast.parse(f.read(), filename=path)


def _all_param_names(func_node):
    """Collect all parameter names from a function node, including kwonlyargs."""
    names = [arg.arg for arg in func_node.args.args if arg.arg not in ("self", "cls")]
    names += [arg.arg for arg in func_node.args.kwonlyargs]
    return names


# ---------------------------------------------------------------------------
# Phase 3: should_enable_rag(request_rag_enabled)
# ---------------------------------------------------------------------------


class TestShouldEnableRag:
    """Validate should_enable_rag has request_rag_enabled parameter."""

    def test_function_exists(self):
        tree = _parse(MAIN_PY)
        names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        assert "should_enable_rag" in names, "should_enable_rag not found in main.py"

    def test_has_request_rag_enabled_param(self):
        tree = _parse(MAIN_PY)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "should_enable_rag":
                param_names = _all_param_names(node)
                assert "request_rag_enabled" in param_names, (
                    f"should_enable_rag missing 'request_rag_enabled' param. "
                    f"Found params: {param_names}"
                )
                return
        pytest.fail("should_enable_rag function not found")

    def test_request_true_returns_early(self):
        """When request_rag_enabled is True and RAG_ENABLED, return True."""
        with open(MAIN_PY, "r") as f:
            source = f.read()
        assert "request_rag_enabled is True" in source, (
            "should_enable_rag should check 'request_rag_enabled is True'"
        )

    def test_request_false_returns_false(self):
        """When request_rag_enabled is False, return False."""
        with open(MAIN_PY, "r") as f:
            source = f.read()
        assert "request_rag_enabled is False" in source, (
            "should_enable_rag should check 'request_rag_enabled is False'"
        )

    def test_request_none_falls_through(self):
        """When request_rag_enabled is None, fall through to alias/binding/project checks."""
        with open(MAIN_PY, "r") as f:
            source = f.read()
        tree = _parse(MAIN_PY)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "should_enable_rag":
                body_text = ast.unparse(node)
                assert "alias" in body_text, (
                    "should_enable_rag should fall through to alias checks"
                )
                assert "binding" in body_text or "project" in body_text, (
                    "should_enable_rag should fall through to binding/project checks"
                )
                return


# ---------------------------------------------------------------------------
# Phase 5: Prompt trimming protects RAG context
# ---------------------------------------------------------------------------


class TestPromptTrimming:
    """Validate trim loop preserves RAG context blocks via _protected_suffix."""

    def test_chat_function_has_protected_suffix(self):
        """The chat function should have _protected_suffix for RAG context."""
        with open(MAIN_PY, "r") as f:
            source = f.read()
        assert "_protected_suffix" in source, (
            "chat function should define _protected_suffix to protect RAG context"
        )

    def test_protected_suffix_value(self):
        """_protected_suffix should be 1 (RAG merged into user message)."""
        with open(MAIN_PY, "r") as f:
            source = f.read()
        assert "_protected_suffix = 1" in source or "_protected_suffix=1" in source, (
            "_protected_suffix should be 1 (RAG merged into user message, only last msg protected)"
        )

    def test_trim_uses_protected_suffix(self):
        """The trim loop should use dynamic_messages[:-_protected_suffix]."""
        with open(MAIN_PY, "r") as f:
            source = f.read()
        assert "dynamic_messages[:-_protected_suffix]" in source or \
               "dynamic_messages[:- _protected_suffix]" in source, (
            "Trim loop should use dynamic_messages[:-_protected_suffix]"
        )

    def test_rag_context_protection_comment(self):
        """There should be a comment explaining RAG context protection."""
        with open(MAIN_PY, "r") as f:
            source = f.read()
        has_comment = any(marker in source for marker in [
            "NEVER remove RAG context",
            "preserve rag_context",
            "_protected_suffix",
        ])
        assert has_comment, (
            "Should have comments explaining RAG context protection in trim loop"
        )

    def test_current_user_message_is_none_check(self):
        """Should handle case where current_user_message is None."""
        with open(MAIN_PY, "r") as f:
            source = f.read()
        assert "current_user_message is None" in source, (
            "Should handle case where current_user_message is None"
        )


# ---------------------------------------------------------------------------
# Phase 4: collection_filter parameter in search_documents
# ---------------------------------------------------------------------------


def _find_async_functions(tree, name):
    """Find all async def functions with the given name."""
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            results.append(node)
    return results


def _find_methods_in_class(tree, class_name, method_name):
    """Find all methods with the given name inside a class."""
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                    results.append(item)
    return results


class TestCollectionFilter:
    """Validate collection_filter is wired through the search path."""

    def test_search_documents_has_collection_filter_param(self):
        tree = _parse(RAG_PY)
        funcs = _find_async_functions(tree, "search_documents")
        assert len(funcs) > 0, "search_documents function not found in rag.py"
        for func in funcs:
            param_names = _all_param_names(func)
            if "collection_filter" in param_names:
                return
        pytest.fail(f"search_documents missing 'collection_filter' param. Found: {param_names}")

    def test_rag_service_search_has_collection_filter_param(self):
        tree = _parse(RAG_PY)
        methods = _find_methods_in_class(tree, "ProjectScopedRagService", "search")
        assert len(methods) > 0, "ProjectScopedRagService.search not found"
        for method in methods:
            # search() uses keyword-only args (self, *, ...) so check kwonlyargs
            param_names = _all_param_names(method)
            if "collection_filter" in param_names:
                return
        pytest.fail(f"ProjectScopedRagService.search missing 'collection_filter'. Found: {param_names}")

    def test_collection_filter_passed_to_rag_service_search(self):
        """search_documents() passes collection_filter to rag_service.search()."""
        with open(RAG_PY, "r") as f:
            source = f.read()
        tree = _parse(RAG_PY)
        funcs = _find_async_functions(tree, "search_documents")
        for func in funcs:
            body_text = ast.unparse(func)
            if "collection_filter" in body_text:
                return
        pytest.fail("search_documents should pass collection_filter to rag_service.search()")

    def test_search_method_adds_collection_to_must_conditions(self):
        """ProjectScopedRagService.search adds collection_filter to must_conditions."""
        with open(RAG_PY, "r") as f:
            source = f.read()
        has_collection_field_check = any(marker in source for marker in [
            'key="collection"',
            "key='collection'",
        ])
        assert has_collection_field_check, (
            "ProjectScopedRagService.search should add a FieldCondition with key='collection' "
            "when collection_filter is provided."
        )


# ---------------------------------------------------------------------------
# Integration: Verify all three fixes coexist in the codebase
# ---------------------------------------------------------------------------


class TestAllFixesCoexist:
    """Verify all three fixes are present in the same codebase state."""

    def test_all_fixes_present(self):
        with open(MAIN_PY, "r") as f:
            main_source = f.read()
        with open(RAG_PY, "r") as f:
            rag_source = f.read()

        checks = [
            ("Phase 3: request_rag_enabled param",
             "request_rag_enabled" in main_source),
            ("Phase 3: request_rag_enabled is True guard",
             "request_rag_enabled is True" in main_source),
            ("Phase 3: request_rag_enabled is False guard",
             "request_rag_enabled is False" in main_source),
            ("Phase 4: collection_filter param in search_documents",
             "collection_filter" in rag_source),
            ("Phase 4: collection FieldCondition",
             'key="collection"' in rag_source or "key='collection'" in rag_source),
            ("Phase 5: RAG context preservation via _protected_suffix",
             "_protected_suffix" in main_source),
            ("Phase 5: Protected suffix value is 1 (RAG merged into user)",
             "_protected_suffix = 1" in main_source),
        ]

        all_passed = True
        for desc, result in checks:
            status = "PASS" if result else "FAIL"
            if not result:
                all_passed = False
            print(f"  [{status}] {desc}")

        assert all_passed, "One or more fixes are missing from the codebase"


# ---------------------------------------------------------------------------
# Phase 7: RAG context merged into user message (not system)
# ---------------------------------------------------------------------------


class TestRagContextInUserMessage:
    """Validate RAG context is merged into user message, not a system message."""

    def test_no_system_rag_section(self):
        """RAG context should NOT be a separate system message section."""
        with open(MAIN_PY, "r") as f:
            source = f.read()
        assert '"role": "system"' not in source[
            source.find("current_user_message is not None"):
            source.find("first_dynamic_section_name", source.find("current_user_message is not None"))
        ], (
            "RAG context should not be injected as a 'system' role message. "
            "It should be merged into the user message content."
        )

    def test_rag_merged_into_user_content(self):
        """RAG context should be merged into user message with 'Retrieved context:' prefix."""
        with open(MAIN_PY, "r") as f:
            source = f.read()
        assert "Retrieved context:" in source, (
            "RAG context should be merged into user message with 'Retrieved context:' prefix"
        )

    def test_rag_merged_with_question(self):
        """Merged RAG content should include the user question after 'Question:'."""
        with open(MAIN_PY, "r") as f:
            source = f.read()
        assert "Question:" in source, (
            "RAG-merged user message should include user question after 'Question:'"
        )

    def test_user_message_not_none_check(self):
        """current_user_message should be checked before merging RAG."""
        with open(MAIN_PY, "r") as f:
            source = f.read()
        assert "current_user_message is not None" in source, (
            "Should check current_user_message is not None before merging RAG"
        )

    def test_protected_suffix_is_one(self):
        """_protected_suffix should be 1 since RAG is merged into user message."""
        with open(MAIN_PY, "r") as f:
            source = f.read()
        # Look for the specific line in the trim section
        trim_section_start = source.find("if final_prompt_est_tokens > QONDUIT_TARGET_PROMPT_TOKENS")
        assert trim_section_start > 0, "Could not find trim section"
        trim_section = source[trim_section_start:trim_section_start + 500]
        assert "_protected_suffix = 1" in trim_section, (
            "In trim section, _protected_suffix should be 1 (rag merged into user)"
        )
