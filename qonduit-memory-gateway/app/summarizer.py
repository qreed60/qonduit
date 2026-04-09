from __future__ import annotations

import httpx
import logging
import os
import re

LLAMA_BASE = os.getenv("LLAMA_BASE", "http://192.168.5.5:8080").strip() or "http://192.168.5.5:8080"
logger = logging.getLogger("qonduit.memory_gateway")


def _extract_matches(pattern: str, text: str, max_items: int = 12) -> list[str]:
    values = [m.group(0).strip() for m in re.finditer(pattern, text, flags=re.MULTILINE)]
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            deduped.append(value)
            seen.add(value)
        if len(deduped) >= max_items:
            break
    return deduped


def _latest_user_task(older_messages: list[dict]) -> str:
    for message in reversed(older_messages):
        if message.get("role") == "user":
            content = str(message.get("content", "")).strip()
            if content:
                return content[:300]
    return ""


def build_coding_summary(existing_summary: str, older_messages: list[dict]) -> str:
    older_text = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}" for m in older_messages
    )

    file_paths = _extract_matches(r"\b[\w./-]+\.(?:py|ts|tsx|js|jsx|dart|go|rs|java|kt|cpp|c|h|hpp|md|yaml|yml|json|toml|ini|sh)\b", older_text)
    symbols = _extract_matches(r"\b(?:class|def|fn|function|interface|struct|enum)\s+[A-Za-z_][A-Za-z0-9_]*", older_text)
    api_endpoints = _extract_matches(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/[^\s]+", older_text)
    build_commands = _extract_matches(r"\b(?:python|pytest|pip|poetry|npm|pnpm|yarn|cargo|go|flutter|dart|make|uvicorn)\b[^\n]*", older_text)
    errors = _extract_matches(r"\b(?:Traceback|Exception|Error|FAILED|AssertionError|TypeError|ValueError|RuntimeError)[^\n]*", older_text)
    constraints = _extract_matches(r"\b(?:must|do not|don't|cannot|can't|required|preserve|backward compatible)[^\n]*", older_text)
    decisions = _extract_matches(r"\b(?:decide|decision|choose|chose|selected|use|using)\b[^\n]*", older_text)
    unresolved = _extract_matches(r"\b(?:todo|fixme|unresolved|remaining|next step|follow-up)\b[^\n]*", older_text)

    lines: list[str] = []
    lines.append("Coding memory summary")
    if existing_summary.strip():
        lines.append(f"Prior summary: {existing_summary.strip()[:400]}")

    task = _latest_user_task(older_messages)
    lines.append(f"Active task: {task or '(not specified)'}")

    if file_paths:
        lines.append("Files: " + ", ".join(file_paths))
    if symbols:
        lines.append("Symbols: " + "; ".join(symbols))
    if api_endpoints:
        lines.append("APIs/endpoints: " + "; ".join(api_endpoints))
    if build_commands:
        lines.append("Build/test commands: " + " | ".join(build_commands[:8]))
    if errors:
        lines.append("Errors/logs: " + " | ".join(errors[:8]))
    if decisions:
        lines.append("Decisions made: " + " | ".join(decisions[:8]))
    if constraints:
        lines.append("Constraints/rejected approaches: " + " | ".join(constraints[:8]))
    if unresolved:
        lines.append("Unresolved issues: " + " | ".join(unresolved[:8]))

    compact = "\n".join(line for line in lines if line.strip()).strip()
    return compact[:6000]


def build_chat_summary(existing_summary: str, older_messages: list[dict]) -> str:
    fallback_items: list[str] = []
    if existing_summary.strip():
        fallback_items.append(existing_summary.strip())
    fallback_items.extend(
        f"{m.get('role', 'user')}: {str(m.get('content', '')).strip()}"
        for m in older_messages[-6:]
        if str(m.get("content", "")).strip()
    )
    compact = "\n".join(fallback_items).strip()
    return compact[:2000] if compact else existing_summary


def build_summary_prompt(mode: str, existing_summary: str, older_messages: list[dict]) -> str:
    older_text = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}"
        for m in older_messages
    )

    if mode == "coding":
        return (
            "Update the rolling CODING conversation summary.\n\n"
            "Preserve exact technical details. Include file paths, classes/functions/symbols, "
            "APIs/endpoints, build/test commands, exact errors/logs/stack traces, active task, "
            "decisions made, unresolved issues, constraints, and rejected approaches.\n\n"
            f"Existing summary:\n{existing_summary or '(none)'}\n\n"
            f"Older messages:\n{older_text}\n"
        )

    return (
        "Update the rolling chat summary.\n\n"
        "Keep important user preferences, active projects, unresolved questions, "
        "constraints, and concrete facts. Be concise but useful.\n\n"
        f"Existing summary:\n{existing_summary or '(none)'}\n\n"
        f"Older messages:\n{older_text}\n"
    )


async def summarize_messages(
    model: str,
    existing_summary: str,
    older_messages: list[dict],
    mode: str = "chat",
) -> str:
    if not older_messages:
        return existing_summary

    normalized_mode = (mode or "chat").strip().lower()
    if normalized_mode not in {"chat", "coding"}:
        normalized_mode = "chat"

    if normalized_mode == "coding":
        return build_coding_summary(existing_summary, older_messages)

    prompt = build_summary_prompt(normalized_mode, existing_summary, older_messages)

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You compress conversations into concise working memory."
            },
            {
                "role": "user",
                "content": prompt
            },
        ],
        "max_tokens": 400,
        "temperature": 0.2,
    }

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{LLAMA_BASE}/v1/chat/completions",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as error:
        logger.warning(
            "summary_fallback_used model=%s older_messages=%s mode=%s error=%s",
            model,
            len(older_messages),
            normalized_mode,
            str(error),
        )
        return build_chat_summary(existing_summary, older_messages)
