# RAG Document Ingestion

## Overview

This document describes the document ingestion system added to the Qonduit Memory Gateway for RAG indexing and native chat attachments.

## Existing Functions Reused

- **`rag.py`**: `ensure_collection()`, `add_document()`, `search_documents()`, `list_collections()`, `create_collection_marker()`, `rag_service`, `qdrant`, `embedding_backend`, `project_collection_name()`
- **`rag_read.py`**: `router` (read-only endpoints), `_validate_project_id()`, `_build_filter()`, `project_collection_name()`
- **`main.py`**: `chunk_text()`, `extract_text_from_file()`, `extract_text_from_pdf()`, `extract_text_from_docx()`, `extract_text_from_csv_file()`, `extract_text_from_xlsx()`, `extract_text_from_txt_like()`
- **`main.py`**: Existing `/rag/upload` endpoint (preserved)
- **`main.py`**: Chat RAG context merge logic (extended with attachments)

## New Files Added

| File | Description |
|------|-------------|
| `app/parser.py` | Document parser abstraction with text extraction |
| `app/documents.py` | Document storage, metadata, chunk/embed/upsert helpers, and REST endpoints |
| `scripts/validate_document_ingestion.py` | Validation script for all ingestion paths |

## New Endpoints Added

### Persistent RAG Document Endpoints (`/v1/rag`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/v1/rag/projects/{project_id}/documents/upload` | Upload a file for RAG indexing |
| `POST` | `/v1/rag/projects/{project_id}/documents/text` | Upload plain text for RAG indexing |
| `DELETE` | `/v1/rag/projects/{project_id}/documents/{document_id}` | Delete a document and its chunks |
| `POST` | `/v1/rag/projects/{project_id}/documents/{document_id}/reingest` | Reparse, rechunk, and re-index a document |
| `GET` | `/v1/rag/projects/{project_id}/documents/{document_id}/source` | Fetch document metadata and text preview |

### Chat Attachment Support

The existing `POST /v1/chat/completions` endpoint now accepts an optional `attachments` array in the request body.

## Supported File Types

### Plain / Document / Config
`.txt`, `.md`, `.markdown`, `.rst`, `.json`, `.jsonl`, `.csv`, `.tsv`, `.log`, `.xml`, `.html`, `.htm`, `.yaml`, `.yml`, `.toml`, `.ini`, `.cfg`

### Document Formats
- `.pdf` (extractable text only, via `pypdf`)
- `.docx` (via `python-docx`)

### Code / Engineering / HDL
`.py`, `.js`, `.jsx`, `.ts`, `.tsx`, `.dart`, `.kt`, `.kts`, `.java`, `.c`, `.h`, `.cpp`, `.hpp`, `.cc`, `.cxx`, `.cs`, `.go`, `.rs`, `.swift`, `.php`, `.rb`, `.lua`, `.r`, `.sh`, `.bash`, `.zsh`, `.ps1`, `.bat`, `.cmd`, `.sql`, `.graphql`, `.proto`, `Dockerfile`, `Makefile`, `.cmake`, `.gradle`, `.gradle.kts`, `.xsd`, `.svg`, `.v`, `.sv`, `.svh`, `.vhd`, `.vhdl`, `.VHDL`, `.xdc`, `.sdc`, `.qsf`, `.tcl`

### Spreadsheet / Presentation (Google docs export)
- Google Sheets → `.csv` (supported), `.xlsx` (supported)
- Google Docs → `.docx`, `.pdf`, `.txt`, `.md` (all supported)
- Google Slides → `.pdf` (supported), `.pptx` (not implemented — no lightweight extractor available)

### Notes
- Extension matching is **case-insensitive**.
- Scanned PDF / OCR support is **not** implemented. Files with no extractable text return a clear warning.
- `.pptx` is not supported in this phase (no lightweight pure-Python extractor without heavy dependencies).

## Validation Commands

### Parser Unit Tests
```bash
cd /workspace/project/qonduit/qonduit-memory-gateway
python -c "from app.parser import parse_document_bytes; print('Parser import OK')"
```

### Full Ingestion Validation
```bash
python scripts/validate_document_ingestion.py
```

### Existing RAG Search (regression check)
```bash
curl -s http://localhost:8011/v1/rag/health | python -m json.tool
```

### Existing RAG Chat (regression check)
```bash
curl -s -X POST http://localhost:8011/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"local","messages":[{"role":"user","content":"Hello"}]}' | python -m json.tool
```

