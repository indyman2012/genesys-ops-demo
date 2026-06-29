#!/usr/bin/env python3
"""Remove a user from queues by prefix or from all assigned queues."""
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
    parser = argparse.ArgumentParser(description="Remove a user from matching queues.")
    parser.add_argument("csv_file", nargs="?", help="CSV with Email, QueuePrefix columns")
    parser.add_argument("--user-email", help="Single user email")
    parser.add_argument("--queue-prefix", default="", help="Queue prefix filter; blank removes from all user queues")
    add_apply_arg(parser)
    add_env_arg(parser)
    args = parser.parse_args()

    rows = read_csv(Path(args.csv_file)) if args.csv_file else []
    if not rows:
        if not args.user_email:
            parser.error("Provide csv_file or --user-email")
        rows = [{"Email": args.user_email, "QueuePrefix": args.queue_prefix}]

    client = create_client(log=tool_log, env_file=args.env_file)
    for row in rows:
        user = find_user_by_email(client, row.get("Email", ""))
        prefix = row.get("QueuePrefix", "")
        if prefix:
            queues = get_queues_by_prefix(client, prefix)
        else:
            queues = client.paged_get(f"/api/v2/users/{user['id']}/queues")
        tool_log("info", f"{user.get('name')} matched {len(queues)} queues for removal")
        for queue in queues:
            queue_name = queue.get("name")
            body = [{"id": user["id"]}]
            if args.apply:
                client.request(
                    "DELETE",
                    f"/api/v2/routing/queues/{queue['id']}/members",
                    json_body=body,
                )
                tool_log("ok", f"Removed from {queue_name}")
            else:
                write_jsonl("queue_remove_payloads.jsonl", {"queue": queue_name, "body": body})
                tool_log("dry", f"Would remove from {queue_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
