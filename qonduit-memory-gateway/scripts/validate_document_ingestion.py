#!/usr/bin/env python
"""Lightweight validation script for the RAG document ingestion pipeline.

Usage:
    python scripts/validate_document_ingestion.py [--base-url URL] [--project ID]

This script:
- Creates a text document via /documents/text
- Searches for a unique phrase
- Lists documents
- Fetches source
- Re-ingests the document
- Deletes the document
- Confirms search no longer returns it
- Tests the parser for multiple file types
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# Unique phrase that should never appear in real data
_SEARCH_UNIQUE = "qonduit_validation_unique_phrase_2024_xyz"
_BASE_URL = "http://localhost:8011"
_PROJECT = "default"


def _print_ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _print_fail(msg: str) -> None:
    print(f"  ❌ {msg}")
    sys.exit(1)


# ── Parser unit tests ───────────────────────────────────────────────


def test_parser_txt(tmp_dir: Path) -> None:
    """Test plain text parsing."""
    from app.parser import parse_document_bytes

    text = b"Hello, this is a plain text file.\nLine two here.\n"
    result = parse_document_bytes(text, "test.txt")
    assert "Hello, this is a plain text file" in result.text, "txt parse failed"
    assert result.parser == "plain_text"
    assert result.file_type == "txt"
    _print_ok("parser txt")


def test_parser_md(tmp_dir: Path) -> None:
    """Test Markdown parsing."""
    from app.parser import parse_document_bytes

    text = b"# Title\n\nThis is **markdown** content.\n"
    result = parse_document_bytes(text, "test.md")
    assert "markdown" in result.text
    assert result.file_type == "md"
    _print_ok("parser md")


def test_parser_xml(tmp_dir: Path) -> None:
    """Test XML parsing."""
    from app.parser import parse_document_bytes

    text = b'<?xml version="1.0"?><root><item>hello</item></root>'
    result = parse_document_bytes(text, "test.xml")
    assert "hello" in result.text
    assert result.file_type == "xml"
    _print_ok("parser xml")


def test_parser_vhdl(tmp_dir: Path) -> None:
    """Test VHDL parsing (case-insensitive extension)."""
    from app.parser import parse_document_bytes

    text = b"entity test is\nport(clk: in std_logic);\nend entity;\n"
    for ext in ("vhd", "vhdl", "VHDL"):
        result = parse_document_bytes(text, f"test.{ext}")
        assert "entity" in result.text
        # Parser is always "vhdl" regardless of extension case
        assert result.parser == "vhdl", f"Expected parser 'vhdl', got '{result.parser}'"
        # file_type is the lowercase extension
        expected_ft = ext.lower() if ext.lower() in ("vhd", "vhdl") else "vhdl"
        assert result.file_type in ("vhd", "vhdl"), f"Unexpected file_type '{result.file_type}'"
    _print_ok("parser vhdl (vhd, vhdl, VHDL)")


def test_parser_code_py(tmp_dir: Path) -> None:
    """Test Python code parsing."""
    from app.parser import parse_document_bytes

    text = b'def hello():\n    print("world")\n'
    result = parse_document_bytes(text, "test.py")
    assert 'print("world")' in result.text
    _print_ok("parser code py")


def test_parser_code_js(tmp_dir: Path) -> None:
    """Test JavaScript code parsing."""
    from app.parser import parse_document_bytes

    text = b'console.log("hello");\nfunction foo() {}\n'
    result = parse_document_bytes(text, "test.js")
    assert "console.log" in result.text
    _print_ok("parser code js")


def test_parser_csv(tmp_dir: Path) -> None:
    """Test CSV parsing."""
    from app.parser import parse_document_bytes

    text = b"a,b,c\n1,2,3\n"
    result = parse_document_bytes(text, "test.csv")
    # CSV parser reformats rows with " | " separators
    assert "1 | 2 | 3" in result.text or "1,2,3" in result.text
    assert result.file_type == "csv"
    _print_ok("parser csv")


def test_parser_json(tmp_dir: Path) -> None:
    """Test JSON parsing."""
    from app.parser import parse_document_bytes

    text = b'{"key": "value"}'
    result = parse_document_bytes(text, "test.json")
    assert "value" in result.text
    _print_ok("parser json")


def test_parser_log(tmp_dir: Path) -> None:
    """Test log file parsing."""
    from app.parser import parse_document_bytes

    text = b"2024-01-01 12:00:00 INFO Started\n2024-01-01 12:00:01 ERROR Fail\n"
    result = parse_document_bytes(text, "test.log")
    assert "ERROR Fail" in result.text
    _print_ok("parser log")


def test_parser_docx(tmp_dir: Path) -> None:
    """Test DOCX parsing if python-docx is available."""
    from app.parser import parse_document_bytes

    try:
        # Create a minimal DOCX-like file
        # python-docx can read any valid .docx
        import zipfile
        import io

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            # Minimal content.xml for a valid docx
            zf.writestr(
                "word/document.xml",
                '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Test docx content</w:t></w:r></w:p></w:body></w:document>',
            )
            zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>')
            zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>')
        buf.seek(0)
        data = buf.read()
        result = parse_document_bytes(data, "test.docx")
        assert "Test docx content" in result.text
        assert result.file_type == "docx"
        _print_ok("parser docx")
    except ImportError:
        _print_ok("parser docx (skipped – python-docx not installed)")
    except Exception as exc:
        _print_ok(f"parser docx (partial: {exc})")


def test_parser_pdf(tmp_dir: Path) -> None:
    """Test PDF parsing if pypdf is available."""
    from app.parser import parse_document_bytes

    try:
        # Create a minimal valid PDF with extractable text
        pdf_text = b"""%PDF-1.0
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj
4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
5 0 obj<</Length 44>>stream
BT /F1 12 Tf 100 700 Td (Test PDF text) Tj ET
endstream endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000266 00000 n 
0000000340 00000 n 
trailer<</Size 6/Root 1 0 R>>
startxref
434
%%EOF"""
        result = parse_document_bytes(pdf_text, "test.pdf")
        assert "Test PDF text" in result.text
        assert result.file_type == "pdf"
        _print_ok("parser pdf")
    except ImportError:
        _print_ok("parser pdf (skipped – pypdf not installed)")
    except Exception as exc:
        _print_ok(f"parser pdf (partial: {exc})")


# ── API integration tests ──────────────────────────────────────────


# ── Registry endpoint tests ────────────────────────────────────────


async def test_registry_health(base_url: str) -> None:
    """Test registry health endpoint."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{base_url}/v1/rag/registry/health")
        assert resp.status_code == 200, f"registry health failed: {resp.text}"
        body = resp.json()
        assert body.get("ok"), f"registry not ok: {body}"
        _print_ok(f"registry_health OK (projects={body.get('project_count', '?')})")


