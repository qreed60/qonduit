#!/usr/bin/env python3
"""Validate project-scoped RAG injection for MainActivity questions."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    last_chat_payload: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict | None = None, **kwargs):
        if url.endswith("/v1/chat/completions"):
            _FakeAsyncClient.last_chat_payload = json or {}
            return _FakeResponse(
                200,
                {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "created": 1,
                    "model": (json or {}).get("model", "unknown"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {},
                },
            )
        return _FakeResponse(404, {"error": "not found"})


def run() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ["GATEWAY_DATA_DIR"] = temp_dir
        gateway_root = Path(__file__).resolve().parents[1]
        if str(gateway_root) not in sys.path:
            sys.path.insert(0, str(gateway_root))

        from app.main import app

        with patch("app.main.httpx.AsyncClient", _FakeAsyncClient), patch(
            "app.main.search_documents",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "chunk-main-activity",
                        "score": 0.97,
                        "text": (
                            "android/app/src/main/kotlin/com/example/"
                            "MainActivity.kt defines class MainActivity "
                            "extending FlutterActivity."
                        ),
                        "payload": {
                            "file_path": (
                                "android/app/src/main/kotlin/com/example/"
                                "MainActivity.kt"
                            )
                        },
                    }
                ]
            ),
        ):
            client = TestClient(app)
            response = client.post(
                "/v1/chat/completions",
                headers={"X-Project-ID": "android-qonduit"},
                json={
                    "model": "gpt-oss:20b",
                    "messages": [
                        {
                            "role": "user",
                            "content": "Where is MainActivity defined?",
                        }
                    ],
                    "mode": "coding",
                },
            )
            assert response.status_code == 200, response.text

            payload = _FakeAsyncClient.last_chat_payload
            assert isinstance(payload, dict), "Upstream payload was not captured"
            messages = payload.get("messages")
            assert isinstance(messages, list), "messages missing from payload"
            compiled_prompt = "\n".join(str(m.get("content", "")) for m in messages)
            assert "Retrieved context:" in compiled_prompt
            assert "MainActivity.kt" in compiled_prompt

    print("MainActivity RAG validation passed.")


if __name__ == "__main__":
    run()
