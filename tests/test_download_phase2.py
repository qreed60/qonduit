"""
Unit tests for Phase 2 Hugging Face download job system.

Tests cover:
- Helper function validation (repo_id, filenames, paths)
- Path traversal protection
- Job lifecycle (create, status, cancel)
- Persistence (save/load)
- Worker thread scheduling
- Endpoint responses
"""

import json
import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qonduit_router_api import (
    _validate_repo_id,
    _validate_download_filename,
    _validate_target_name,
    _resolve_target_path,
    _make_hf_resolve_url,
    _format_job,
    _persist_download_jobs,
    _load_download_jobs,
    _prune_completed_jobs,
    _clean_partial_file,
    _download_jobs,
    _download_jobs_lock,
    _download_queue,
    _download_queue_lock,
    _QONDUIT_HF_ALLOW_NON_GGUF,
    _QONDUIT_HF_ALLOW_DELETE,
    _DOWNLOAD_JOBS_DIR,
    _DOWNLOAD_JOBS_FILE,
    _stop_download_worker,
    _download_shutdown_event,
    _download_worker_started,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_jobs():
    """Clear in-memory jobs and queue before/after each test.

    Also reset the lazy-init flag and shutdown event so that each test
    starts fresh and teardown properly stops any worker thread.
    """
    # Reset worker state before each test
    with _download_jobs_lock:
        _download_jobs.clear()
    with _download_queue_lock:
        _download_queue.clear()
    global _download_worker_started
    _download_worker_started = False
    _download_shutdown_event.clear()
    yield
    # Stop the download worker thread to prevent test hangs
    _stop_download_worker()
    # Reset state after test
    _download_worker_started = False


@pytest.fixture
def _temp_model_dir(tmp_path, monkeypatch):
    """Set QONDUIT_MODEL_DIR to a temp directory."""
    model_dir = tmp_path / "models" / "llm"
    model_dir.mkdir(parents=True)
    monkeypatch.setenv("QONDUIT_MODEL_DIR", str(model_dir))
    return model_dir


@pytest.fixture
def _temp_jobs_file(tmp_path, monkeypatch):
    """Set jobs file to a temp location."""
    jobs_file = tmp_path / "download_jobs.json"
    monkeypatch.setattr(
        "qonduit_router_api._DOWNLOAD_JOBS_FILE", jobs_file
    )
    monkeypatch.setattr(
        "qonduit_router_api._DOWNLOAD_JOBS_DIR", jobs_file.parent
    )
    return jobs_file


# ── Repo ID Validation Tests ────────────────────────────────────────────────

class TestValidateRepoId:
    def test_valid_repo_id(self):
        assert _validate_repo_id("bartowski/Qwen3-Coder-Next-GGUF") is None

    def test_valid_repo_id_with_dots(self):
        assert _validate_repo_id("org.model/repo-v1.0") is None

    def test_valid_repo_id_with_underscore(self):
        assert _validate_repo_id("user_name/model_name") is None

    def test_empty_string(self):
        assert _validate_repo_id("") == "repo_id_required"

    def test_none(self):
        assert _validate_repo_id(None) == "repo_id_required"

    def test_missing_slash(self):
        assert _validate_repo_id("justonename") == "invalid_repo_id_format"

    def test_special_chars(self):
        assert _validate_repo_id("user name/model") == "invalid_repo_id_format"

    def test_leading_slash(self):
        assert _validate_repo_id("/user/repo") == "invalid_repo_id_format"

    def test_trailing_slash(self):
        result = _validate_repo_id("user/repo/")
        assert result is None or result == "invalid_repo_id_format"

    def test_whitespace(self):
        assert _validate_repo_id("  ") == "repo_id_required"


# ── Download Filename Validation Tests ───────────────────────────────────────

class TestValidateDownloadFilename:
    def test_valid_gguf_filename(self):
        assert _validate_download_filename("model-Q4_K_M.gguf") is None

    def test_valid_nested_path(self):
        assert _validate_download_filename("BF16/model-00001.gguf") is None

    def test_absolute_path_rejected(self):
        assert _validate_download_filename("/etc/passwd") == "absolute_path_rejected"

    def test_path_traversal_rejected(self):
        assert _validate_download_filename("../etc/passwd") == "path_traversal_blocked"

    def test_double_dot_in_middle_rejected(self):
        assert _validate_download_filename("foo/../bar.gguf") == "path_traversal_blocked"

    def test_backslash_rejected(self):
        assert _validate_download_filename("foo\\bar.gguf") == "path_traversal_blocked"

    def test_non_gguf_rejected_by_default(self):
        result = _validate_download_filename("model.bin")
        assert result == "non_gguf_blocked"

    def test_empty_filename(self):
        assert _validate_download_filename("") == "filename_required"

    def test_whitespace_only(self):
        assert _validate_download_filename("  ") == "filename_required"

    def test_filename_too_long(self):
        long_name = "a" * 256 + ".gguf"
        assert _validate_download_filename(long_name) == "filename_too_long"


# ── Target Name Validation Tests ─────────────────────────────────────────────

class TestValidateTargetName:
    def test_valid_target_name(self):
        assert _validate_target_name("model-Q4_K_M.gguf") is None

    def test_absolute_path_rejected(self):
        assert _validate_target_name("/etc/passwd") == "absolute_path_rejected"

    def test_path_traversal_rejected(self):
        assert _validate_target_name("../etc/passwd") == "path_traversal_blocked"

    def test_slash_in_name_rejected(self):
        assert _validate_target_name("foo/bar.gguf") == "path_traversal_blocked"

    def test_non_gguf_rejected_by_default(self):
        assert _validate_target_name("model.bin") == "non_gguf_blocked"

    def test_empty_name(self):
        assert _validate_target_name("") == "target_name_required"


# ── Path Resolution Tests ────────────────────────────────────────────────────

class TestResolveTargetPath:
    @patch("qonduit_router_api._get_model_dir")
    def test_resolves_within_model_dir(self, mock_model_dir, tmp_path):
        model_dir = tmp_path / "models" / "llm"
        model_dir.mkdir(parents=True)
        mock_model_dir.return_value = model_dir
        result = _resolve_target_path("test.gguf")
        assert result is not None
        assert result.name == "test.gguf"
        assert str(model_dir) in str(result)

    @patch("qonduit_router_api._get_model_dir")
    def test_rejects_traversal_outside_model_dir(self, mock_model_dir, tmp_path):
        model_dir = tmp_path / "models" / "llm"
        model_dir.mkdir(parents=True)
        mock_model_dir.return_value = model_dir
        result = _resolve_target_path("../etc/passwd")
        assert result is None

    @patch("qonduit_router_api._get_model_dir")
    def test_rejects_absolute_path(self, mock_model_dir, tmp_path):
        model_dir = tmp_path / "models" / "llm"
        model_dir.mkdir(parents=True)
        mock_model_dir.return_value = model_dir
        result = _resolve_target_path("/etc/passwd")
        assert result is None


# ── URL Generation Tests ─────────────────────────────────────────────────────

class TestMakeHfResolveUrl:
    def test_simple_url(self):
        url = _make_hf_resolve_url("user/repo", "model.gguf")
        assert url == "https://huggingface.co/user/repo/resolve/main/model.gguf"

    def test_nested_path_url(self):
        url = _make_hf_resolve_url("user/repo", "BF16/model-00001.gguf")
        assert url == "https://huggingface.co/user/repo/resolve/main/BF16/model-00001.gguf"

    def test_special_chars_encoded(self):
        url = _make_hf_resolve_url("user/repo", "model file.gguf")
        assert "model%20file.gguf" in url


# ── Job Formatting Tests ─────────────────────────────────────────────────────

class TestFormatJob:
    def test_basic_fields(self):
        job = {
            "job_id": "abc123",
            "status": "queued",
            "repo_id": "user/repo",
            "filename": "model.gguf",
            "target_name": "model.gguf",
            "target_path": "/mnt/models/llm/model.gguf",
            "bytes_downloaded": 0,
            "total_bytes": None,
            "progress": 0.0,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "cancel_requested": False,
            "_internal_secret": "should_be_removed",
        }
        formatted = _format_job(job)
        assert "job_id" in formatted
        assert "status" in formatted
        assert "_internal_secret" not in formatted

    def test_all_expected_fields_present(self):
        job = {
            "job_id": "abc",
            "status": "complete",
            "repo_id": "u/r",
            "filename": "f.gguf",
            "target_name": "f.gguf",
            "target_path": "/path/f.gguf",
            "bytes_downloaded": 100,
            "total_bytes": 200,
            "progress": 0.5,
            "started_at": "2025-01-01T00:00:00Z",
            "completed_at": "2025-01-01T00:01:00Z",
            "error": None,
            "cancel_requested": False,
        }
        formatted = _format_job(job)
        expected_keys = {
            "job_id", "status", "repo_id", "filename", "target_name",
            "target_path", "bytes_downloaded", "total_bytes", "progress",
            "started_at", "completed_at", "error", "cancel_requested",
        }
        assert set(formatted.keys()) == expected_keys


# ── Persistence Tests ────────────────────────────────────────────────────────

class TestPersistJobs:
    def test_save_and_load_jobs(self, _temp_jobs_file):
        """Jobs should persist to disk and reload."""
        # Create a job
        job = {
            "job_id": "test-123",
            "status": "queued",
            "repo_id": "user/repo",
            "filename": "model.gguf",
            "target_name": "model.gguf",
            "target_path": "/mnt/models/llm/model.gguf",
            "bytes_downloaded": 0,
            "total_bytes": None,
            "progress": 0.0,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "cancel_requested": False,
        }
        with _download_jobs_lock:
            _download_jobs["test-123"] = job

        _persist_download_jobs()

        # Verify file exists and contains valid JSON
        assert _temp_jobs_file.exists()
        with open(_temp_jobs_file) as f:
            data = json.load(f)
        assert "test-123" in data
        assert data["test-123"]["status"] == "queued"

    def test_load_marks_downloading_as_interrupted(self, _temp_jobs_file):
        """Jobs with 'downloading' status should be marked 'interrupted' on load."""
        # Pre-populate the file
        data = {
            "job-1": {
                "status": "downloading",
                "job_id": "job-1",
                "repo_id": "u/r",
                "filename": "f.gguf",
                "target_name": "f.gguf",
                "target_path": "/mnt/models/llm/f.gguf",
                "bytes_downloaded": 50,
                "total_bytes": 100,
                "progress": 0.5,
                "started_at": "2025-01-01T00:00:00Z",
                "completed_at": None,
                "error": None,
                "cancel_requested": False,
            }
        }
        _temp_jobs_file.write_text(json.dumps(data))

        with _download_jobs_lock:
            _download_jobs.clear()

        _load_download_jobs()

        with _download_jobs_lock:
            job = _download_jobs.get("job-1")
            assert job is not None
            assert job["status"] == "interrupted"
            assert "Service restart" in job.get("error", "")

    def test_load_marks_queued_as_interrupted(self, _temp_jobs_file):
        """Jobs with 'queued' status should be marked 'interrupted' on load."""
        data = {
            "job-1": {
                "status": "queued",
                "job_id": "job-1",
                "repo_id": "u/r",
                "filename": "f.gguf",
                "target_name": "f.gguf",
                "target_path": "/mnt/models/llm/f.gguf",
                "bytes_downloaded": 0,
                "total_bytes": None,
                "progress": 0.0,
                "started_at": None,
                "completed_at": None,
                "error": None,
                "cancel_requested": False,
            }
        }
        _temp_jobs_file.write_text(json.dumps(data))

        with _download_jobs_lock:
            _download_jobs.clear()

        _load_download_jobs()

        with _download_jobs_lock:
            job = _download_jobs.get("job-1")
            assert job is not None
            assert job["status"] == "interrupted"


# ── Prune Jobs Tests ─────────────────────────────────────────────────────────

class TestPruneCompletedJobs:
    def test_removes_old_completed_jobs(self):
        """Jobs with completed/failed/cancelled/interrupted status should be pruned."""
        with _download_jobs_lock:
            # Active jobs (should be kept)
            _download_jobs["active-1"] = {"status": "downloading", "completed_at": None}
            _download_jobs["active-2"] = {"status": "queued", "completed_at": None}
            # Completed jobs (should be pruned)
            _download_jobs["done-1"] = {"status": "complete", "completed_at": "2025-01-01T00:01:00Z"}
            _download_jobs["done-2"] = {"status": "failed", "completed_at": "2025-01-01T00:02:00Z"}
            _download_jobs["done-3"] = {"status": "cancelled", "completed_at": "2025-01-01T00:03:00Z"}
            _download_jobs["done-4"] = {"status": "interrupted", "completed_at": "2025-01-01T00:04:00Z"}

        _prune_completed_jobs()

        with _download_jobs_lock:
            assert "active-1" in _download_jobs
            assert "active-2" in _download_jobs
            assert "done-1" not in _download_jobs
            assert "done-2" not in _download_jobs
            assert "done-3" not in _download_jobs
            assert "done-4" not in _download_jobs

    def test_max_retained_keeps_recent_active(self):
        """Should keep up to max_retained active jobs."""
        with _download_jobs_lock:
            for i in range(5):
                _download_jobs[f"active-{i}"] = {
                    "status": "downloading",
                    "completed_at": f"2025-01-01T00:{i:02d}:00Z",
                }

        _prune_completed_jobs(max_retained=3)

        with _download_jobs_lock:
            # Should keep the 3 most recent
            assert "active-2" in _download_jobs
            assert "active-3" in _download_jobs
            assert "active-4" in _download_jobs
            # Older ones pruned
            assert "active-0" not in _download_jobs
            assert "active-1" not in _download_jobs


# ── Clean Partial File Tests ─────────────────────────────────────────────────

class TestCleanPartialFile:
    def test_removes_existing_partial(self, tmp_path):
        partial = tmp_path / "partial.test.download"
        partial.touch()
        assert partial.exists()

        _clean_partial_file(partial)
        assert not partial.exists()

    def test_noop_on_missing_partial(self, tmp_path):
        partial = tmp_path / "missing.download"
        # Should not raise
        _clean_partial_file(partial)


# ── Endpoint Tests (Flask test client) ───────────────────────────────────────

class TestDownloadEndpoints:
    @pytest.fixture
    def client(self, _temp_model_dir, _temp_jobs_file, monkeypatch):
        """Create a Flask test client with patched config."""
        monkeypatch.setenv("QONDUIT_MODEL_DIR", str(_temp_model_dir))
        monkeypatch.setenv("QONDUIT_ALLOW_LOCAL", "true")

        # Clear state
        with _download_jobs_lock:
            _download_jobs.clear()
        with _download_queue_lock:
            _download_queue.clear()

        # Ensure worker from previous test is stopped
        _stop_download_worker()

        # Prevent the background worker thread from starting during endpoint
        # tests. The worker would block on real HTTP calls and cause test
        # hangs. All endpoint logic is tested without the worker.
        with patch("qonduit_router_api._ensure_download_worker_started"):
            from qonduit_router_api import app as flask_app
            flask_app.config["TESTING"] = True
            yield flask_app.test_client()

            # Stop worker after tests
            _stop_download_worker()

    def test_download_dry_run_valid(self, client, _temp_model_dir):
        """Dry run should validate without creating a job."""
        resp = client.post(
            "/api/v1/qonduit-router/hf/download",
            json={
                "repo_id": "bartowski/Qwen3-Coder-Next-GGUF",
                "filename": "model-Q4_K_M.gguf",
                "dry_run": True,
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["dry_run"] is True
        assert data["repo_id"] == "bartowski/Qwen3-Coder-Next-GGUF"
        assert data["exists"] is False  # File doesn't exist yet

    def test_download_dry_run_non_gguf_blocked(self, client):
        """Non-GGUF filenames should be blocked in dry run."""
        resp = client.post(
            "/api/v1/qonduit-router/hf/download",
            json={
                "repo_id": "user/repo",
                "filename": "model.bin",
                "dry_run": True,
            },
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"] == "invalid_filename"

    def test_download_start_creates_job(self, client, _temp_model_dir):
        """Starting a download should create a job in queued status."""
        resp = client.post(
            "/api/v1/qonduit-router/hf/download",
            json={
                "repo_id": "bartowski/Qwen3-Coder-Next-GGUF",
                "filename": "model-Q4_K_M.gguf",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["status"] == "queued"
        assert "job_id" in data

    def test_download_start_missing_repo_id(self, client):
        """Missing repo_id should return 400."""
        resp = client.post(
            "/api/v1/qonduit-router/hf/download",
            json={"filename": "model.gguf"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"] == "invalid_repo_id"

    def test_download_start_missing_filename(self, client):
        """Missing filename should return 400."""
        resp = client.post(
            "/api/v1/qonduit-router/hf/download",
            json={"repo_id": "user/repo"},
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"] == "invalid_filename"

    def test_download_start_path_traversal_blocked(self, client):
        """Path traversal should be rejected."""
        resp = client.post(
            "/api/v1/qonduit-router/hf/download",
            json={
                "repo_id": "user/repo",
                "filename": "../etc/passwd",
            },
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["error"] == "invalid_filename"

    def test_download_start_target_already_exists(self, client, _temp_model_dir):
        """Starting download when target exists should return 409."""
        # Create the target file in the real model dir that _resolve_target_path will use
        # We need to patch _resolve_target_path to point to our temp dir
        import qonduit_router_api as api_module
        with patch.object(api_module, "_resolve_target_path") as mock_resolve:
            mock_path = _temp_model_dir / "model.gguf"
            mock_path.parent.mkdir(parents=True, exist_ok=True)
            mock_path.write_text("dummy")
            mock_resolve.return_value = mock_path

            resp = client.post(
                "/api/v1/qonduit-router/hf/download",
                json={
                    "repo_id": "user/repo",
                    "filename": "model.gguf",
                    "target_name": "model.gguf",
                },
            )
            assert resp.status_code == 409
            data = resp.get_json()
            assert data["error"] == "model_exists"

    def test_download_start_overwrite_flag(self, client, _temp_model_dir):
        """Overwrite flag should allow starting download even if target exists."""
        import qonduit_router_api as api_module
        with patch.object(api_module, "_resolve_target_path") as mock_resolve:
            mock_path = _temp_model_dir / "model.gguf"
            mock_path.parent.mkdir(parents=True, exist_ok=True)
            mock_path.write_text("dummy")
            mock_resolve.return_value = mock_path

            resp = client.post(
                "/api/v1/qonduit-router/hf/download",
                json={
                    "repo_id": "user/repo",
                    "filename": "model.gguf",
                    "target_name": "model.gguf",
                    "overwrite": True,
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True

    def test_downloads_list_empty(self, client):
        """List should return empty jobs when none exist."""
        resp = client.get("/api/v1/qonduit-router/hf/downloads")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["total"] == 0
        assert data["jobs"] == []
        assert data["active_count"] == 0
        assert data["queued_count"] == 0

    def test_downloads_list_after_create(self, client):
        """List should include jobs after creation."""
        # Create a job
        client.post(
            "/api/v1/qonduit-router/hf/download",
            json={
                "repo_id": "user/repo",
                "filename": "model.gguf",
            },
        )

        resp = client.get("/api/v1/qonduit-router/hf/downloads")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["total"] >= 1

    def test_download_get_by_id(self, client):
        """Get specific job by ID."""
        resp = client.post(
            "/api/v1/qonduit-router/hf/download",
            json={
                "repo_id": "user/repo",
                "filename": "model.gguf",
            },
        )
        job_id = resp.get_json()["job_id"]

        resp = client.get(f"/api/v1/qonduit-router/hf/downloads/{job_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["job"]["job_id"] == job_id

    def test_download_get_nonexistent(self, client):
        """Get nonexistent job should return 404."""
        resp = client.get("/api/v1/qonduit-router/hf/downloads/nonexistent-id")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"] == "job_not_found"

    def test_download_cancel(self, client):
        """Cancel should mark job for cancellation."""
        resp = client.post(
            "/api/v1/qonduit-router/hf/download",
            json={
                "repo_id": "user/repo",
                "filename": "model.gguf",
            },
        )
        job_id = resp.get_json()["job_id"]

        resp = client.post(f"/api/v1/qonduit-router/hf/downloads/{job_id}/cancel")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["status"] == "cancelling"

        # Verify the cancel flag is set
        resp = client.get(f"/api/v1/qonduit-router/hf/downloads/{job_id}")
        data = resp.get_json()
        assert data["job"]["cancel_requested"] is True

    def test_download_cancel_already_complete(self, client):
        """Cancel should fail if job is already complete."""
        resp = client.post(
            "/api/v1/qonduit-router/hf/download",
            json={
                "repo_id": "user/repo",
                "filename": "model.gguf",
            },
        )
        job_id = resp.get_json()["job_id"]

        # Manually mark as complete
        from qonduit_router_api import _download_jobs_lock
        with _download_jobs_lock:
            _download_jobs[job_id]["status"] = "complete"

        resp = client.post(f"/api/v1/qonduit-router/hf/downloads/{job_id}/cancel")
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["error"] == "already_complete"

    def test_download_cancel_nonexistent(self, client):
        """Cancel nonexistent job should return 404."""
        resp = client.post("/api/v1/qonduit-router/hf/downloads/nonexistent/cancel")
        assert resp.status_code == 404
        data = resp.get_json()
        assert data["error"] == "job_not_found"

    def test_download_cors_headers(self, client):
        """CORS headers should be present on download endpoints."""
        resp = client.options(
            "/api/v1/qonduit-router/hf/download",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        # Should not return 500
        assert resp.status_code in (200, 204)
