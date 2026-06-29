#!/usr/bin/env python3
"""Remove a user from every queue they belong to."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse

from core.cli import add_apply_arg, add_env_arg, create_client, print_mode_banner, setup_path, tool_log
from core.lookups import find_user_by_email, find_user_queue_memberships


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Remove a user from all queue memberships.")
    parser.add_argument("--email", required=True, help="User email")
    add_apply_arg(parser)
    add_env_arg(parser)
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    print_mode_banner("Remove User From All Queues", apply=args.apply, Email=args.email, API=client.api_host)

    user = find_user_by_email(client, args.email)
    tool_log("info", f"Target: {user.get('name')} ({user['id']})")

    memberships = find_user_queue_memberships(client, user["id"])
    tool_log("info", f"Found {len(memberships)} queue memberships")
    for membership in memberships:
        tool_log("info", f"  - {membership['queue_name']}")

    if not args.apply:
        tool_log("dry", "No changes made. Re-run with --apply to remove memberships.")
        return 0

    success = 0
    failures = 0
    for membership in memberships:
        try:
            client.request(
                "DELETE",
                f"/api/v2/routing/queues/{membership['queue_id']}/members/{user['id']}",
            )
            success += 1
            tool_log("ok", f"Removed from {membership['queue_name']}")
        except Exception as exc:
            failures += 1
            tool_log("error", f"Failed {membership['queue_name']}: {exc}")

    tool_log("info", f"Complete — success={success}, failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
