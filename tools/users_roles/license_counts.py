#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import json

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Aggregate Genesys license counts.")
    add_env_arg(parser)
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    counts: dict[str, int] = {}
    for user in client.paged_get("/api/v2/license/users"):
        for license_name in user.get("licenses") or []:
            counts[license_name] = counts.get(license_name, 0) + 1

    tool_log("info", "License counts:")
    for license_name, count in sorted(counts.items()):
        tool_log("info", f"  {license_name}: {count}")

    out = output_path("license_counts.json")
    out.write_text(json.dumps(counts, indent=2), encoding="utf-8")
    tool_log("ok", f"Saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