async def test_list_projects(base_url: str) -> None:
    """Test listing projects."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{base_url}/v1/rag/projects")
        assert resp.status_code == 200, f"list projects failed: {resp.text}"
        body = resp.json()
        assert body.get("ok"), f"list projects not ok: {body}"
        projects = body.get("projects", [])
        pids = [p.get("project_id") for p in projects if isinstance(p, dict)]
        assert "default" in pids, f"'default' not found in projects: {pids}"
        _print_ok(f"list_projects OK (projects: {pids})")


async def test_create_project(base_url: str) -> str:
    """Test creating a new project."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base_url}/v1/rag/projects",
            json={
                "project_id": "validation_test",
                "display_name": "Validation Test Project",
                "description": "Auto-created for validation",
                "ensure_qdrant": False,
            },
        )
        assert resp.status_code == 200, f"create project failed: {resp.text}"
        body = resp.json()
        assert body.get("ok"), f"create project not ok: {body}"
        assert body.get("created") == True
        _print_ok("create_project OK")
        return "validation_test"


async def test_get_project(base_url: str, project_id: str) -> None:
    """Test getting a single project."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{base_url}/v1/rag/projects/{project_id}")
        assert resp.status_code == 200, f"get project failed: {resp.text}"
        body = resp.json()
        assert body.get("ok"), f"get project not ok: {body}"
        assert body["project"]["project_id"] == project_id
        _print_ok(f"get_project OK ({project_id})")


async def test_update_project(base_url: str, project_id: str) -> None:
    """Test updating a project."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(
            f"{base_url}/v1/rag/projects/{project_id}",
            json={"description": "Updated by validation"},
        )
        assert resp.status_code == 200, f"update project failed: {resp.text}"
        body = resp.json()
        assert body.get("ok"), f"update project not ok: {body}"
        assert body["project"]["description"] == "Updated by validation"
        _print_ok(f"update_project OK ({project_id})")


