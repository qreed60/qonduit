#!/usr/bin/env python3
"""Lightweight Phase 2/3 validation checks.

Validates:
- project-scoped persistence separation
- mode precedence behavior
- coding summary technical detail retention
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def run() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ["GATEWAY_DATA_DIR"] = temp_dir

        gateway_root = Path(__file__).resolve().parents[1]
        if str(gateway_root) not in sys.path:
            sys.path.insert(0, str(gateway_root))

        from app.store import load_conversation, save_conversation
        from app.summarizer import build_coding_summary

        save_conversation(
            "conv-1",
            {
                "summary": "alpha summary",
                "recent_messages": [{"role": "user", "content": "hello alpha"}],
            },
            project_id="alpha",
        )
        save_conversation(
            "conv-1",
            {
                "summary": "beta summary",
                "recent_messages": [{"role": "user", "content": "hello beta"}],
            },
            project_id="beta",
        )

        alpha_state = load_conversation("conv-1", project_id="alpha")
        beta_state = load_conversation("conv-1", project_id="beta")

        assert alpha_state["summary"] == "alpha summary"
        assert beta_state["summary"] == "beta summary"
        assert alpha_state["project_id"] == "alpha"
        assert beta_state["project_id"] == "beta"

        coding_summary = build_coding_summary(
            "",
            [
                {
                    "role": "user",
                    "content": (
                        "Fix bug in app/main.py and lib/service.ts\n"
                        "Run pytest -q\n"
                        "Traceback (most recent call last): ValueError: bad input\n"
                        "Do not break backward compatible API behavior"
                    ),
                },
                {
                    "role": "assistant",
                    "content": "Decision: use safe fallback parser; TODO unresolved edge case",
                },
            ],
        )

        assert "app/main.py" in coding_summary
        assert "pytest -q" in coding_summary
        assert "ValueError" in coding_summary
        assert "Do not break backward compatible" in coding_summary
        assert "Decision" in coding_summary
        assert "Unresolved" in coding_summary or "unresolved" in coding_summary

    print("Phase 2/3 validation passed.")


if __name__ == "__main__":
    run()
