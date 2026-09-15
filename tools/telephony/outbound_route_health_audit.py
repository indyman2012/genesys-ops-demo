#!/usr/bin/env python3
"""
Read-only audit: Default Outbound Route health across telephony sites.

Checks each site for:
  - Default Outbound Route exists
  - Route enabled
  - External trunk assigned

Status values: OK, MISSING, DISABLED, NO_TRUNK, ERROR

Example:
  python tools/telephony/outbound_route_health_audit.py
  python tools/telephony/outbound_route_health_audit.py --sites-csv outputs/site_filter.csv
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, read_csv, setup_path, tool_log, write_csv
from core.genesys_client import safe_str
from core.outbound_route_health import DEFAULT_ROUTE_NAME, STATUS_OK, verify_default_route

SKIP_SITE_NAMES = frozenset({"PureCloud Voice - AWS"})


def load_site_filter(csv_path: Path | None) -> set[str] | None:
    if not csv_path:
        return None
    rows = read_csv(csv_path)
    if not rows:
        raise SystemExit(f"No rows in site filter CSV: {csv_path}")

    codes: set[str] = set()
    for row in rows:
        code = (
            safe_str(row.get("site_code"))
            or safe_str(row.get("site_name"))
            or safe_str(row.get("name"))
        ).upper()
        if code:
            codes.add(code)
    if not codes:
        raise SystemExit(f"Site filter CSV has no site_code/site_name/name column values: {csv_path}")
    return codes


def phones_by_site_id(client) -> dict[str, int]:
    counts: dict[str, int] = {}
    phones = client.paged_get("/api/v2/telephony/providers/edges/phones", params={"pageSize": 100})
    for phone in phones:
        site = phone.get("site") or {}
        site_id = safe_str(site.get("id"))
        if site_id:
            counts[site_id] = counts.get(site_id, 0) + 1
    return counts


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Audit Default Outbound Route health across sites (read-only).")
    add_env_arg(parser)
    parser.add_argument(
        "--sites-csv",
        default=None,
        help="Optional CSV with site_code / site_name / name column to limit audit scope",
    )
    parser.add_argument(
        "--route-name",
        default=DEFAULT_ROUTE_NAME,
        help=f'Outbound route name to inspect (default: "{DEFAULT_ROUTE_NAME}")',
    )
    parser.add_argument(
        "--include-phone-count",
        action="store_true",
        help="Fetch per-site phone counts (slower; extra API paging)",
    )
    args = parser.parse_args()

    site_filter = load_site_filter(Path(args.sites_csv)) if args.sites_csv else None
    client = create_client(log=tool_log, env_file=args.env_file)

    tool_log("info", "Loading telephony sites...")
    sites = client.paged_get("/api/v2/telephony/providers/edges/sites")
    phone_counts: dict[str, int] = {}
    if args.include_phone_count:
        tool_log("info", "Loading phone inventory for per-site counts...")
        phone_counts = phones_by_site_id(client)

    if site_filter:
        sites = [s for s in sites if safe_str(s.get("name")).upper() in site_filter]
        tool_log("info", f"Filtered to {len(sites)} site(s) from {args.sites_csv}")

    results: list[dict] = []
    failing = 0

    print()
    print("=" * 72)
    print("OUTBOUND ROUTE HEALTH AUDIT — READ ONLY")
    print("=" * 72)

    for index, site in enumerate(sites, 1):
        site_name = safe_str(site.get("name"))
        site_id = safe_str(site.get("id"))
        if site_name in SKIP_SITE_NAMES:
            continue

        tool_log("info", f"[{index}/{len(sites)}] {site_name}")
        ok, note, route_eval = verify_default_route(client, site_id, route_name=args.route_name)

        location = site.get("location") or {}
        phone_count = str(phone_counts.get(site_id, 0)) if args.include_phone_count else ""

        row = {
            "site_code": site_name,
            "site_id": site_id,
            "location_name": safe_str(location.get("name")),
            "route_name": route_eval.get("route_name") or args.route_name,
            "route_id": route_eval.get("route_id") or "",
            "enabled": "yes" if route_eval.get("enabled") else "no",
            "trunk_names": route_eval.get("trunk_names") or "",
            "phone_count": phone_count,
            "status": route_eval.get("status") or "",
            "issues": route_eval.get("issues") or note,
        }
        results.append(row)

        if row["status"] != STATUS_OK:
            failing += 1
            tool_log("warn", f"  {site_name}: {row['status']} — {row['issues']}")

    results.sort(key=lambda r: (r["status"] != STATUS_OK, r["site_code"]))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = output_path(f"outbound_route_health_audit_{ts}.csv")
    fields = [
        "site_code",
        "site_id",
        "location_name",
        "route_name",
        "route_id",
        "enabled",
        "trunk_names",
        "phone_count",
        "status",
        "issues",
    ]
    write_csv(out_csv, results, fields)

    ok_count = sum(1 for r in results if r["status"] == STATUS_OK)
    print()
    print("=" * 72)
    print("SUMMARY")
    print(f"  Sites audited: {len(results)}")
    print(f"  OK: {ok_count}")
    print(f"  Failing: {failing}")
    print(f"  Report: {out_csv}")
    print("=" * 72)

    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