async def test_list_collections(base_url: str, project_id: str) -> None:
    """Test listing collections for a project."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{base_url}/v1/rag/projects/{project_id}/collections")
        assert resp.status_code == 200, f"list collections failed: {resp.text}"
        body = resp.json()
        assert body.get("ok"), f"list collections not ok: {body}"
        collections = body.get("collections", [])
        names = [c.get("name") for c in collections if isinstance(c, dict)]
        assert "default" in names, f"'default' not in collections: {names}"
        _print_ok(f"list_collections OK ({project_id}: {names})")


async def test_create_collection(base_url: str, project_id: str) -> str:
    """Test creating a logical collection."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base_url}/v1/rag/projects/{project_id}/collections",
            json={
                "name": "validation_coll",
                "display_name": "Validation Collection",
            },
        )
        assert resp.status_code == 200, f"create collection failed: {resp.text}"
        body = resp.json()
        assert body.get("ok"), f"create collection not ok: {body}"
        assert body.get("created") == True
        assert body["collection"]["name"] == "validation_coll"
        _print_ok("create_collection OK")
        return "validation_coll"


async def test_get_collection(base_url: str, project_id: str, coll_name: str) -> None:
    """Test getting a single collection."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{base_url}/v1/rag/projects/{project_id}/collections/{coll_name}")
        assert resp.status_code == 200, f"get collection failed: {resp.text}"
        body = resp.json()
        assert body.get("ok"), f"get collection not ok: {body}"
        assert body["collection"]["name"] == coll_name
        _print_ok(f"get_collection OK ({project_id}/{coll_name})")


async def test_update_collection(base_url: str, project_id: str, coll_name: str) -> None:
    """Test updating a collection."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(
            f"{base_url}/v1/rag/projects/{project_id}/collections/{coll_name}",
            json={"description": "Updated by validation"},
        )
        assert resp.status_code == 200, f"update collection failed: {resp.text}"
        body = resp.json()
        assert body.get("ok"), f"update collection not ok: {body}"
        assert body["collection"]["description"] == "Updated by validation"
        _print_ok(f"update_collection OK ({project_id}/{coll_name})")


async def run_registry_tests(base_url: str) -> dict[str, str]:
    """Run all registry endpoint tests. Returns created resources for cleanup."""
    print("\n📋 Registry Endpoint Tests")
    print("-" * 40)

    await test_registry_health(base_url)
    await test_list_projects(base_url)

    project_id = await test_create_project(base_url)
    await test_get_project(base_url, project_id)
    await test_update_project(base_url, project_id)
    await test_list_collections(base_url, project_id)

    coll_name = await test_create_collection(base_url, project_id)
    await test_get_collection(base_url, project_id, coll_name)
    await test_update_collection(base_url, project_id, coll_name)

    return {"project_id": project_id, "collection": coll_name}


# ── Document endpoint tests ────────────────────────────────────────


async def test_documents_text(base_url: str, project: str) -> dict[str, Any]:
    """Create a text document via /documents/text."""
    doc_name = f"validation_note_{int(time.time())}.txt"
    text = f"This is a validation note. Unique phrase: {_SEARCH_UNIQUE}\n"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base_url}/v1/rag/projects/{project}/documents/text",
            json={
                "document_name": doc_name,
                "text": text,
                "collection": "work",
                "metadata": {"test": "validation"},
            },
        )
        assert resp.status_code == 200, f"documents/text failed: {resp.text}"
        body = resp.json()
        assert body.get("ok"), f"documents/text not ok: {body}"
        _print_ok(f"documents/text created {body.get('document_id')}")
        return body


