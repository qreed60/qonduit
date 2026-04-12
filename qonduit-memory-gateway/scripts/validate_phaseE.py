#!/usr/bin/env python3
"""Validation script for Phase E execution tools."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import main


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


async def run() -> int:
    print("=== Phase E Validation ===")
    failures = 0

    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        gateway_data = root / "gateway_data"
        main.PROJECTS_ROOT = str(root)
        main.GATEWAY_DATA_DIR = str(gateway_data)

        alpha = root / "alpha"
        beta = root / "beta"

        _write(alpha / "README.md", "alpha start\n")
        _write(alpha / ".qonduit_safe_mock", "enabled\n")
        _write(beta / "README.md", "beta start\n")

        patch_payload = json.dumps(
            {
                "format": "qonduit.patch/v1",
                "operations": [
                    {
                        "action": "write",
                        "path": "README.md",
                        "content": "alpha patched\n",
                    }
                ],
            }
        )
        patch_raw = await main.execute_apply_patch("alpha", patch_payload)
        patch_result = json.loads(patch_raw)
        if patch_result.get("ok") and (alpha / "README.md").read_text() == (
            "alpha patched\n"
        ):
            print("PASS: apply_patch writes within project root.")
        else:
            failures += 1
            print("FAIL: apply_patch did not write expected project file.")
            print(patch_raw)

        blocked_payload = json.dumps(
            {
                "format": "qonduit.patch/v1",
                "operations": [
                    {
                        "action": "write",
                        "path": "../beta/README.md",
                        "content": "hijack\n",
                    }
                ],
            }
        )
        blocked_raw = await main.execute_apply_patch("alpha", blocked_payload)
        blocked_result = json.loads(blocked_raw)
        beta_text = (beta / "README.md").read_text(encoding="utf-8")
        blocked = not blocked_result.get("ok") and beta_text == "beta start\n"
        if blocked:
            print("PASS: writes outside project root are blocked.")
        else:
            failures += 1
            print("FAIL: out-of-project write was not blocked.")
            print(blocked_raw)

        build_raw = await main.execute_run_build("alpha", timeout_seconds=60)
        build_result = json.loads(build_raw)
        if build_result.get("ok") and "safe_mock" in build_result.get(
            "project_type", ""
        ):
            print("PASS: run_build executes allowlisted command.")
        else:
            failures += 1
            print("FAIL: run_build did not succeed for safe mock.")
            print(build_raw)

        tests_raw = await main.execute_run_tests("alpha", timeout_seconds=60)
        tests_result = json.loads(tests_raw)
        if tests_result.get("ok") and "safe_mock" in tests_result.get(
            "project_type", ""
        ):
            print("PASS: run_tests executes allowlisted command.")
        else:
            failures += 1
            print("FAIL: run_tests did not succeed for safe mock.")
            print(tests_raw)

        tail_raw = await main.execute_tail_logs(
            "alpha",
            source="build",
            max_lines=1,
        )
        tail_result = json.loads(tail_raw)
        tail_ok = tail_result.get("ok") and tail_result.get("line_count", 0) <= 1
        if tail_ok:
            print("PASS: tail_logs returns bounded output.")
        else:
            failures += 1
            print("FAIL: tail_logs did not respect bounds.")
            print(tail_raw)

        # Ensure no cross-project access to beta logs via alpha project scope.
        (main.project_execution_log_dir("beta") / "build.log").write_text(
            "beta-only-log\n",
            encoding="utf-8",
        )
        alpha_tail_raw = await main.execute_tail_logs(
            "alpha",
            source="build",
            max_lines=50,
        )
        if "beta-only-log" not in alpha_tail_raw:
            print("PASS: no cross-project log access is exposed.")
        else:
            failures += 1
            print("FAIL: cross-project log leakage detected.")
            print(alpha_tail_raw)

    print(f"\nValidation complete. failures={failures}")
    return failures


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
