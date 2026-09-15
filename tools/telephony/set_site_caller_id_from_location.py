#!/usr/bin/env python3
"""
Set telephony site outbound caller ID from the associated location's
emergency number (ELIN) in E.164 format.

For each site:
- GET full site object
- read location.emergencyNumber.e164
- set callerId to that value (Genesys outbound caller address field)
- set callerName (default: Archwell Health)

Default: DRY RUN (CSV under outputs/).
Use --apply to perform site updates.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import (
    add_apply_arg,
    add_env_arg,
    create_client,
    output_path,
    print_mode_banner,
    tool_log,
    write_csv,
)
from core.genesys_client import ensure_e164, safe_str

DEFAULT_CALLER_NAME = "Archwell Health"

# Legacy/unused telephony sites — skip during bulk caller ID updates.
SKIP_UNUSED_SITES = frozenset({"Papago"})

PUT_STRIP_KEYS = (
    "id",
    "selfUri",
    "dateCreated",
    "dateModified",
    "createdBy",
    "modifiedBy",
    "createdByApp",
    "modifiedByApp",
    "version",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set site callerId from associated location emergency number (E.164)."
    )
    add_apply_arg(parser)
    add_env_arg(parser)
    parser.add_argument(
        "--caller-name",
        default=DEFAULT_CALLER_NAME,
        help=f'Outbound caller name (default: "{DEFAULT_CALLER_NAME}")',
    )
    parser.add_argument(
        "--center-sites-only",
        action="store_true",
        help="Only process 6-character center site codes (e.g. CLEELK).",
    )
    parser.add_argument(
        "--site",
        action="append",
        default=[],
        help="Limit to one or more site names (repeatable).",
    )
    return parser.parse_args()


def is_center_site_code(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9]{6}", safe_str(name).strip()))


def clean_put_payload(site: dict) -> dict:
    payload = json.loads(json.dumps(site))
    for key in PUT_STRIP_KEYS:
        payload.pop(key, None)
    return payload


def emergency_e164_from_site(site: dict) -> tuple[str, str]:
    """Return (e164, source_label) from embedded site location."""
    location = site.get("location") or {}
    emergency = location.get("emergencyNumber") or {}
    e164 = ensure_e164(emergency.get("e164") or emergency.get("number") or "")
    if e164:
        return e164, "site.location.emergencyNumber"
    return "", ""


def main() -> int:
    args = parse_args()
    client = create_client(log=tool_log, env_file=args.env_file)
    site_filter = {safe_str(name).strip().upper() for name in args.site if safe_str(name).strip()}

    print_mode_banner(
        "Set Site Caller ID From Location Emergency Number",
        apply=args.apply,
        caller_name=args.caller_name,
        scope="center sites only" if args.center_sites_only else "all sites",
        site_filter=", ".join(sorted(site_filter)) if site_filter else "(none)",
    )

    summaries = client.paged_get("/api/v2/telephony/providers/edges/sites")
    if site_filter:
        summaries = [s for s in summaries if safe_str(s.get("name")).strip().upper() in site_filter]
    if args.center_sites_only:
        summaries = [s for s in summaries if is_center_site_code(s.get("name", ""))]

    tool_log("info", f"Sites to evaluate: {len(summaries)}")

    rows: list[dict] = []
    counts: dict[str, int] = {}

    for index, summary in enumerate(summaries, 1):
        site_name = safe_str(summary.get("name")).strip()
        site_id = summary.get("id")
        row: dict = {
            "site_name": site_name,
            "site_id": site_id,
            "status": "",
            "current_caller_id": "",
            "target_caller_id": "",
            "current_caller_name": "",
            "target_caller_name": args.caller_name,
            "emergency_source": "",
            "location_name": "",
            "notes": "",
        }

        if site_name.upper() in {s.upper() for s in SKIP_UNUSED_SITES}:
            row["status"] = "SKIP_UNUSED_SITE"
            row["notes"] = "Legacy/unused site; not updated"
            counts[row["status"]] = counts.get(row["status"], 0) + 1
            rows.append(row)
            continue

        try:
            detail = client.request("GET", f"/api/v2/telephony/providers/edges/sites/{site_id}")
            location = detail.get("location") or {}
            row["location_name"] = safe_str(location.get("name"))

            if not location.get("id"):
                row["status"] = "SKIP_NO_LOCATION"
                row["notes"] = "Site has no associated location"
            else:
                target_caller_id, source = emergency_e164_from_site(detail)
                row["emergency_source"] = source
                row["target_caller_id"] = target_caller_id
                row["current_caller_id"] = safe_str(detail.get("callerId"))
                row["current_caller_name"] = safe_str(detail.get("callerName"))

                if not target_caller_id:
                    row["status"] = "SKIP_NO_EMERGENCY_NUMBER"
                    row["notes"] = "Location has no emergency number E.164"
                elif (
                    row["current_caller_id"] == target_caller_id
                    and row["current_caller_name"] == args.caller_name
                ):
                    row["status"] = "ALREADY_CORRECT"
                elif not args.apply:
                    row["status"] = "WOULD_UPDATE"
                    tool_log(
                        "dry",
                        f"[{index}/{len(summaries)}] {site_name}: "
                        f"{row['current_caller_id'] or '(blank)'} -> {target_caller_id}",
                    )
                else:
                    payload = clean_put_payload(detail)
                    payload["callerId"] = target_caller_id
                    payload["callerName"] = args.caller_name
                    client.request(
                        "PUT",
                        f"/api/v2/telephony/providers/edges/sites/{site_id}",
                        json_body=payload,
                    )
                    row["status"] = "UPDATED"
                    tool_log(
                        "ok",
                        f"[{index}/{len(summaries)}] {site_name}: "
                        f"{row['current_caller_id'] or '(blank)'} -> {target_caller_id}",
                    )
        except Exception as exc:
            row["status"] = "ERROR"
            row["notes"] = str(exc)
            tool_log("error", f"[{index}/{len(summaries)}] {site_name}: {exc}")

        counts[row["status"]] = counts.get(row["status"], 0) + 1
        rows.append(row)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = output_path(f"site_caller_id_from_location_{stamp}.csv")
    write_csv(
        out,
        rows,
        [
            "site_name",
            "site_id",
            "status",
            "current_caller_id",
            "target_caller_id",
            "current_caller_name",
            "target_caller_name",
            "emergency_source",
            "location_name",
            "notes",
        ],
    )

    print()
    tool_log("info", "Summary:")
    for status, count in sorted(counts.items()):
        tool_log("info", f"  {status}: {count}")
    tool_log("info", f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
