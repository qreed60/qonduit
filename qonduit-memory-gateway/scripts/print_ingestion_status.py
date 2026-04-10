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
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--debug", action="store_true", help="Show full debug state")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    if args.debug:
        target = f"{base}/v1/ingestion/debug"
    elif args.project_id:
        target = f"{base}/v1/ingestion/status/{args.project_id}"
    else:
        target = f"{base}/v1/ingestion/status"
    
    with urllib.request.urlopen(target, timeout=30) as response:
        data = response.read().decode("utf-8")
    print(json.dumps(json.loads(data), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
