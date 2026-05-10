"""Document parser abstraction for RAG and chat attachment ingestion.

Parses raw file bytes into structured ``ParsedDocument`` objects with extracted
text, metadata, and warnings.  Supports a broad set of plain-text, code, and
document formats (PDF, DOCX, CSV, XLSX).

Supported extensions are case-insensitive.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docx import Document as DocxDocument
from pypdf import PdfReader

logger = logging.getLogger("qonduit.memory_gateway.parser")

# ---------------------------------------------------------------------------
# Extension → parser name mapping (all lowercase keys)
# ---------------------------------------------------------------------------

# Plain / document / config
_PLAIN_EXTENSIONS: dict[str, str] = {
    ".txt": "plain_text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "restructured_text",
    ".json": "json",
    ".jsonl": "jsonl",
    ".csv": "csv",
    ".tsv": "tsv",
    ".log": "log",
    ".xml": "xml",
    ".html": "html",
    ".htm": "html",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
}

# Code / engineering / HDL
_CODE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".dart": "dart",
    ".kt": "kotlin",
    ".kts": "kotlin_script",
    ".java": "java",
    ".c": "c",
    ".h": "c_header",
    ".cpp": "cpp",
    ".hpp": "cpp_header",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".swift": "swift",
    ".php": "php",
    ".rb": "ruby",
    ".lua": "lua",
    ".r": "r",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".ps1": "powershell",
    ".bat": "batch",
    ".cmd": "batch",
    ".sql": "sql",
    ".graphql": "graphql",
    ".proto": "protobuf",
    ".cmake": "cmake",
    ".gradle": "gradle",
    ".gradle.kts": "gradle_kts",
    ".xsd": "xsd",
    ".tcl": "tcl",
}

# Build files (filename-based, checked separately)
_BUILD_FILES: dict[str, str] = {
    "dockerfile": "dockerfile",
    "makefile": "makefile",
}

# HDL / hardware description
_HDL_EXTENSIONS: dict[str, str] = {
    ".v": "verilog",
    ".sv": "systemverilog",
    ".svh": "systemverilog_header",
    ".vhd": "vhdl",
    ".vhdl": "vhdl",
    ".xdc": "xdc",
    ".sdc": "sdc",
    ".qsf": "qsf",
}

# Document formats
_DOCUMENT_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".xls": "xlsx",
    ".pptx": "pptx",  # Reserved for future
}

# Build the master lookup (all lowercase)
_EXTENSION_MAP: dict[str, str] = {}
_EXTENSION_MAP.update(_PLAIN_EXTENSIONS)
_EXTENSION_MAP.update(_CODE_EXTENSIONS)
_EXTENSION_MAP.update(_HDL_EXTENSIONS)
_EXTENSION_MAP.update(_DOCUMENT_EXTENSIONS)


def _detect_parser_by_extension(
    filename: str,
    mime_type: str | None,
) -> tuple[str, str]:
    """Return ``(parser_name, file_type)`` based on the filename extension.

    Falls back to ``plain_text`` when the extension is unknown but the content
    is likely text.
    """
    if filename:
        ext = Path(filename).suffix.lower()
        if ext and ext in _EXTENSION_MAP:
            return _EXTENSION_MAP[ext], ext.lstrip(".")

        # Build-file check (filename only, no extension)
        base = Path(filename).name.lower()
        if base in _BUILD_FILES:
            return _BUILD_FILES[base], base

    # MIME-type hint
    if mime_type:
        mt = mime_type.lower()
        if "pdf" in mt:
            return "pdf", ".pdf".lstrip(".")
        if "word" in mt or "docx" in mt:
            return "docx", ".docx".lstrip(".")
        if "spreadsheet" in mt or "excel" in mt or "xlsx" in mt:
            return "xlsx", ".xlsx".lstrip(".")
        if "csv" in mt:
            return "csv", ".csv".lstrip(".")

    return "plain_text", "txt"


# ---------------------------------------------------------------------------
# Text extractors
# ---------------------------------------------------------------------------


def _read_text_file(data: bytes, mime_hint: str | None = None) -> str:
    """Read a generic text file."""
    del mime_hint  # not used, but signature must match dispatch
    return data.decode("utf-8", errors="replace")


def _parse_json(data: bytes, mime_hint: str | None = None) -> str:
    """Pretty-print JSON for readability."""
    del mime_hint  # not used, but signature must match dispatch
    try:
        obj = json.loads(data.decode("utf-8"))
        return json.dumps(obj, indent=2, ensure_ascii=False)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return data.decode("utf-8", errors="replace")


def _parse_jsonl(data: bytes, mime_hint: str | None = None) -> str:
    """Flatten JSONL for readability."""
    del mime_hint  # not used, but signature must match dispatch
    lines: list[str] = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            lines.append(json.dumps(obj, ensure_ascii=False))
        except json.JSONDecodeError:
            lines.append(line)
    return "\n".join(lines)


def _parse_csv_file(data: bytes, mime_hint: str | None = None) -> str:
    """Read CSV / TSV data and return a readable text representation."""
    text = data.decode("utf-8", errors="replace")
    # Auto-detect delimiter: if first row contains tabs, use tab delimiter
    first_line = text.splitlines()[0] if text.splitlines() else text
    delimiter = "\t" if "\t" in first_line else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows: list[str] = []
    for row in reader:
        rows.append(" | ".join(cell.strip() for cell in row))
    return "\n".join(rows)


def _parse_xml(data: bytes, mime_hint: str | None = None) -> str:
    """Return XML content, pretty-printed if possible."""
    del mime_hint  # not used, but signature must match dispatch
    raw = data.decode("utf-8", errors="replace")
    try:
        from xml.dom.minidom import parseString

        dom = parseString(raw)
        return dom.toprettyxml(indent="  ", encoding=None)
    except Exception:
        return raw


def _parse_pdf(data: bytes) -> tuple[str, list[str]]:
    """Extract text from PDF bytes.

    Returns ``(text, warnings)``.  Warnings may include OCR notes.
    """
    warnings: list[str] = []
    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []

    for i, page in enumerate(reader.pages):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        if page_text.strip():
            pages.append(page_text.strip())
        else:
            warnings.append(
                f"Page {i + 1} has no extractable text (scanned/image PDF?)"
            )

    text = "\n\n".join(pages)
    if not text.strip() and reader.pages:
        warnings.append("PDF contains pages but no extractable text")
    return text, warnings


def _parse_docx(data: bytes) -> tuple[str, list[str]]:
    """Extract text from DOCX bytes."""
    doc = DocxDocument(io.BytesIO(data))
    paragraphs = [
        p.text for p in doc.paragraphs if p.text and p.text.strip()
    ]
    # Also grab text from tables
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                paragraphs.append(" | ".join(cells))
    text = "\n\n".join(paragraphs)
    warnings: list[str] = []
    if not text.strip():
        warnings.append("DOCX contains no extractable text")
    return text, warnings


def _parse_xlsx(data: bytes) -> tuple[str, list[str]]:
    """Extract text from XLSX bytes using openpyxl."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    parts: list[str] = []
    for sheet in wb.worksheets:
        parts.append(f"# Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            values = ["" if cell is None else str(cell) for cell in row]
            line = " | ".join(v.strip() for v in values if str(v).strip())
            if line:
                parts.append(line)
    wb.close()
    text = "\n".join(parts)
    warnings: list[str] = []
    if not text.strip():
        warnings.append("XLSX contains no extractable data")
    return text, warnings


# Map parser names to extractor functions
# Functions that return (text, warnings) tuple
_COMPOSED_EXTRACTORS: dict[str, Any] = {
    "pdf": _parse_pdf,
    "docx": _parse_docx,
    "xlsx": _parse_xlsx,
}

# Functions that return just text (no warnings)
_SIMPLE_EXTRACTORS: dict[str, Any] = {
    "plain_text": _read_text_file,
    "markdown": _read_text_file,
    "restructured_text": _read_text_file,
    "json": _parse_json,
    "jsonl": _parse_jsonl,
    "csv": _parse_csv_file,
    "tsv": _parse_csv_file,
    "log": _read_text_file,
    "xml": _parse_xml,
    "html": _read_text_file,
    "yaml": _read_text_file,
    "toml": _read_text_file,
    "ini": _read_text_file,
    "python": _read_text_file,
    "javascript": _read_text_file,
    "typescript": _read_text_file,
    "dart": _read_text_file,
    "kotlin": _read_text_file,
    "kotlin_script": _read_text_file,
    "java": _read_text_file,
    "c": _read_text_file,
    "c_header": _read_text_file,
    "cpp": _read_text_file,
    "cpp_header": _read_text_file,
    "csharp": _read_text_file,
    "go": _read_text_file,
    "rust": _read_text_file,
    "swift": _read_text_file,
    "php": _read_text_file,
    "ruby": _read_text_file,
    "lua": _read_text_file,
    "r": _read_text_file,
    "shell": _read_text_file,
    "powershell": _read_text_file,
    "batch": _read_text_file,
    "sql": _read_text_file,
    "graphql": _read_text_file,
    "protobuf": _read_text_file,
    "dockerfile": _read_text_file,
    "makefile": _read_text_file,
    "cmake": _read_text_file,
    "gradle": _read_text_file,
    "gradle_kts": _read_text_file,
    "xsd": _read_text_file,
    "tcl": _read_text_file,
    "verilog": _read_text_file,
    "systemverilog": _read_text_file,
    "systemverilog_header": _read_text_file,
    "vhdl": _read_text_file,
    "xdc": _read_text_file,
    "sdc": _read_text_file,
    "qsf": _read_text_file,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class ParsedDocument:
    """Result of parsing a document file."""

    text: str
    parser: str
    file_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def parse_document_bytes(
    data: bytes,
    filename: str,
    mime_type: str | None = None,
) -> ParsedDocument:
    """Parse raw file bytes and return a :class:`ParsedDocument`.

    Parameters
    ----------
    data:
        Raw file bytes.
    filename:
        Original filename (used for extension detection).
    mime_type:
        Optional MIME type hint from the upload.

    Returns
    -------
    ParsedDocument
        Contains extracted text, parser name, file type, metadata, and warnings.
    """
    parser_name, file_type = _detect_parser_by_extension(filename, mime_type)

    metadata: dict[str, Any] = {
        "parser": parser_name,
        "file_type": file_type,
        "original_filename": filename,
    }
    if mime_type:
        metadata["mime_type"] = mime_type

    # Composed extractors return (text, warnings)
    if parser_name in _COMPOSED_EXTRACTORS:
        extractor = _COMPOSED_EXTRACTORS[parser_name]
        text, warnings = extractor(data)
        return ParsedDocument(
            text=text,
            parser=parser_name,
            file_type=file_type,
            metadata=metadata,
            warnings=warnings,
        )

    # Simple extractors return just text
    extractor = _SIMPLE_EXTRACTORS.get(parser_name, _read_text_file)
    try:
        text = extractor(data, mime_hint=mime_type)
    except Exception as exc:
        logger.warning(
            "parser_failed parser=%s file=%s error=%s",
            parser_name,
            filename,
            exc,
        )
        text = f"Error parsing file with parser '{parser_name}': {exc}"

    return ParsedDocument(
        text=text,
        parser=parser_name,
        file_type=file_type,
        metadata=metadata,
        warnings=[],
    )


def parse_document_file(
    file_path: str,
    filename: str | None = None,
    mime_type: str | None = None,
) -> ParsedDocument:
    """Parse a file from disk and return a :class:`ParsedDocument`.

    Convenience wrapper around :func:`parse_document_bytes`.
    """
    if filename is None:
        filename = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        data = f.read()
    return parse_document_bytes(data, filename, mime_type)


# ---------------------------------------------------------------------------
# Sentinel to detect unsupported formats
# ---------------------------------------------------------------------------

UNSUPPORTED_FILE_TYPES: set[str] = set()  # Could be populated dynamically


def is_supported_extension(filename: str) -> bool:
    """Return ``True`` if the filename extension is supported for parsing."""
    _, file_type = _detect_parser_by_extension(filename, None)
    if file_type == "pptx":
        return False  # Reserved but not yet implemented
    return True