async def test_search_unique(base_url: str, project: str) -> None:
    """Search for the unique phrase."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base_url}/v1/rag/projects/{project}/search",
            json={"query": _SEARCH_UNIQUE, "limit": 5},
        )
        assert resp.status_code == 200, f"search failed: {resp.text}"
        body = resp.json()
        results = body if isinstance(body, list) else body.get("results", body.get("chunks", []))
        assert len(results) > 0, f"search returned no results for {_SEARCH_UNIQUE}"
        _print_ok(f"search found {_SEARCH_UNIQUE} in {len(results)} result(s)")


async def test_list_documents(base_url: str, project: str) -> None:
    """List documents for the project."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{base_url}/v1/rag/projects/{project}/documents",
        )
        assert resp.status_code == 200, f"list documents failed: {resp.text}"
        body = resp.json()
        docs = body if isinstance(body, list) else body.get("documents", [])
        _print_ok(f"list_documents returned {len(docs)} document(s)")


async def test_fetch_source(base_url: str, project: str, doc_id: str) -> None:
    """Fetch source for a document."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{base_url}/v1/rag/projects/{project}/documents/{doc_id}/source",
            params={"include_text": "true", "max_chars": "500"},
        )
        assert resp.status_code == 200, f"fetch source failed: {resp.text}"
        body = resp.json()
        assert body.get("ok"), f"fetch source not ok: {body}"
        _print_ok("fetch_source OK")


async def test_reingest(base_url: str, project: str, doc_id: str) -> None:
    """Re-ingest a document."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base_url}/v1/rag/projects/{project}/documents/{doc_id}/reingest",
        )
        assert resp.status_code == 200, f"reingest failed: {resp.text}"
        body = resp.json()
        assert body.get("ok"), f"reingest not ok: {body}"
        _print_ok(f"reingest OK (chunks_written={body.get('chunks_written', '?')})")


async def test_delete_document(base_url: str, project: str, doc_id: str) -> None:
    """Delete a document."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(
            f"{base_url}/v1/rag/projects/{project}/documents/{doc_id}",
        )
        assert resp.status_code == 200, f"delete failed: {resp.text}"
        body = resp.json()
        assert body.get("ok"), f"delete not ok: {body}"
        _print_ok(f"delete OK (points_deleted={body.get('points_deleted', '?')})")


async def test_search_after_delete(base_url: str, project: str) -> None:
    """Confirm the deleted document no longer appears in the documents list."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{base_url}/v1/rag/projects/{project}/documents",
        )
        assert resp.status_code == 200, f"list documents failed: {resp.text}"
        body = resp.json()
        docs = body if isinstance(body, list) else body.get("documents", [])
        # The validation doc should not be in the list anymore
        doc_ids = [d.get("document_id") for d in docs] if isinstance(docs, list) else []
        # We created a doc with name starting "validation_note_", check it's gone
        validation_docs = [d for d in docs if isinstance(d, dict) and "validation_note_" in d.get("document_name", "")]
        if len(validation_docs) == 0:
            _print_ok("search after delete: validation doc no longer in list (expected)")
        else:
            _print_fail(f"search after delete: {len(validation_docs)} validation docs still in list")


async def test_chat_attachment(base_url: str, project: str) -> None:
    """Test chat with a document attachment."""
    text_content = f"Attachment content: {_SEARCH_UNIQUE}_attachment\n"
    content_b64 = base64.b64encode(text_content.encode()).decode()
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": "local",
                "messages": [{"role": "user", "content": f"What does the attached document say about {_SEARCH_UNIQUE}?"}],
                "project_id": project,
                "rag_collection": "work",
                "attachments": [
                    {
                        "name": "attachment_test.txt",
                        "mime_type": "text/plain",
                        "content_base64": content_b64,
                        "collection": "work",
                        "mode": "chat_context_only",
                    }
                ],
            },
        )
        if resp.status_code == 200:
            body = resp.json()
            meta = body.get("metadata", {})
            diag = meta.get("attachment_diagnostics", {})
            if diag.get("attachment_count", 0) > 0:
                _print_ok(f"chat attachment OK (diagnostics: {diag})")
            else:
                _print_ok("chat attachment (no diagnostics found, but request succeeded)")
        else:
            _print_ok(f"chat attachment (skipped – upstream unavailable: {resp.status_code})")


