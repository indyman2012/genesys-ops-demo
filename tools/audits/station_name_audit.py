#!/usr/bin/env python3
"""
Audit Genesys Cloud users assigned to a station whose name does not match
the expected "{FirstName LastName} Web" convention.

Uses:
  GET /api/v2/stations  (status=ASSOCIATED, userId set)
  GET /api/v2/users     (for name/email lookup)

Read-only. Outputs CSV + console summary under outputs/.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import (
    add_env_arg,
    create_client,
    output_path,
    setup_path,
    tool_log,
    write_csv,
)

setup_path()

STATION_SUFFIX = " Web"


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def expected_station_name(user_name: str) -> str:
    return f"{user_name.strip()}{STATION_SUFFIX}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit user station name assignments.")
    add_env_arg(parser)
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include inactive/deleted users in the audit.",
    )
    args = parser.parse_args()

    client = create_client(env_file=args.env_file)

    tool_log("info", "Loading users...")
    users_by_id: dict[str, dict] = {}
    for u in client.paged_get("/api/v2/users", params={"pageSize": 100}):
        uid = u.get("id")
        if not uid:
            continue
        state = (u.get("state") or "").lower()
        if not args.include_inactive and state != "active":
            continue
        users_by_id[uid] = {
            "id": uid,
            "name": u.get("name") or "",
            "email": u.get("email") or "",
            "title": u.get("title") or "",
            "state": state,
        }

    tool_log("info", f"Loaded {len(users_by_id)} users")

    tool_log("info", "Loading associated stations...")
    associated: list[dict] = []
    for station in client.paged_get("/api/v2/stations", params={"pageSize": 100}):
        if (station.get("status") or "").upper() != "ASSOCIATED":
            continue
        user_id = station.get("userId") or station.get("webRtcUserId")
        if not user_id:
            continue
        associated.append(station)

    tool_log("info", f"Found {len(associated)} associated stations")

    rows_ok: list[dict] = []
    rows_bad: list[dict] = []
    rows_unknown_user: list[dict] = []

    for station in associated:
        user_id = station.get("userId") or station.get("webRtcUserId")
        actual_name = station.get("name") or ""
        station_id = station.get("id") or ""
        station_type = station.get("type") or ""

        user = users_by_id.get(user_id)
        if not user:
            rows_unknown_user.append({
                "user_id": user_id,
                "user_name": "",
                "email": "",
                "title": "",
                "expected_station": "",
                "actual_station": actual_name,
                "station_id": station_id,
                "station_type": station_type,
                "issue": "Associated user not in active user list",
            })
            continue

        expected = expected_station_name(user["name"])
        if normalize_name(actual_name) == normalize_name(expected):
            rows_ok.append({
                "user_id": user_id,
                "user_name": user["name"],
                "email": user["email"],
                "title": user["title"],
                "expected_station": expected,
                "actual_station": actual_name,
                "station_id": station_id,
                "station_type": station_type,
                "issue": "",
            })
        else:
            rows_bad.append({
                "user_id": user_id,
                "user_name": user["name"],
                "email": user["email"],
                "title": user["title"],
                "expected_station": expected,
                "actual_station": actual_name,
                "station_id": station_id,
                "station_type": station_type,
                "issue": "Station name mismatch",
            })

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fieldnames = [
        "user_id", "user_name", "email", "title",
        "expected_station", "actual_station",
        "station_id", "station_type", "issue",
    ]

    mismatch_path = output_path(f"station_name_mismatch_{ts}.csv")
    ok_path = output_path(f"station_name_ok_{ts}.csv")
    unknown_path = output_path(f"station_name_unknown_user_{ts}.csv")

    write_csv(mismatch_path, rows_bad, fieldnames)
    write_csv(ok_path, rows_ok, fieldnames)
    if rows_unknown_user:
        write_csv(unknown_path, rows_unknown_user, fieldnames)

    print()
    print("=" * 72)
    print("STATION NAME AUDIT")
    print(f"Expected pattern: {{FirstName LastName}}{STATION_SUFFIX}")
    print("=" * 72)
    print(f"Associated stations scanned: {len(associated)}")
    print(f"Correct:                     {len(rows_ok)}")
    print(f"Mismatches:                  {len(rows_bad)}")
    print(f"Unknown user:                {len(rows_unknown_user)}")
    print(f"\nMismatch report: {mismatch_path}")
    if rows_bad:
        print("\nMISMATCHES:")
        for row in sorted(rows_bad, key=lambda r: (r["user_name"] or r["email"]).lower()):
            print(
                f"  - {row['user_name']} ({row['email']})"
                f"\n      expected: {row['expected_station']!r}"
                f"\n      actual:   {row['actual_station']!r}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
