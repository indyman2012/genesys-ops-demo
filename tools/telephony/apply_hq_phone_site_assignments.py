#!/usr/bin/env python3
"""
Apply HR-reviewed center site assignments to Headquarters WebRTC phones.

Reads a CSV with assigned_site_code filled in (from export_hq_phone_hr_template.py).
Validates each code against Genesys telephony sites, then moves the phone site.

Default: DRY RUN. Use --apply to perform updates.

Example:
  python tools/telephony/apply_hq_phone_site_assignments.py \\
    outputs/hq_phone_hr_assignment_template_REVIEWED.csv
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import (
    add_apply_arg,
    add_env_arg,
    create_client,
    output_path,
    read_csv,
    tool_log,
    write_csv,
)

DEFAULT_HQ_SITE = "Headquarters"


def build_phone_update_payload(phone: dict, target_site: dict) -> dict:
    payload = json.loads(json.dumps(phone))
    payload["site"] = {"id": target_site["id"], "name": target_site["name"]}
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply HR-reviewed HQ phone site assignments.")
    add_apply_arg(parser)
    add_env_arg(parser)
    parser.add_argument(
        "reviewed_csv",
        help="HR-reviewed CSV with assigned_site_code column filled in",
    )
    parser.add_argument(
        "--hq-site",
        default=DEFAULT_HQ_SITE,
        help="Expected current site name (default Headquarters)",
    )
    args = parser.parse_args()

    reviewed_path = Path(args.reviewed_csv)
    if not reviewed_path.exists():
        tool_log("error", f"File not found: {reviewed_path}")
        return 1

    rows_in = read_csv(reviewed_path)
    if not rows_in:
        tool_log("error", "CSV is empty")
        return 1

    client = create_client(env_file=args.env_file)

    sites = client.paged_get("/api/v2/telephony/providers/edges/sites")
    site_by_code = {
        (s.get("name") or "").upper(): s
        for s in sites
        if (s.get("name") or "").strip()
    }
    hq_site = next((s for s in sites if (s.get("name") or "").lower() == args.hq_site.lower()), None)
    if not hq_site:
        tool_log("error", f'HQ site "{args.hq_site}" not found')
        return 1

    results: list[dict] = []
    counts: dict[str, int] = {}

    print()
    print("=" * 72)
    print(f"APPLY HR SITE ASSIGNMENTS — {'APPLY' if args.apply else 'DRY RUN'}")
    print("=" * 72)

    for row in rows_in:
        phone_id = (row.get("phone_id") or "").strip()
        phone_name = (row.get("phone_name") or "").strip()
        user_name = (row.get("user_name") or "").strip()
        user_email = (row.get("user_email") or "").strip()
        assigned = (row.get("assigned_site_code") or "").strip().upper()
        hr_notes = (row.get("hr_notes") or "").strip()

        result = {
            "phone_name": phone_name,
            "phone_id": phone_id,
            "user_name": user_name,
            "user_email": user_email,
            "assigned_site_code": assigned,
            "target_site_name": "",
            "target_site_id": "",
            "action": "",
            "error": "",
            "hr_notes": hr_notes,
        }

        if not phone_id:
            result["action"] = "SKIP_INVALID_ROW"
            result["error"] = "Missing phone_id"
            counts[result["action"]] = counts.get(result["action"], 0) + 1
            results.append(result)
            continue

        if not assigned:
            result["action"] = "SKIP_STAY_AT_HQ"
            counts[result["action"]] = counts.get(result["action"], 0) + 1
            results.append(result)
            continue

        if len(assigned) != 6:
            result["action"] = "SKIP_INVALID_CODE"
            result["error"] = f"Site code must be 6 characters, got {assigned!r}"
            counts[result["action"]] = counts.get(result["action"], 0) + 1
            print(f"\nINVALID: {phone_name} — code {assigned!r}")
            results.append(result)
            continue

        target = site_by_code.get(assigned)
        if not target:
            result["action"] = "SKIP_UNKNOWN_SITE"
            result["error"] = f"No telephony site named {assigned}"
            counts[result["action"]] = counts.get(result["action"], 0) + 1
            print(f"\nUNKNOWN SITE: {phone_name} — {assigned}")
            results.append(result)
            continue

        result["target_site_name"] = target.get("name") or assigned
        result["target_site_id"] = target.get("id") or ""

        try:
            detail = client.request("GET", f"/api/v2/telephony/providers/edges/phones/{phone_id}")
        except Exception as exc:
            result["action"] = "SKIP_PHONE_NOT_FOUND"
            result["error"] = str(exc)
            counts[result["action"]] = counts.get(result["action"], 0) + 1
            results.append(result)
            continue

        current_site = detail.get("site") or {}
        current_site_id = current_site.get("id") or ""
        if current_site_id == result["target_site_id"]:
            result["action"] = "SKIP_ALREADY_ON_TARGET"
            counts[result["action"]] = counts.get(result["action"], 0) + 1
            results.append(result)
            continue

        if args.apply:
            payload = build_phone_update_payload(detail, target)
            try:
                client.request(
                    "PUT",
                    f"/api/v2/telephony/providers/edges/phones/{phone_id}",
                    json_body=payload,
                    expected_statuses=[200, 202],
                )
                result["action"] = "UPDATED"
            except Exception as exc:
                result["action"] = "FAILED"
                result["error"] = str(exc)
        else:
            result["action"] = "WOULD_MOVE"

        counts[result["action"]] = counts.get(result["action"], 0) + 1
        print(f"\n{result['action']}: {phone_name}")
        print(f"  User: {user_name} ({user_email})")
        print(f"  {current_site.get('name') or args.hq_site} -> {assigned}")
        if hr_notes:
            print(f"  HR note: {hr_notes}")
        results.append(result)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = output_path(f"hq_phone_hr_apply_results_{ts}.csv")
    write_csv(out_csv, results, list(results[0].keys()) if results else [
        "phone_name", "phone_id", "user_name", "user_email",
        "assigned_site_code", "target_site_name", "target_site_id",
        "action", "error", "hr_notes",
    ])

    print()
    print("=" * 72)
    print("SUMMARY")
    for action, cnt in sorted(counts.items()):
        print(f"  {action}: {cnt}")
    print(f"\nResults CSV: {out_csv}")
    print("=" * 72)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
