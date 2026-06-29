#!/usr/bin/env python3
"""Assign extensions from a pool to users matching titles in CSV."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from pathlib import Path

from core.cli import add_apply_arg, add_env_arg, create_client, read_csv, setup_path, tool_log, write_jsonl
from core.genesys_client import GenesysError, safe_str


def extension_pool(name: str) -> tuple[int, int]:
    if name == "15100-15999":
        return 15100, 15999
    return 8300, 8500


def user_extensions(user: dict) -> list[int]:
    extensions: list[int] = []
    for address in user.get("addresses") or []:
        if not isinstance(address, dict):
            continue
        if safe_str(address.get("mediaType")).upper() != "PHONE":
            continue
        if safe_str(address.get("type")).upper() != "WORK":
            continue
        digits = "".join(ch for ch in safe_str(address.get("extension")) if ch.isdigit())
        if digits:
            extensions.append(int(digits))
    return extensions


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Assign extensions from pool by user title.")
    parser.add_argument("csv_file", help="CSV with Title column")
    parser.add_argument("--extension-pool", default="8300-8500", choices=["8300-8500", "15100-15999"])
    add_apply_arg(parser)
    add_env_arg(parser)
    args = parser.parse_args()

    titles = {row.get("Title", "").lower() for row in read_csv(Path(args.csv_file))}
    client = create_client(log=tool_log, env_file=args.env_file)
    users = client.paged_get("/api/v2/users")
    used = {ext for user in users for ext in user_extensions(user)}
    low, high = extension_pool(args.extension_pool)
    tool_log("info", f"Using pool x{low}-x{high}")

    for user in users:
        if safe_str(user.get("title")).lower() not in titles:
            continue
        if user_extensions(user):
            tool_log("ok", f"{user.get('name')} already has extension")
            continue
        ext = next((candidate for candidate in range(low, high + 1) if candidate not in used), None)
        if ext is None:
            raise GenesysError("No extensions left in pool")

        detail = client.request("GET", f"/api/v2/users/{user['id']}")
        addresses = detail.get("addresses") or []
        work = None
        for address in addresses:
            if isinstance(address, dict) and safe_str(address.get("mediaType")).upper() == "PHONE" and safe_str(address.get("type")).upper() == "WORK":
                work = address
        if work is None:
            work = {"mediaType": "PHONE", "type": "WORK", "address": "", "countryCode": "US"}
            addresses.append(work)
        work.update({"extension": str(ext), "display": f"x{ext}", "voicePrimary": True, "primary": True})
        body = {"addresses": addresses}
        if detail.get("version") is not None:
            body["version"] = detail.get("version")

        if args.apply:
            client.request("PATCH", f"/api/v2/users/{user['id']}", json_body=body)
            used.add(ext)
            tool_log("ok", f"Assigned x{ext} to {user.get('name')}")
        else:
            write_jsonl("extensions_payloads.jsonl", {"user": user.get("name"), **body})
            tool_log("dry", f"Would assign x{ext} to {user.get('name')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
