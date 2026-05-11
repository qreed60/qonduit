"""Tool Registry — frontend-friendly tool definitions and execution.

This module defines:
- The canonical list of known tools with OpenAI-compatible function schemas.
- Classification of tools as read-only, destructive, or unavailable.
- Argument validation helpers used by the execute endpoint.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("qonduit.memory_gateway.tools")

# ---------------------------------------------------------------------------
# Tool classification constants
# ---------------------------------------------------------------------------

SAFE_TOOLS: set[str] = {
    "rag_project_list",
    "rag_collection_list",
    "rag_search",
    "rag_document_list",
    "rag_document_chunks",
    "gateway_health",
    "model_list",
}

DESTRUCTIVE_TOOLS: set[str] = {
    "shell_exec",
    "file_write",
    "file_delete",
    "model_stop",
    "model_launch",
    "system_shutdown",
    "homeassistant_action",
    "web_request",
}

UNAVAILABLE_TOOLS: set[str] = DESTRUCTIVE_TOOLS - {
    "shell_exec",
    "file_write",
    "file_delete",
    "model_stop",
    "model_launch",
    "system_shutdown",
    "homeassistant_action",
    "web_request",
}

ALL_KNOWN_TOOLS: set[str] = SAFE_TOOLS | DESTRUCTIVE_TOOLS

# ---------------------------------------------------------------------------
# Frontend-friendly tool definitions
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "rag_project_list",
        "displayName": "RAG Project List",
        "description": "List all known RAG projects and their health status.",
        "enabled": True,
        "requiresConfirmation": False,
        "category": "rag",
        "backendAvailable": True,
        "backendProvider": "Gateway",
        "lastError": None,
        "readOnly": True,
        "destructive": False,
        "schema": {
            "type": "function",
            "function": {
                "name": "rag_project_list",
                "description": "List all known RAG projects and their health status.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
    },
    {
        "name": "rag_collection_list",
        "displayName": "RAG Collection List",
        "description": "List logical collections within a RAG project.",
        "enabled": True,
        "requiresConfirmation": False,
        "category": "rag",
        "backendAvailable": True,
        "backendProvider": "Gateway",
        "lastError": None,
        "readOnly": True,
        "destructive": False,
        "schema": {
            "type": "function",
            "function": {
                "name": "rag_collection_list",
                "description": "List logical collections within a RAG project.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project identifier (default: default).",
                        },
                    },
                    "required": [],
                },
            },
        },
    },
    {
        "name": "rag_search",
        "displayName": "RAG Knowledge Search",
        "description": "Search indexed RAG knowledge for a project and optional logical collection.",
        "enabled": True,
        "requiresConfirmation": False,
        "category": "rag",
        "backendAvailable": True,
        "backendProvider": "Gateway",
        "lastError": None,
        "readOnly": True,
        "destructive": False,
        "schema": {
            "type": "function",
            "function": {
                "name": "rag_search",
                "description": "Search indexed RAG knowledge for a project and optional logical collection.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query text.",
                        },
                        "project_id": {
                            "type": "string",
                            "description": "Project identifier (default: default).",
                        },
                        "collection": {
                            "type": "string",
                            "description": "Optional logical collection filter.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default: 5).",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            },
        },
    },
    {
        "name": "gateway_health",
        "displayName": "Gateway Health",
        "description": "Return health status of Gateway dependencies (Qdrant, embedding service).",
        "enabled": True,
        "requiresConfirmation": False,
        "category": "system",
        "backendAvailable": True,
        "backendProvider": "Gateway",
        "lastError": None,
        "readOnly": True,
        "destructive": False,
        "schema": {
            "type": "function",
            "function": {
                "name": "gateway_health",
                "description": "Return health status of Gateway dependencies (Qdrant, embedding service).",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
    },
    {
        "name": "model_list",
        "displayName": "Model List",
        "description": "List available models through the Gateway.",
        "enabled": True,
        "requiresConfirmation": False,
        "category": "system",
        "backendAvailable": True,
        "backendProvider": "Gateway",
        "lastError": None,
        "readOnly": True,
        "destructive": False,
        "schema": {
            "type": "function",
            "function": {
                "name": "model_list",
                "description": "List available models through the Gateway.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
    },
    # ---- Optional tools (document listing) — available if helpers exist ----
    {
        "name": "rag_document_list",
        "displayName": "RAG Document List",
        "description": "List documents indexed in a RAG project.",
        "enabled": True,
        "requiresConfirmation": False,
        "category": "rag",
        "backendAvailable": True,
        "backendProvider": "Gateway",
        "lastError": None,
        "readOnly": True,
        "destructive": False,
        "schema": {
            "type": "function",
            "function": {
                "name": "rag_document_list",
                "description": "List documents indexed in a RAG project.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project identifier (default: default).",
                        },
                        "collection": {
                            "type": "string",
                            "description": "Optional collection filter.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results (default: 100).",
                            "default": 100,
                        },
                    },
                    "required": [],
                },
            },
        },
    },
    {
        "name": "rag_document_chunks",
        "displayName": "RAG Document Chunks",
        "description": "List chunks for a specific document in a RAG project.",
        "enabled": True,
        "requiresConfirmation": False,
        "category": "rag",
        "backendAvailable": True,
        "backendProvider": "Gateway",
        "lastError": None,
        "readOnly": True,
        "destructive": False,
        "schema": {
            "type": "function",
            "function": {
                "name": "rag_document_chunks",
                "description": "List chunks for a specific document in a RAG project.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project identifier (default: default).",
                        },
                        "document_id": {
                            "type": "string",
                            "description": "Document identifier (source::file_path).",
                        },
                    },
                    "required": ["document_id"],
                },
            },
        },
    },
    # ---- Risky / future placeholders ----
    {
        "name": "shell_exec",
        "displayName": "Shell Execute",
        "description": "Execute a shell command on the server. DANGEROUS: may alter system state.",
        "enabled": False,
        "requiresConfirmation": True,
        "category": "execution",
        "backendAvailable": False,
        "backendProvider": "Gateway",
        "lastError": "Destructive tool — not available in read-only mode.",
        "readOnly": False,
        "destructive": True,
        "schema": {
            "type": "function",
            "function": {
                "name": "shell_exec",
                "description": "Execute a shell command on the server.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Shell command to execute.",
                        },
                    },
                    "required": ["command"],
                },
            },
        },
    },
    {
        "name": "file_write",
        "displayName": "File Write",
        "description": "Write content to a file on the server. DANGEROUS: may alter system state.",
        "enabled": False,
        "requiresConfirmation": True,
        "category": "filesystem",
        "backendAvailable": False,
        "backendProvider": "Gateway",
        "lastError": "Destructive tool — not available in read-only mode.",
        "readOnly": False,
        "destructive": True,
        "schema": {
            "type": "function",
            "function": {
                "name": "file_write",
                "description": "Write content to a file on the server.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Target file path.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Content to write.",
                        },
                    },
                    "required": ["path", "content"],
                },
            },
        },
    },
    {
        "name": "file_delete",
        "displayName": "File Delete",
        "description": "Delete a file from the server. DANGEROUS: data loss.",
        "enabled": False,
        "requiresConfirmation": True,
        "category": "filesystem",
        "backendAvailable": False,
        "backendProvider": "Gateway",
        "lastError": "Destructive tool — not available in read-only mode.",
        "readOnly": False,
        "destructive": True,
        "schema": {
            "type": "function",
            "function": {
                "name": "file_delete",
                "description": "Delete a file from the server.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path to delete.",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
    },
    {
        "name": "model_stop",
        "displayName": "Model Stop",
        "description": "Stop a running model. DANGEROUS: terminates active inference.",
        "enabled": False,
        "requiresConfirmation": True,
        "category": "system",
        "backendAvailable": False,
        "backendProvider": "Gateway",
        "lastError": "Destructive tool — not available in read-only mode.",
        "readOnly": False,
        "destructive": True,
        "schema": {
            "type": "function",
            "function": {
                "name": "model_stop",
                "description": "Stop a running model.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "model": {
                            "type": "string",
                            "description": "Model identifier to stop.",
                        },
                    },
                    "required": ["model"],
                },
            },
        },
    },
    {
        "name": "model_launch",
        "displayName": "Model Launch",
        "description": "Launch a model. DANGEROUS: consumes resources.",
        "enabled": False,
        "requiresConfirmation": True,
        "category": "system",
        "backendAvailable": False,
        "backendProvider": "Gateway",
        "lastError": "Destructive tool — not available in read-only mode.",
        "readOnly": False,
        "destructive": True,
        "schema": {
            "type": "function",
            "function": {
                "name": "model_launch",
                "description": "Launch a model.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "model": {
                            "type": "string",
                            "description": "Model identifier to launch.",
                        },
                        "parameters": {
                            "type": "object",
                            "description": "Launch parameters.",
                        },
                    },
                    "required": ["model"],
                },
            },
        },
    },
    {
        "name": "homeassistant_action",
        "displayName": "Home Assistant Action",
        "description": "Execute an action in Home Assistant.",
        "enabled": False,
        "requiresConfirmation": True,
        "category": "homeassistant",
        "backendAvailable": False,
        "backendProvider": "Gateway",
        "lastError": "Not implemented.",
        "readOnly": False,
        "destructive": True,
        "schema": {
            "type": "function",
            "function": {
                "name": "homeassistant_action",
                "description": "Execute an action in Home Assistant.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "domain": {
                            "type": "string",
                            "description": "Home Assistant service domain.",
                        },
                        "service": {
                            "type": "string",
                            "description": "Service name.",
                        },
                        "data": {
                            "type": "object",
                            "description": "Service data.",
                        },
                    },
                    "required": ["domain", "service"],
                },
            },
        },
    },
    {
        "name": "web_request",
        "displayName": "Web Request",
        "description": "Make an arbitrary HTTP request. DANGEROUS: SSRF risk.",
        "enabled": False,
        "requiresConfirmation": True,
        "category": "web",
        "backendAvailable": False,
        "backendProvider": "Gateway",
        "lastError": "Destructive tool — not available in read-only mode.",
        "readOnly": False,
        "destructive": True,
        "schema": {
            "type": "function",
            "function": {
                "name": "web_request",
                "description": "Make an arbitrary HTTP request.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Target URL.",
                        },
                        "method": {
                            "type": "string",
                            "description": "HTTP method.",
                            "default": "GET",
                        },
                    },
                    "required": ["url"],
                },
            },
        },
    },
    {
        "name": "system_shutdown",
        "displayName": "System Shutdown",
        "description": "Shut down the system. EXTREMELY DANGEROUS.",
        "enabled": False,
        "requiresConfirmation": True,
        "category": "system",
        "backendAvailable": False,
        "backendProvider": "Gateway",
        "lastError": "Destructive tool — not available in read-only mode.",
        "readOnly": False,
        "destructive": True,
        "schema": {
            "type": "function",
            "function": {
                "name": "system_shutdown",
                "description": "Shut down the system.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "delay_seconds": {
                            "type": "integer",
                            "description": "Delay before shutdown.",
                            "default": 0,
                        },
                    },
                    "required": [],
                },
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Lookup structures (built at import time for performance)
# ---------------------------------------------------------------------------

# name -> definition
TOOLS_BY_NAME: dict[str, dict[str, Any]] = {
    t["name"]: t for t in TOOL_DEFINITIONS
}

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_tool(name: str) -> dict[str, Any] | None:
    """Return a tool definition by name, or None."""
    return TOOLS_BY_NAME.get(name)


def list_tools() -> list[dict[str, Any]]:
    """Return all known tool definitions."""
    return list(TOOL_DEFINITIONS)


def is_safe_tool(name: str) -> bool:
    """Return True if the tool is in the safe (read-only) set."""
    return name in SAFE_TOOLS


def is_destructive_tool(name: str) -> bool:
    """Return True if the tool is destructive."""
    return name in DESTRUCTIVE_TOOLS


def validate_tool_arguments(
    tool_name: str, arguments: dict[str, Any] | None
) -> tuple[bool, str | None]:
    """Validate arguments for a tool.

    Returns (ok, error_message).
    """
    tool = get_tool(tool_name)
    if not tool:
        return False, "tool_not_found"

    params = tool["schema"]["function"]["parameters"]
    required: list[str] = params.get("required", [])
    properties: dict[str, Any] = params.get("properties", {})

    args = arguments or {}

    # Check required fields
    for field in required:
        if field not in args:
            return False, f"invalid_arguments: missing required field '{field}'"

    # Type hints (best-effort)
    type_map: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    for field, schema in properties.items():
        if field in args:
            expected = type_map.get(schema.get("type"))
            if expected and not isinstance(args[field], expected):
                return False, f"invalid_arguments: field '{field}' expected {schema.get('type')}"

    return True, None
