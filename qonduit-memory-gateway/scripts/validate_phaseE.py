#!/usr/bin/env python3
"""Validation script for Phase E execution tools and loop wiring."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from starlette.requests import Request

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import main


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    post_call_count = 0
    saw_tool_result = False
    saw_run_build = False
    saw_run_tests = False
    saw_tail_logs = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
        _FakeAsyncClient.post_call_count += 1
        messages = json.get("messages", [])

        saw_apply_patch = any(
            message.get("role") == "tool"
            and "affected_files" in str(message.get("content", ""))
            for message in messages
        )
        _FakeAsyncClient.saw_tool_result = (
            _FakeAsyncClient.saw_tool_result or saw_apply_patch
        )
        _FakeAsyncClient.saw_run_build = _FakeAsyncClient.saw_run_build or any(
            message.get("role") == "tool"
            and "safe_mock_build_ok" in str(message.get("content", ""))
            for message in messages
        )
        _FakeAsyncClient.saw_run_tests = _FakeAsyncClient.saw_run_tests or any(
            message.get("role") == "tool"
            and "safe_mock_tests_ok" in str(message.get("content", ""))
            for message in messages
        )
        _FakeAsyncClient.saw_tail_logs = _FakeAsyncClient.saw_tail_logs or any(
            message.get("role") == "tool"
            and "safe_mock_build_ok" in str(message.get("content", ""))
            and "line_count" in str(message.get("content", ""))
            for message in messages
        )

        if not saw_apply_patch:
            tool_calls = [
                {
                    "id": "call_patch",
                    "type": "function",
                    "function": {
                        "name": "apply_patch",
                        "arguments": json_module.dumps(
                            {
                                "patch": json_module.dumps(
                                    {
                                        "format": "qonduit.patch/v1",
                                        "operations": [
                                            {
                                                "action": "write",
                                                "path": "MainActivity.kt",
                                                "content": "// harmless\n",
                                            }
                                        ],
                                    }
                                )
                            }
                        ),
                    },
                },
                {
                    "id": "call_build",
                    "type": "function",
                    "function": {
                        "name": "run_build",
                        "arguments": json_module.dumps({"timeout_seconds": 30}),
                    },
                },
                {
                    "id": "call_tests",
                    "type": "function",
                    "function": {
                        "name": "run_tests",
                        "arguments": json_module.dumps({"timeout_seconds": 30}),
                    },
                },
                {
                    "id": "call_logs",
                    "type": "function",
                    "function": {
                        "name": "tail_logs",
                        "arguments": json_module.dumps(
                            {"source": "build", "max_lines": 20}
                        ),
                    },
                },
            ]
            return _FakeResponse(
                {
                    "id": "cmpl-1",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": tool_calls,
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            )

        return _FakeResponse(
            {
                "id": "cmpl-2",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Patch and checks completed successfully.",
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
        )


json_module = json


def _fake_request() -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [],
        "query_string": b"",
    }
    return Request(scope)


async def run() -> int:
    print("=== Phase E Validation ===")
    failures = 0

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        gateway_data = root / "gateway_data"
        main.PROJECTS_ROOT = str(root)
        main.GATEWAY_DATA_DIR = str(gateway_data)

        alpha = root / "alpha"
        beta = root / "beta"

        _FakeAsyncClient.post_call_count = 0
        _FakeAsyncClient.saw_tool_result = False
        _FakeAsyncClient.saw_run_build = False
        _FakeAsyncClient.saw_run_tests = False
        _FakeAsyncClient.saw_tail_logs = False

        _write(alpha / "README.md", "alpha start\n")
        _write(alpha / ".qonduit_safe_mock", "enabled\n")
        _write(alpha / "MainActivity.kt", "class MainActivity {}\n")
        _write(beta / "README.md", "beta start\n")

        patch_payload = json.dumps(
            {
                "format": "qonduit.patch/v1",
                "operations": [
                    {
                        "action": "write",
                        "path": "README.md",
                        "content": "alpha patched\n",
                    }
                ],
            }
        )
        patch_raw = await main.execute_apply_patch("alpha", patch_payload)
        patch_result = json.loads(patch_raw)
        patch_schema_ok = (
            patch_result.get("status") == "success"
            and patch_result.get("applied") is True
            and isinstance(patch_result.get("files_changed"), list)
            and isinstance(patch_result.get("summary"), str)
        )
        if (
            patch_result.get("ok")
            and patch_schema_ok
            and (alpha / "README.md").read_text() == "alpha patched\n"
        ):
            print("PASS: apply_patch writes within project root with clear schema.")
        else:
            failures += 1
            print("FAIL: apply_patch did not write expected project file/schema.")
            print(patch_raw)

        blocked_payload = json.dumps(
            {
                "format": "qonduit.patch/v1",
                "operations": [
                    {
                        "action": "write",
                        "path": "../beta/README.md",
                        "content": "hijack\n",
                    }
                ],
            }
        )
        blocked_raw = await main.execute_apply_patch("alpha", blocked_payload)
        blocked_result = json.loads(blocked_raw)
        beta_text = (beta / "README.md").read_text(encoding="utf-8")
        blocked = not blocked_result.get("ok") and beta_text == "beta start\n"
        if blocked:
            print("PASS: writes outside project root are blocked.")
        else:
            failures += 1
            print("FAIL: out-of-project write was not blocked.")
            print(blocked_raw)

        original_client = main.httpx.AsyncClient
        original_rag_enabled = main.RAG_ENABLED
        main.RAG_ENABLED = False
        main.httpx.AsyncClient = _FakeAsyncClient
        try:
            req = main.GatewayChatRequest(
                project_id="alpha",
                model="gpt-oss:20b",
                stream=False,
                mode="coding",
                messages=[
                    main.ChatMessage(
                        role="user",
                        content=(
                            "Read MainActivity.kt, add harmless comment, "
                            "run build/tests, then summarize."
                        ),
                    )
                ],
            )
            response = await main.chat(req=req, request=_fake_request())
        finally:
            main.httpx.AsyncClient = original_client
            main.RAG_ENABLED = original_rag_enabled

        final_content = (
            response.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if "completed successfully" in final_content:
            print("PASS: final assistant answer is returned after tool calls.")
        else:
            failures += 1
            print("FAIL: final assistant answer missing.")
            print(json.dumps(response, indent=2))

        if (alpha / "MainActivity.kt").read_text(encoding="utf-8") == (
            "// harmless\n"
        ):
            print("PASS: apply_patch was executed through the tool loop.")
        else:
            failures += 1
            print("FAIL: apply_patch did not execute through loop.")

        if _FakeAsyncClient.post_call_count == 2:
            print("PASS: convergence achieved before max iterations (2 model calls).")
        else:
            failures += 1
            print("FAIL: expected exactly 2 model calls for convergence.")

        if "max iterations" not in final_content.lower():
            print("PASS: final response did not hit max-iteration fallback.")
        else:
            failures += 1
            print("FAIL: response hit max-iteration fallback unexpectedly.")

        if _FakeAsyncClient.saw_run_build:
            print("PASS: run_build is wired into loop execution.")
        else:
            failures += 1
            print("FAIL: run_build did not execute via loop.")

        if _FakeAsyncClient.saw_run_tests:
            print("PASS: run_tests is wired into loop execution.")
        else:
            failures += 1
            print("FAIL: run_tests did not execute via loop.")

        if _FakeAsyncClient.saw_tail_logs:
            print("PASS: tail_logs is wired into loop execution.")
        else:
            failures += 1
            print("FAIL: tail_logs did not execute via loop.")

    print(f"\nValidation complete. failures={failures}")
    return failures


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
