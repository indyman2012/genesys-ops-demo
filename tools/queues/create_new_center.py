#!/usr/bin/env python3
"""Preflight planner for new center provisioning from CSV."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from pathlib import Path

from core.cli import add_apply_arg, add_env_arg, create_client, read_csv, setup_path, tool_log, write_jsonl
from core.genesys_client import GenesysError, ensure_e164


REQUIRED_COLUMNS = [
    "Division", "SiteName", "LocationName", "Address1", "City", "State", "Zip", "Country",
    "EmergencyNumber", "CallerIDNumber", "BackOfficeNumber", "TimeZone", "SiteAdminEmail", "TrunkBaseName",
]


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Create New Center preflight planner.")
    parser.add_argument("csv_file", help="Center provisioning CSV")
    add_apply_arg(parser)
    add_env_arg(parser)
    args = parser.parse_args()

    create_client(log=tool_log, env_file=args.env_file)
    for index, row in enumerate(read_csv(Path(args.csv_file)), start=1):
        missing = [column for column in REQUIRED_COLUMNS if not row.get(column)]
        if missing:
            raise GenesysError(f"Row {index} missing columns: {missing}")

        code = row["SiteName"][:3].upper()
        plan = {
            "division": row["Division"],
            "location": {
                "name": row["LocationName"],
                "address": {
                    "street1": row["Address1"],
                    "city": row["City"],
                    "state": row["State"],
                    "zipcode": row["Zip"],
                    "country": row["Country"],
                },
            },
            "site": row["SiteName"],
            "didRange": ensure_e164(row["CallerIDNumber"]),
            "comingSoonRouteDnis": ensure_e164(row["CallerIDNumber"]),
            "queues": [
                f"Patient Services - {code} - {row['LocationName']}",
                f"NCM_{code} - {row['LocationName']}",
            ],
            "backOfficeContact": {
                "name": f"{row['SiteName']} - Back Office Line",
                "number": ensure_e164(row["BackOfficeNumber"]),
            },
        }
        write_jsonl("create_new_center_preflight_payloads.jsonl", plan)
        tool_log("dry" if not args.apply else "warn", f"Planned center {row['SiteName']} ({row['LocationName']})")

    if args.apply:
        tool_log("warn", "Create New Center apply mode is preflight-only until endpoint hardening is complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
