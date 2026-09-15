#!/usr/bin/env python3
"""
Export one-row-per-phone CSV for HR to assign center site codes.

Reads the main audit CSV from reassign_hq_phones_by_active_queue.py and
includes rows that need manual assignment (REVIEW_AMBIGUOUS_E911 and
SKIP_NO_JOINED_QUEUE).

HR fills in assigned_site_code (6-char code, e.g. GBOARC, PHLBRB).
Leave blank if the phone should stay at Headquarters.

Example:
  python tools/telephony/export_hq_phone_hr_template.py \\
    outputs/hq_phone_reassign_by_queue_20260630_144016.csv
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import create_client, output_path, read_csv, tool_log, write_csv

INCLUDE_ACTIONS = frozenset({"REVIEW_AMBIGUOUS_E911", "SKIP_NO_JOINED_QUEUE"})

TEMPLATE_FIELDS = [
    "phone_name",
    "phone_id",
    "user_name",
    "user_email",
    "user_id",
    "audit_status",
    "joined_true_count",
    "audit_notes",
    "assigned_site_code",
    "hr_notes",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Export HR site-assignment template CSV.")
    parser.add_argument(
        "audit_csv",
        help="Path to hq_phone_reassign_by_queue_*.csv from the dry run",
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="Include every HQ phone with a user, not just review/skip rows",
    )
    args = parser.parse_args()

    audit_path = Path(args.audit_csv)
    if not audit_path.exists():
        tool_log("error", f"File not found: {audit_path}")
        return 1

    rows_in = read_csv(audit_path)
    out_rows: list[dict] = []
    seen_phones: set[str] = set()

    for row in rows_in:
        action = (row.get("action") or "").strip()
        if not args.include_all and action not in INCLUDE_ACTIONS:
            continue
        phone_id = (row.get("phone_id") or "").strip()
        if not phone_id or phone_id in seen_phones:
            continue
        seen_phones.add(phone_id)
        out_rows.append({
            "phone_name": row.get("phone_name") or "",
            "phone_id": phone_id,
            "user_name": row.get("user_name") or "",
            "user_email": row.get("user_email") or "",
            "user_id": row.get("user_id") or "",
            "audit_status": action,
            "joined_true_count": row.get("joined_true_count") or row.get("joined_queue_count") or "",
            "audit_notes": row.get("notes") or "",
            "assigned_site_code": "",
            "hr_notes": "",
        })

    out_rows.sort(key=lambda r: (r["user_name"] or r["user_email"]).lower())

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    template_csv = output_path(f"hq_phone_hr_assignment_template_{ts}.csv")
    write_csv(template_csv, out_rows, TEMPLATE_FIELDS)

    # Reference list of valid 6-char site codes
    client = create_client()
    site_codes = sorted(
        (s.get("name") or "").upper()
        for s in client.paged_get("/api/v2/telephony/providers/edges/sites")
        if len((s.get("name") or "").strip()) == 6
    )
    ref_csv = output_path(f"hq_phone_valid_site_codes_{ts}.csv")
    write_csv(ref_csv, [{"site_code": c} for c in site_codes], ["site_code"])

    print()
    print("=" * 72)
    print("HR ASSIGNMENT TEMPLATE")
    print("=" * 72)
    print(f"Source audit:     {audit_path}")
    print(f"Rows for HR:      {len(out_rows)}")
    print(f"Template CSV:     {template_csv}")
    print(f"Valid site codes: {ref_csv} ({len(site_codes)} codes)")
    print()
    print("Instructions for HR:")
    print("  - Fill assigned_site_code with the 6-character center code (e.g. GBOARC)")
    print("  - Leave assigned_site_code blank to keep the phone at Headquarters")
    print("  - Optional: add hr_notes for context")
    print("=" * 72)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
