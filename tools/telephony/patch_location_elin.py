#!/usr/bin/env python3
"""Set emergency number type to ELIN on all locations that have one."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import json

from core.cli import add_apply_arg, add_env_arg, create_client, setup_path, tool_log


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Patch location emergency numbers to type=elin.")
    add_apply_arg(parser)
    add_env_arg(parser)
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    locations = client.paged_get("/api/v2/locations")
    tool_log("info", f"Found {len(locations)} locations")

    updated = 0
    for index, location in enumerate(locations, 1):
        location_id = location["id"]
        name = location.get("name", "")
        detail = client.request("GET", f"/api/v2/locations/{location_id}")
        version = detail.get("version")
        emergency = detail.get("emergencyNumber") or {}
        current_type = emergency.get("type")

        if not emergency:
            tool_log("info", f"[{index}] {name}: no emergency number")
            continue
        if current_type == "elin":
            tool_log("ok", f"[{index}] {name}: already ELIN")
            continue

        tool_log("info", f"[{index}] {name}: v{version} {current_type} -> elin")
        if not args.apply:
            continue

        emergency_block = json.loads(json.dumps(emergency))
        emergency_block["type"] = "elin"
        client.request(
            "PATCH",
            f"/api/v2/locations/{location_id}",
            json_body={"version": version, "emergencyNumber": emergency_block},
        )
        updated += 1

    tool_log("info", f"Updated: {updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
