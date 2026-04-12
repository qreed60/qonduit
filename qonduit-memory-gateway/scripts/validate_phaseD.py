#!/usr/bin/env python3
"""Validation script for Phase D project-grounding read-only tools."""

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
    print("=== Phase D Validation ===")
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        main.PROJECTS_ROOT = str(root)

        android = root / "android-demo"
        web = root / "web-demo"

        _write(
            android / "android/app/src/main/AndroidManifest.xml",
            "<manifest package='app.cogwheel.qonduit'/>",
        )
        _write(
            android / "android/app/src/main/kotlin/app/cogwheel/qonduit/MainActivity.kt",
            "class MainActivity {}",
        )
        _write(
            android / "android/app/src/main/kotlin/app/cogwheel/qonduit/QonduitApplication.kt",
            "class QonduitApplication : Application() {}",
        )
        _write(android / "android/settings.gradle.kts", "include(':app')")

        _write(web / "package.json", '{"name":"web-demo"}')
        _write(web / "src/main.tsx", "console.log('web')")

        failures = 0

        detection_raw = await main.execute_detect_project_type("android-demo")
        detection = json.loads(detection_raw)
        primary_type = detection.get("primary_type")
        confidence = detection.get("confidence", 0)
        if primary_type == "android_kotlin" and confidence >= 0.6:
            print("PASS: Android project detection is strong and explicit.")
        else:
            failures += 1
            print("FAIL: Android project detection is weak.")
            print(detection_raw)

        entries_raw = await main.execute_get_project_entry_points("android-demo")
        entries = json.loads(entries_raw).get("entry_points", [])
        entry_paths = [entry.get("path", "") for entry in entries]
        expected_markers = [
            "android/app/src/main/AndroidManifest.xml",
            "MainActivity.kt",
            "QonduitApplication.kt",
        ]
        if all(any(marker in path for path in entry_paths) for marker in expected_markers):
            print("PASS: Android entry point discovery is prioritized correctly.")
        else:
            failures += 1
            print("FAIL: Android entry point discovery missing expected files.")
            print(entries_raw)

        read_res = await main.execute_read_file(
            project_id="android-demo",
            path="android/app/src/main/kotlin/app/cogwheel/qonduit/MainActivity.kt",
            start_line=1,
            end_line=1,
        )
        if "MainActivity" in read_res:
            print("PASS: Exact file reads return requested project file content.")
        else:
            failures += 1
            print("FAIL: Exact file read did not return expected content.")
            print(read_res)

        leak_res = await main.execute_search_project_files(
            project_id="android-demo",
            pattern="**/*.tsx",
            max_results=20,
        )
        if "No files matching" in leak_res:
            print("PASS: No cross-project leakage from web project files.")
        else:
            failures += 1
            print("FAIL: Cross-project leakage detected.")
            print(leak_res)

        blocked_res = await main.execute_read_file(
            project_id="android-demo",
            path="../web-demo/src/main.tsx",
        )
        if "Blocked path escape attempt" in blocked_res:
            print("PASS: Path escape outside project root is blocked.")
        else:
            failures += 1
            print("FAIL: Path escape was not blocked.")
            print(blocked_res)

    print(f"\nValidation complete. failures={failures}")
    return failures


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
