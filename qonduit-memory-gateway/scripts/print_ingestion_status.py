#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print gateway ingestion status (all or one project)",
    )
    parser.add_argument("--project-id")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    target = (
        f"{base}/v1/ingestion/status/{args.project_id}"
        if args.project_id
        else f"{base}/v1/ingestion/status"
    )
    with urllib.request.urlopen(target, timeout=30) as response:
        data = response.read().decode("utf-8")
    print(json.dumps(json.loads(data), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
