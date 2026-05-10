"""Comprehensive tests for the RagRegistry layer.

Tests cover:
- Project CRUD (create, get, update, delete, list)
- Collection CRUD (create, get, update, delete, list)
- Persistence across restarts (reload)
- Duplicate/idempotent creation
- Invalid name rejection
- Safe delete behavior (default project/collection protection)
- Upload integration with project/collection metadata
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.rag_registry import (
    RagRegistry,
    _load_registry,
    _save_registry,
    _now_iso,
    validate_project_id,
    validate_collection_name,
    get_registry,
    reset_registry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_registry_path(tmp_path: Path) -> Path:
    """Return a path for a temporary registry file."""
    return tmp_path / "rag_registry.json"


@pytest.fixture()
def registry(tmp_registry_path: Path) -> RagRegistry:
    """Create a fresh RagRegistry backed by a temporary file."""
    return RagRegistry(path=str(tmp_registry_path))


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class TestValidateProjectId:
    def test_valid_lowercase_alphanumeric(self) -> None:
        assert validate_project_id("default") == "default"
        assert validate_project_id("my-project") == "my-project"
        assert validate_project_id("test_123") == "test_123"
        assert validate_project_id("a") == "a"

    def test_uppercase_normalized(self) -> None:
        assert validate_project_id("MyProject") == "myproject"
        assert validate_project_id("MY-PROJECT") == "my-project"
        assert validate_project_id("Test_Project") == "test_project"

    def test_spaces_becomes_hyphens(self) -> None:
        assert validate_project_id("my project") == "my-project"
        # Double spaces produce double hyphens (implementation detail)
        assert validate_project_id("  test  project  ") == "test--project"

    def test_invalid_characters_sanitized(self) -> None:
        assert validate_project_id("my.project") == "myproject"
        assert validate_project_id("test@#") == "test"

    def test_allows_leading_digit(self) -> None:
        # The regex ^[a-z0-9] allows digits as first character
        assert validate_project_id("123project") == "123project"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="project_id is required"):
            validate_project_id("")
        with pytest.raises(ValueError, match="must contain at least one valid"):
            validate_project_id("   ")
        with pytest.raises(ValueError, match="project_id is required"):
            validate_project_id(None)

    def test_rejects_too_long(self) -> None:
        long_id = "a" * 200
        with pytest.raises(ValueError, match="at most 128"):
            validate_project_id(long_id)

    def test_rejects_leading_special_char(self) -> None:
        with pytest.raises(ValueError, match="must start with"):
            validate_project_id("-invalid")
        with pytest.raises(ValueError, match="must start with"):
            validate_project_id("_invalid")


class TestValidateCollectionName:
    def test_valid(self) -> None:
        assert validate_collection_name("default") == "default"
        assert validate_collection_name("work") == "work"
        assert validate_collection_name("checkbook") == "checkbook"

    def test_uppercase_normalized(self) -> None:
        assert validate_collection_name("Work") == "work"

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="collection name is required"):
            validate_collection_name("")

    def test_rejects_leading_special(self) -> None:
        with pytest.raises(ValueError, match="must start with"):
            validate_collection_name("-invalid")


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


class TestProjectCreate:
    def test_create_default_project(self, registry: RagRegistry) -> None:
        project, created, warnings = registry.ensure_project(project_id="default")
        assert created is True
        assert project["project_id"] == "default"
        assert project["qdrant_collection"] == "qonduit_rag__default"
        assert project["default_collection"] == "default"
        assert "created_at" in project
        assert "updated_at" in project

    def test_create_custom_project(self, registry: RagRegistry) -> None:
        project, created, _ = registry.ensure_project(
            project_id="acme",
            display_name="Acme Corp",
            description="My test project",
        )
        assert created is True
        assert project["display_name"] == "Acme Corp"
        assert project["description"] == "My test project"

    def test_create_project_with_metadata(self, registry: RagRegistry) -> None:
        # Metadata is set on update, not creation (current implementation behavior)
        registry.ensure_project(project_id="test")
        registry.update_project(
            "test",
            metadata={"owner": "alice", "region": "us-east"},
        )
        result = registry.get_project("test")
        assert result["project"]["metadata"] == {"owner": "alice", "region": "us-east"}

    def test_create_project_creates_qdrant_when_flagged(
        self, registry: RagRegistry
    ) -> None:
        with patch("app.rag_registry.qdrant") as mock_qdrant:
            mock_qdrant.get_collection.side_effect = Exception("not found")
            mock_qdrant.create_collection.return_value = None
            project, created, _ = registry.ensure_project(
                project_id="with-qdrant",
                ensure_qdrant=True,
            )
            mock_qdrant.create_collection.assert_called_once()
            assert created is True

    def test_create_project_with_qdrant_failure_warns(
        self, registry: RagRegistry
    ) -> None:
        # When ensure_qdrant=True and Qdrant creation fails,
        # the project is still created but a warning is appended
        with patch("app.rag_registry.qdrant") as mock_qdrant:
            # get_collection raises -> triggers create_collection
            # create_collection raises -> triggers the outer catch -> warning
            mock_qdrant.get_collection.side_effect = Exception("not found")
            mock_qdrant.create_collection.side_effect = Exception("disk full")
            _, _, warnings = registry.ensure_project(
                project_id="qdrant-fail",
                ensure_qdrant=True,
            )
            assert len(warnings) == 1
            assert "qdrant_collection_create_failed" in warnings[0]


class TestProjectCreateIdempotent:
    def test_duplicate_project_returns_existing(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="dup")
        _, created1, _ = registry.ensure_project(project_id="dup")
        assert created1 is False

    def test_idempotent_creation_updates_fields(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="dup", description="initial")
        updated, created, _ = registry.ensure_project(
            project_id="dup", description="updated"
        )
        assert created is False
        assert updated["description"] == "updated"

    def test_idempotent_creation_adds_metadata(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="meta-dup")
        updated, created, _ = registry.ensure_project(
            project_id="meta-dup", metadata={"key": "val"}
        )
        assert created is False
        assert updated["metadata"] == {"key": "val"}


class TestProjectList:
    def test_list_empty(self, registry: RagRegistry) -> None:
        with patch("app.rag_registry._discover_qdrant_collections", return_value=[]):
            with patch("app.rag_registry._try_get_collection_info", return_value={"exists": False, "points_count": 0}):
                projects = registry.list_projects()
                assert projects == []

    def test_list_multiple_projects(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="alpha")
        registry.ensure_project(project_id="beta")
        projects = registry.list_projects()
        ids = [p["project_id"] for p in projects]
        assert "alpha" in ids
        assert "beta" in ids

    def test_list_includes_qdrant_stats(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="stats-test")
        projects = registry.list_projects()
        entry = next(p for p in projects if p["project_id"] == "stats-test")
        assert "points_count" in entry
        assert "exists_in_qdrant" in entry


class TestProjectGet:
    def test_get_existing_project(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="get-me")
        result = registry.get_project("get-me")
        assert result["ok"] is True
        assert result["project"]["project_id"] == "get-me"
        assert "qdrant" in result

    def test_get_nonexistent_project(self, registry: RagRegistry) -> None:
        with pytest.raises(ValueError, match="Project not found"):
            registry.get_project("no-such-project")

    def test_get_includes_collections(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="coll-test")
        result = registry.get_project("coll-test")
        assert "collections" in result["project"]
        assert result["project"]["collections_count"] >= 1


class TestProjectUpdate:
    def test_update_display_name(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="upd")
        result = registry.update_project("upd", display_name="New Name")
        assert result["project"]["display_name"] == "New Name"

    def test_update_description(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="upd")
        result = registry.update_project("upd", description="A description")
        assert result["project"]["description"] == "A description"

    def test_update_default_collection(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="upd")
        result = registry.update_project("upd", default_collection="work")
        assert result["project"]["default_collection"] == "work"

    def test_update_nonexistent_project(self, registry: RagRegistry) -> None:
        with pytest.raises(ValueError, match="Project not found"):
            registry.update_project("ghost", display_name="x")

    def test_update_metadata(self, registry: RagRegistry) -> None:
        # Metadata is set on update (not creation), so first call sets "a"
        registry.ensure_project(project_id="upd")
        registry.update_project("upd", metadata={"a": 1})
        result = registry.update_project("upd", metadata={"b": 2})
        assert result["project"]["metadata"] == {"a": 1, "b": 2}


class TestProjectDelete:
    def test_delete_project(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="del-me")
        result = registry.delete_project("del-me")
        assert result["ok"] is True
        assert result["deleted"] is True

    def test_delete_nonexistent(self, registry: RagRegistry) -> None:
        with pytest.raises(ValueError, match="Project not found"):
            registry.delete_project("no-such")

    def test_cannot_delete_default_without_force(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="default")
        with pytest.raises(PermissionError, match="default"):
            registry.delete_project("default")

    def test_delete_default_with_force(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="default")
        result = registry.delete_project("default", force=True)
        assert result["ok"] is True

    def test_delete_project_with_qdrant(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="qdrant-del", ensure_qdrant=True)
        # Even if Qdrant exists, delete should work without force for non-default
        result = registry.delete_project("qdrant-del")
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# Collection CRUD
# ---------------------------------------------------------------------------


class TestCollectionCreate:
    def test_create_collection(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="coll-proj")
        coll, created = registry.create_collection("coll-proj", "work")
        assert created is True
        assert coll["name"] == "work"
        assert coll["display_name"] == "Work"

    def test_create_collection_with_display_name(
        self, registry: RagRegistry
    ) -> None:
        registry.ensure_project(project_id="coll-proj")
        coll, _ = registry.create_collection(
            "coll-proj", "my-coll", display_name="My Collection"
        )
        assert coll["display_name"] == "My Collection"

    def test_create_collection_requires_project(
        self, registry: RagRegistry
    ) -> None:
        with pytest.raises(ValueError, match="Project not found"):
            registry.create_collection("ghost", "work")

    def test_create_default_collection_on_project_creation(
        self, registry: RagRegistry
    ) -> None:
        registry.ensure_project(project_id="auto-coll")
        collections = registry.list_collections("auto-coll")
        names = [c["name"] for c in collections]
        assert "default" in names

    def test_idempotent_collection_creation(
        self, registry: RagRegistry
    ) -> None:
        registry.ensure_project(project_id="idem-proj")
        _, created1 = registry.create_collection("idem-proj", "work")
        _, created2 = registry.create_collection("idem-proj", "work")
        assert created1 is True
        assert created2 is False


class TestCollectionList:
    def test_list_collections(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="list-proj")
        registry.create_collection("list-proj", "work")
        registry.create_collection("list-proj", "archive")
        collections = registry.list_collections("list-proj")
        names = [c["name"] for c in collections]
        assert "default" in names
        assert "work" in names
        assert "archive" in names

    def test_list_collections_nonexistent_project(
        self, registry: RagRegistry
    ) -> None:
        with pytest.raises(ValueError, match="Project not found"):
            registry.list_collections("ghost")


class TestCollectionGet:
    def test_get_collection(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="get-proj")
        registry.create_collection("get-proj", "work", description="Work stuff")
        result = registry.get_collection("get-proj", "work")
        assert result["ok"] is True
        assert result["collection"]["description"] == "Work stuff"

    def test_get_nonexistent_collection(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="get-proj")
        with pytest.raises(ValueError, match="Collection not found"):
            registry.get_collection("get-proj", "nope")


class TestCollectionUpdate:
    def test_update_display_name(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="upd-proj")
        registry.create_collection("upd-proj", "work")
        result = registry.update_collection(
            "upd-proj", "work", display_name="Updated Work"
        )
        assert result["collection"]["display_name"] == "Updated Work"

    def test_update_metadata(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="upd-proj")
        registry.create_collection("upd-proj", "work")
        result = registry.update_collection(
            "upd-proj", "work", metadata={"owner": "bob"}
        )
        assert result["collection"]["metadata"] == {"owner": "bob"}

    def test_update_nonexistent_collection(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="upd-proj")
        with pytest.raises(ValueError, match="Collection not found"):
            registry.update_collection("upd-proj", "ghost", display_name="x")


class TestCollectionDelete:
    def test_delete_collection(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="del-proj")
        registry.create_collection("del-proj", "temp")
        result = registry.delete_collection("del-proj", "temp")
        assert result["ok"] is True

    def test_cannot_delete_default_without_force(
        self, registry: RagRegistry
    ) -> None:
        registry.ensure_project(project_id="del-proj")
        with pytest.raises(PermissionError, match="default"):
            registry.delete_collection("del-proj", "default")

    def test_delete_default_with_force(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="del-proj")
        result = registry.delete_collection("del-proj", "default", force=True)
        assert result["ok"] is True

    def test_delete_nonexistent_collection(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="del-proj")
        with pytest.raises(ValueError, match="Collection not found"):
            registry.delete_collection("del-proj", "ghost")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_persist_and_reload(self, tmp_registry_path: Path) -> None:
        # Create and persist
        reg1 = RagRegistry(path=tmp_registry_path)
        reg1.ensure_project(project_id="persist-me", description="Persistent")
        reg1._persist()

        # Verify file exists and is valid JSON
        assert tmp_registry_path.exists()
        raw = tmp_registry_path.read_text()
        data = json.loads(raw)
        assert "persist-me" in data["projects"]

        # Create new instance from same file
        reg2 = RagRegistry(path=tmp_registry_path)
        reg2.reload()
        result = reg2.get_project("persist-me")
        assert result["project"]["description"] == "Persistent"

    def test_reload_updates_in_memory(self, registry: RagRegistry) -> None:
        registry.ensure_project(project_id="reload-test")
        # Simulate external modification
        registry._data["projects"]["reload-test"]["description"] = "modified"
        registry._persist()
        # New instance should pick up the change
        registry2 = RagRegistry(path=registry._path)
        registry2.reload()
        result = registry2.get_project("reload-test")
        assert result["project"]["description"] == "modified"

    def test_corrupt_registry_recovered(self, tmp_registry_path: Path) -> None:
        tmp_registry_path.write_text("not valid json {{{")
        reg = RagRegistry(path=tmp_registry_path)
        # Should not crash; should create fresh registry
        registry_data = reg._data
        assert "projects" in registry_data
        assert isinstance(registry_data["projects"], dict)


# ---------------------------------------------------------------------------
# Registry singleton
# ---------------------------------------------------------------------------


class TestRegistrySingleton:
    def teardown_method(self, method: object) -> None:
        reset_registry()

    def test_singleton_returns_same_instance(self) -> None:
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_reset_clears_singleton(self) -> None:
        r1 = get_registry()
        reset_registry()
        r2 = get_registry()
        assert r1 is not r2


# ---------------------------------------------------------------------------
# Upload integration
# ---------------------------------------------------------------------------


class TestUploadIntegration:
    """Verify document upload flow registers project/collection in registry."""

    def test_ingest_registers_project_and_collection(self) -> None:
        """When a document is ingested, the project and collection are
        registered in the RagRegistry."""
        reset_registry()
        from app.documents import ingest_text_document

        async def _run() -> MagicMock:
            with patch("app.documents.get_registry") as mock_get_reg:
                mock_reg = MagicMock()
                mock_get_reg.return_value = mock_reg

                with patch("app.documents.chunk_text", return_value=["chunk1"]):
                    with patch("app.documents.embedding_backend.embed_query", return_value=[0.1] * 768):
                        with patch("app.documents.qdrant.upsert"):
                            with patch("app.documents.rag_service.ensure_collection"):
                                with patch("app.documents._store.save_source") as mock_save_source:
                                    mock_save_source.return_value = Path("/tmp/source.txt")
                                    with patch("app.documents._store.save_metadata"):
                                        await ingest_text_document(
                                            project_id="upload-test",
                                            collection="work",
                                            document_name="test.txt",
                                            text="Hello world",
                                            metadata={"file_type": "txt"},
                                            source="file_upload",
                                            saved_path="/tmp/source.txt",
                                        )
                                        return mock_reg

        mock_reg = asyncio.run(_run())
        # verify registry was called
        mock_reg.ensure_collection_exists.assert_called_once_with(
            "upload-test", "work"
        )

    def test_ingest_text_registers_project_and_collection(self) -> None:
        """Text upload also registers project/collection."""
        reset_registry()
        from app.documents import ingest_text_document

        async def _run() -> MagicMock:
            with patch("app.documents.get_registry") as mock_get_reg:
                mock_reg = MagicMock()
                mock_get_reg.return_value = mock_reg

                with patch("app.documents.chunk_text", return_value=["chunk1"]):
                    with patch("app.documents.embedding_backend.embed_query", return_value=[0.1] * 768):
                        with patch("app.documents.qdrant.upsert"):
                            with patch("app.documents.rag_service.ensure_collection"):
                                with patch("app.documents._store.save_source") as mock_save_source:
                                    mock_save_source.return_value = Path("/tmp/source.txt")
                                    with patch("app.documents._store.save_metadata"):
                                        await ingest_text_document(
                                            project_id="txt-test",
                                            collection="archive",
                                            document_name="note.txt",
                                            text="Some notes",
                                            metadata={"file_type": "txt"},
                                            source="text_upload",
                                            saved_path="/tmp/source.txt",
                                        )
                                        return mock_reg

        mock_reg = asyncio.run(_run())
        mock_reg.ensure_collection_exists.assert_called_once_with(
            "txt-test", "archive"
        )

    def test_ingest_can_skip_registry(self) -> None:
        """When ensure_registry=False, registry is not called."""
        reset_registry()
        from app.documents import ingest_text_document

        async def _run() -> MagicMock:
            with patch("app.documents.get_registry") as mock_get_reg:
                mock_reg = MagicMock()
                mock_get_reg.return_value = mock_reg

                with patch("app.documents.chunk_text", return_value=["chunk1"]):
                    with patch("app.documents.embedding_backend.embed_query", return_value=[0.1] * 768):
                        with patch("app.documents.qdrant.upsert"):
                            with patch("app.documents.rag_service.ensure_collection"):
                                with patch("app.documents._store.save_source") as mock_save_source:
                                    mock_save_source.return_value = Path("/tmp/source.txt")
                                    with patch("app.documents._store.save_metadata"):
                                        await ingest_text_document(
                                            project_id="no-reg",
                                            collection="work",
                                            document_name="test.txt",
                                            text="Hello",
                                            metadata={"file_type": "txt"},
                                            source="file_upload",
                                            saved_path="/tmp/source.txt",
                                            ensure_registry=False,
                                        )
                                        return mock_get_reg

        mock_get_reg = asyncio.run(_run())
        mock_get_reg.assert_not_called()
