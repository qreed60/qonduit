#!/usr/bin/env python3
"""Lightweight Phase 1 validation checks.

Runs a minimal in-process validation for:
- GET /v1/models
- POST /v1/chat/completions with OpenAI-style body
- POST /v1/chat/completions with legacy compatibility fields
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, **kwargs):
        if url.endswith("/v1/models"):
            return _FakeResponse(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "llama-3.1",
                            "object": "model",
                            "owned_by": "local",
                        }
                    ],
                },
            )
        return _FakeResponse(404, text="not found")

    async def post(self, url: str, json: dict | None = None, **kwargs):
        if url.endswith("/v1/chat/completions"):
            model = (json or {}).get("model", "unknown")
            return _FakeResponse(
                200,
                {
                    "id": "chatcmpl-fake",
                    "object": "chat.completion",
                    "created": 1,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "stubbed response",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )
        return _FakeResponse(404, text="not found")


def run() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ["GATEWAY_DATA_DIR"] = temp_dir
        gateway_root = Path(__file__).resolve().parents[1]
        if str(gateway_root) not in sys.path:
            sys.path.insert(0, str(gateway_root))

        from app.main import app

        with patch("app.main.httpx.AsyncClient", _FakeAsyncClient), patch(
            "app.main.search_documents", autospec=True
        ) as search_documents:
            search_documents.return_value = []
            client = TestClient(app)

            models_response = client.get("/v1/models")
            assert models_response.status_code == 200, models_response.text
            models_json = models_response.json()
            assert models_json["object"] == "list"
            assert isinstance(models_json["data"], list)

            standard_response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "llama-3.1",
                    "messages": [{"role": "user", "content": "hello"}],
                    "temperature": 0.3,
                    "max_tokens": 64,
                    "user": "phase1-user",
                },
            )
            assert standard_response.status_code == 200, standard_response.text
            standard_json = standard_response.json()
            assert standard_json["choices"][0]["message"]["role"] == "assistant"

            legacy_response = client.post(
                "/v1/chat/completions",
                json={
                    "conversation_id": "legacy-conversation",
                    "context_size": 8192,
                    "model": "llama-3.1",
                    "messages": [{"role": "user", "content": "legacy"}],
                },
            )
            assert legacy_response.status_code == 200, legacy_response.text

    print("Phase 1 validation passed.")


if __name__ == "__main__":
    run()
