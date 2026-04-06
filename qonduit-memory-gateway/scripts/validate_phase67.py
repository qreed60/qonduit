#!/usr/bin/env python3
"""Lightweight Phase 6/7 validation checks."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

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
                "data": [{"id": "llama-upstream", "object": "model", "owned_by": "local"}],
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
        return _FakeResponse(404, {"error": "not found"})


def run() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ["GATEWAY_DATA_DIR"] = temp_dir
        os.environ["MODEL_ALIAS_CONFIG"] = json.dumps(
            {
                "android-qonduit": {
                    "model": "llama-upstream",
                    "project_id": "android_project",
                    "default_mode": "coding",
                    "rag_enabled": True,
                }
            }
        )

        gateway_root = Path(__file__).resolve().parents[1]
        if str(gateway_root) not in sys.path:
            sys.path.insert(0, str(gateway_root))

        from app.main import app

        with patch("app.main.httpx.AsyncClient", _FakeAsyncClient), patch(
            "app.main.search_documents",
            new=AsyncMock(return_value=[]),
        ):
            client = TestClient(app)

            models_resp = client.get("/v1/models")
            assert models_resp.status_code == 200, models_resp.text
            data = models_resp.json().get("data", [])
            ids = {item.get("id") for item in data if isinstance(item, dict)}
            assert "android-qonduit" in ids

            chat_resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "android-qonduit",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
            assert chat_resp.status_code == 200, chat_resp.text
            assert chat_resp.json().get("model") == "android-qonduit"

            upstream_payload = _FakeAsyncClient.last_chat_payload or {}
            assert upstream_payload.get("model") == "llama-upstream"

    print("Phase 6/7 validation passed.")


if __name__ == "__main__":
    run()
