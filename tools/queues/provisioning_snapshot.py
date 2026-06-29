#!/usr/bin/env python3
"""Export user provisioning snapshot to CSV."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log, write_csv
from core.genesys_client import safe_str


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Export user provisioning snapshot.")
    parser.add_argument("--division", default="", help="Optional division name filter")
    add_env_arg(parser)
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    division_filter = args.division.strip().lower()
    rows = []
    for user in client.paged_get("/api/v2/users", params={"state": "any"}):
        division = user.get("division") or {}
        division_name = safe_str(division.get("name"))
        if division_filter and division_name.lower() != division_filter:
            continue
        rows.append({
            "id": user.get("id"),
            "name": user.get("name"),
            "email": user.get("email"),
            "username": user.get("username"),
            "title": user.get("title"),
            "state": user.get("state"),
            "division": division_name,
        })

    out = output_path("provisioning_snapshot.csv")
    write_csv(out, rows, ["id", "name", "email", "username", "title", "state", "division"])
    tool_log("ok", f"Exported {len(rows)} users to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
