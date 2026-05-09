#!/usr/bin/env python3
"""Lightweight Phase 4 validation checks.

Validates:
- /v1/embeddings OpenAI-compatible passthrough behavior
- project-scoped RAG retrieval injection path in /v1/chat/completions
"""

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

    async def get(self, url: str, **kwargs):
        return _FakeResponse(
            200,
            {
                "object": "list",
                "data": [{"id": "llama-3.1", "object": "model", "owned_by": "local"}],
            },
        )

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
        if url.endswith("/v1/embeddings"):
            return _FakeResponse(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "object": "embedding",
                            "index": 0,
                            "embedding": [0.1, 0.2, 0.3],
                        }
                    ],
                    "model": (json or {}).get("model", "test-embed"),
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
            "app.main.create_embeddings_response",
            new=AsyncMock(
                return_value={
                    "object": "list",
                    "data": [
                        {
                            "object": "embedding",
                            "index": 0,
                            "embedding": [0.5, 0.6],
                        }
                    ],
                    "model": "mock-embedding-model",
                }
            ),
        ), patch(
            "app.main.search_documents",
            new=AsyncMock(
                return_value=[
                    {
                        "id": "doc-1",
                        "score": 0.9,
                        "text": "Function foo() is defined in src/foo.py",
                        "payload": {},
                    }
                ]
            ),
        ):
            client = TestClient(app)

            emb_resp = client.post(
                "/v1/embeddings",
                json={"input": "hello world", "model": "mock-embedding-model"},
            )
            assert emb_resp.status_code == 200, emb_resp.text
            emb_json = emb_resp.json()
            assert emb_json["object"] == "list"
            assert emb_json["data"][0]["object"] == "embedding"

            chat_resp = client.post(
                "/v1/chat/completions",
                headers={"X-Project-ID": "project-a"},
                json={
                    "model": "llama-3.1",
                    "messages": [{"role": "user", "content": "where is foo?"}],
                    "mode": "coding",
                    "rag_collection": "kb-main",
                },
            )
            assert chat_resp.status_code == 200, chat_resp.text

            sent_payload = _FakeAsyncClient.last_chat_payload
            assert isinstance(sent_payload, dict)
            messages = sent_payload.get("messages")
            assert isinstance(messages, list)
            injected = "\n".join(str(m.get("content", "")) for m in messages)
            assert "Retrieved context:" in injected
            assert "src/foo.py" in injected

    print("Phase 4 validation passed.")


if __name__ == "__main__":
    run()
