#!/usr/bin/env python3
"""Placeholder for registry tools not yet migrated to dedicated scripts."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import sys

from core.cli import setup_path, tool_log


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Genesys tool placeholder.")
    parser.add_argument("csv_file", nargs="?", help="Optional CSV input")
    parser.add_argument("--tool-id", default="", help="Tool identifier from registry")
    args = parser.parse_args()

    tool_log("warn", f"Tool '{args.tool_id or 'unknown'}' is not implemented in the reworked toolkit yet.")
    tool_log("info", "Check README.md for available scripts under tools/telephony, tools/users_roles, tools/queues, tools/audits.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
