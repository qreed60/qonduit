#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description="Enqueue a gateway ingestion job")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--repo-path")
    parser.add_argument("--branch")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    payload = {
        "project_id": args.project_id,
        "repo_path": args.repo_path,
        "branch": args.branch,
    }
    req = urllib.request.Request(
        f"{args.base_url.rstrip('/')}/v1/ingestion/enqueue",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read().decode("utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
