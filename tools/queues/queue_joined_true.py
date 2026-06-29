#!/usr/bin/env python3
"""Set joined=true for queues matching a prefix for a user."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from pathlib import Path

from core.cli import add_apply_arg, add_env_arg, create_client, read_csv, setup_path, tool_log, write_jsonl
from core.lookups import find_user_by_email, get_queues_by_prefix


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Activate a user on queues matching a prefix (joined=true).")
    parser.add_argument("csv_file", nargs="?", help="CSV with Email, QueuePrefix columns")
    parser.add_argument("--user-email", help="Single user email")
    parser.add_argument("--queue-prefix", help="Single queue prefix")
    add_apply_arg(parser)
    add_env_arg(parser)
    args = parser.parse_args()

    rows = read_csv(Path(args.csv_file)) if args.csv_file else []
    if not rows:
        if not args.user_email or not args.queue_prefix:
            parser.error("Provide csv_file or both --user-email and --queue-prefix")
        rows = [{"Email": args.user_email, "QueuePrefix": args.queue_prefix}]

    client = create_client(log=tool_log, env_file=args.env_file)
    for row in rows:
        user = find_user_by_email(client, row.get("Email", ""))
        queues = get_queues_by_prefix(client, row.get("QueuePrefix", ""))
        tool_log("info", f"{user.get('name')} matched {len(queues)} queues")
        for queue in queues:
            body = {"joined": True}
            queue_name = queue.get("name")
            if args.apply:
                client.request(
                    "PATCH",
                    f"/api/v2/users/{user['id']}/queues/{queue['id']}",
                    json_body=body,
                )
                tool_log("ok", f"{queue_name} joined=true")
            else:
                write_jsonl("queue_joined_true_payloads.jsonl", {"queue": queue_name, **body})
                tool_log("dry", f"Would set {queue_name} joined=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
