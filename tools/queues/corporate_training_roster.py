#!/usr/bin/env python3
"""CLI for corporate training roster workflows (same logic as /corporate-training UI)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_apply_arg, add_env_arg, create_client, print_mode_banner, setup_path, tool_log, write_csv
from tools.queues.corporate_training_roster_lib import (
    RESULT_FIELDS,
    parse_names_from_workbook,
    run_return_from_training,
    run_start_training,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Corporate training roster queue updates.")
    parser.add_argument(
        "mode",
        choices=("start-training", "return-from-training"),
        help="start-training: verify training membership, joined=false on production; "
        "return-from-training: remove from training, joined=true on production",
    )
    parser.add_argument("--input", required=True, type=Path, help="Excel .xlsx with a Name/Agent/Email column")
    add_apply_arg(parser)
    add_env_arg(parser)
    args = parser.parse_args()

    setup_path()
    print_mode_banner("Corporate training roster", apply=args.apply, input=str(args.input))

    names = parse_names_from_workbook(args.input.read_bytes())
    if not names:
        tool_log("error", "No names found in spreadsheet")
        return 1

    client = create_client(log=tool_log, env_file=args.env_file)
    if args.mode == "start-training":
        payload = run_start_training(client, names, apply=args.apply)
    else:
        payload = run_return_from_training(client, names, apply=args.apply)

    out = write_csv(
        f"corporate_training_{payload['mode']}_{'apply' if args.apply else 'dry'}.csv",
        payload["rows"],
        fieldnames=RESULT_FIELDS,
    )
    tool_log("ok", f"Wrote {out}")
    stats = payload["stats"]
    tool_log("info", f"Training queues tracked: {payload['training_queue_count']}")
    tool_log("info", f"Stats: {stats}")
    return 1 if stats.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