## Storage Paths

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `QONDUIT_RAG_UPLOAD_DIR` | `/app/data/rag_uploads` | Persistent RAG document storage |
| `QONDUIT_CHAT_UPLOAD_DIR` | `/app/data/chat_uploads` | Chat attachment temporary storage |
| `QONDUIT_RAG_UPLOAD_MAX_BYTES` | `10485760` (10 MB) | Max upload size for RAG documents |
| `QONDUIT_CHAT_ATTACHMENT_MAX_BYTES` | `10485760` (10 MB) | Max size per chat attachment |
| `QONDUIT_CHAT_ATTACHMENT_CONTEXT_MAX_CHARS` | `20000` | Max chars injected from a chat attachment |
| `QONDUIT_RAG_UPLOAD_CHUNK_SIZE` | `4000` | Chunk size in characters |
| `QONDUIT_RAG_UPLOAD_CHUNK_OVERLAP` | `500` | Chunk overlap in characters |

### Storage Layout
```
{QONDUIT_RAG_UPLOAD_DIR}/{project_id}/{document_id}/
  source.ext            — Original uploaded file
  metadata.json         — Document metadata (JSON)
```

### Qdrant Payload Fields per Chunk
- `project_id`
- `collection`
- `namespace` (same as collection)
- `document_id`
- `document_name`
- `source`
- `file_path` / `saved_path`
- `file_type`
- `mime_type`
- `chunk_index`
- `text`
- `created_at`
- `updated_at`
- `user_id` (if provided)
- `google_file_id` (if provided)

## Chat Attachment Modes

| Mode | Description |
|------|-------------|
| `chat_context_only` | Extract text, inject into current prompt. Do NOT save to RAG. |
| `save_to_rag` | Extract, store, chunk, embed, upsert to RAG, AND inject into current prompt. |
| `save_to_rag_only` | Store, chunk, embed, upsert to RAG. Do NOT inject full text into current prompt. |
| `use_existing_rag_document` | Future-compatible. Not implemented in this phase. |

## Merge Order (RAG chunks + Chat attachments)

1. Retrieved RAG context
2. Attached document context
3. Question
4. Instructions

## Curl Examples

### A. Upload text note
```bash
curl -s -X POST http://localhost:8011/v1/rag/projects/default/documents/text \
  -H "Content-Type: application/json" \
  -d '{
    "collection": "work",
    "document_name": "meeting_notes.txt",
    "text": "Meeting notes from 2024-01-15. Discussed Q3 roadmap."
  }' | python -m json.tool
```

### B. Upload file
```bash
curl -s -X POST "http://localhost:8011/v1/rag/projects/default/documents/upload?collection=work" \
  -F "file=@/path/to/document.pdf" \
  -F "user_id=user123" | python -m json.tool
```

### C. Search
```bash
curl -s -X POST http://localhost:8011/v1/rag/projects/default/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Q3 roadmap", "collection": "work", "limit": 5}' | python -m json.tool
```

### D. Chat with attachment
```bash
curl -s -X POST http://localhost:8011/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "local",
    "messages": [{"role": "user", "content": "What did we discuss about the roadmap?"}],
    "project_id": "default",
    "rag_collection": "work",
    "attachments": [{
      "name": "roadmap.pdf",
      "mime_type": "application/pdf",
      "content_base64": "JVBERi0xLjQK...",
      "collection": "work",
      "mode": "chat_context_only"
    }]
  }' | python -m json.tool
```

### E. Delete document
```bash
curl -s -X DELETE http://localhost:8011/v1/rag/projects/default/documents/doc-uuid-here | python -m json.tool
```

### F. Reingest
```bash
curl -s -X POST http://localhost:8011/v1/rag/projects/default/documents/doc-uuid-here/reingest | python -m json.tool
```

## Parser Dependencies Added

No new dependencies required. The following were already in `requirements.txt`:
- `pypdf==5.1.0` — PDF text extraction
- `python-docx==1.1.2` — DOCX text extraction
- `openpyxl==3.1.5` — XLSX text extraction
- `python-multipart==0.0.9` — Multipart form data parsing (FastAPI)

## Known Limitations

1. **Google OAuth / Drive Picker** — Not implemented. Google docs must be exported and uploaded manually.
2. **Scanned PDFs / OCR** — Not implemented. PDFs with no extractable text will return a warning.
3. **`.pptx` files** — Not implemented (no lightweight pure-Python extractor available).
4. **`.xlsx` support** — Implemented via `openpyxl` (already a dependency).
5. **Background re-indexing** — Not implemented. All operations are synchronous.
6. **Batch upload** — Not implemented. Upload one file at a time.
