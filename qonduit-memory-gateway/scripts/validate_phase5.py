#!/usr/bin/env python3
"""Lightweight Phase 5 validation checks."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch


def run() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        gateway_root = Path(__file__).resolve().parents[1]
        if str(gateway_root) not in sys.path:
            sys.path.insert(0, str(gateway_root))

        repo_path = Path(temp_dir) / "repo"
        repo_path.mkdir(parents=True, exist_ok=True)
        (repo_path / "src").mkdir(parents=True, exist_ok=True)
        (repo_path / "src" / "main.py").write_text(
            "def hello():\n    return 'hi'\n",
            encoding="utf-8",
        )
        (repo_path / "README.md").write_text("# Demo\n", encoding="utf-8")

        from app.ingest_repo import IngestConfig, ingest_repository

        config = IngestConfig(
            project_id="proj-a",
            repo_path=repo_path,
            branch="main",
            include_patterns=["*.py", "*.md"],
            exclude_patterns=[],
            chunk_size=1200,
            chunk_overlap=200,
            commit_sha="abc123",
        )

        async_add = AsyncMock(return_value="point-id")

        with patch("app.ingest_repo.rag_service.add_document", new=async_add), patch(
            "app.ingest_repo.qdrant.scroll",
            return_value=([], None),
        ), patch("app.ingest_repo.qdrant.delete") as delete_mock:
            stats = asyncio.run(ingest_repository(config))
            assert stats.scanned_files >= 2
            assert stats.ingested_files >= 2
            assert stats.ingested_chunks >= 2
            assert stats.deleted_chunks == 0

            assert async_add.await_count >= 2
            kwargs = async_add.await_args.kwargs
            assert kwargs["project_id"] == "proj-a"
            metadata = kwargs["metadata"]
            assert metadata["project_id"] == "proj-a"
            assert metadata["branch"] == "main"
            assert "file_path" in metadata
            assert metadata["commit_sha"] == "abc123"
            delete_mock.assert_not_called()

    print("Phase 5 validation passed.")


if __name__ == "__main__":
    run()
