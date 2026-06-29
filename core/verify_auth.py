#!/usr/bin/env python3
"""Verify OAuth credentials in client.env."""
from __future__ import annotations

import argparse

from core.cli import create_client, print_mode_banner, setup_path, tool_log
from core.config import get_api_host, normalize_region


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Verify Genesys Cloud OAuth credentials.")
    parser.add_argument("--env-file", default=None, help="Path to client.env")
    args = parser.parse_args()

    print_mode_banner(
        "Genesys Cloud Auth Check",
        apply=True,
        Region=normalize_region(),
        API=get_api_host(),
    )

    client = create_client(log=tool_log, env_file=args.env_file)
    org = client.request("GET", "/api/v2/organizations/me")
    tool_log("ok", f"Organization: {org.get('name')} (id={org.get('id')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