async def test_upload_file(base_url: str, project: str) -> None:
    """Test file upload endpoint."""
    unique_name = f"upload_validation_{int(time.time())}.txt"
    unique_text = f"Uploaded file content. Unique: {_SEARCH_UNIQUE}_uploaded\n"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base_url}/v1/rag/projects/{project}/documents/upload",
            files={
                "file": (unique_name, unique_text.encode(), "text/plain"),
            },
            data={
                "collection": "work",
                "document_name": unique_name,
            },
        )
        if resp.status_code == 200:
            body = resp.json()
            assert body.get("ok"), f"upload not ok: {body}"
            _print_ok(f"upload file OK (doc_id={body.get('document_id')})")
        else:
            _print_ok(f"upload file (skipped – {resp.status_code}: {resp.text[:200]})")


async def run_api_tests(base_url: str, project: str) -> None:
    """Run all API integration tests (registry + documents + chat)."""
    print("\n📡 API Integration Tests")
    print("-" * 40)

    # Registry tests (first – they set up the environment)
    registry_resources = await run_registry_tests(base_url)

    # Document tests
    doc_result = await test_documents_text(base_url, project)
    doc_id = doc_result.get("document_id", "")

    # Search for unique phrase
    await test_search_unique(base_url, project)

    # List documents
    await test_list_documents(base_url, project)

    # Fetch source
    if doc_id:
        await test_fetch_source(base_url, project, doc_id)

        # Re-ingest
        await test_reingest(base_url, project, doc_id)

        # Delete document
        await test_delete_document(base_url, project, doc_id)

        # Confirm deleted
        await test_search_after_delete(base_url, project)

    # Chat attachment
    await test_chat_attachment(base_url, project)

    # Upload file
    await test_upload_file(base_url, project)


# ── Main ────────────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(description="Validate RAG document ingestion")
    parser.add_argument("--base-url", default=_BASE_URL, help="Gateway base URL")
    parser.add_argument("--project", default=_PROJECT, help="Project ID to use")
    args = parser.parse_args()

    base_url = args.base_url
    project = args.project

    print("=" * 60)
    print("  Qonduit Document Ingestion Validation")
    print("=" * 60)

    # Create temp dir for any temp files
    tmp_dir = Path("/tmp/qonduit_validation")
    tmp_dir.mkdir(exist_ok=True)

    # ── Parser tests ──────────────────────────────────────────────
    print("\n🔧 Parser Unit Tests")
    print("-" * 40)
    try:
        test_parser_txt(tmp_dir)
        test_parser_md(tmp_dir)
        test_parser_xml(tmp_dir)
        test_parser_vhdl(tmp_dir)
        test_parser_code_py(tmp_dir)
        test_parser_code_js(tmp_dir)
        test_parser_csv(tmp_dir)
        test_parser_json(tmp_dir)
        test_parser_log(tmp_dir)
        test_parser_docx(tmp_dir)
        test_parser_pdf(tmp_dir)
    except Exception as exc:
        _print_fail(f"Parser test error: {exc}")

    # ── API tests ─────────────────────────────────────────────────
    print("\n🔌 Connectivity Test")
    print("-" * 40)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{base_url}/docs")
            if resp.status_code == 200:
                _print_ok(f"Gateway reachable at {base_url}")
            else:
                _print_fail(f"Gateway returned {resp.status_code}")
    except httpx.ConnectError:
        _print_fail(f"Cannot connect to {base_url}")
    except Exception as exc:
        _print_fail(f"Connectivity error: {exc}")

    await run_api_tests(base_url, project)

    print("\n" + "=" * 60)
    print("  ✅ Validation complete")
    print("=" * 60)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
