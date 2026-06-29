#!/usr/bin/env python3
"""Export queue members to CSV."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from pathlib import Path

from core.cli import add_env_arg, create_client, output_path, read_csv, setup_path, tool_log, write_csv
from core.lookups import find_queue_by_name, get_queue_members


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Export queue members to CSV.")
    parser.add_argument("csv_file", nargs="?", help="CSV with QueueName column")
    parser.add_argument("--queue-name", help="Single queue name")
    add_env_arg(parser)
    args = parser.parse_args()

    rows = read_csv(Path(args.csv_file)) if args.csv_file else []
    if not rows:
        if not args.queue_name:
            parser.error("Provide csv_file or --queue-name")
        rows = [{"QueueName": args.queue_name}]

    client = create_client(log=tool_log, env_file=args.env_file)
    for row in rows:
        queue = find_queue_by_name(client, row.get("QueueName", ""))
        members = get_queue_members(client, queue["id"])
        export_rows = []
        for member in members:
            export_rows.append({
                "id": member.get("id") or (member.get("user") or {}).get("id"),
                "name": member.get("name") or (member.get("user") or {}).get("name"),
                "joined": member.get("joined"),
            })
        out = output_path(f"{row.get('QueueName', 'queue').replace(' ', '_')}_members.csv")
        write_csv(out, export_rows, ["id", "name", "joined"])
        tool_log("ok", f"Exported {len(export_rows)} members to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
