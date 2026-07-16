#!/usr/bin/env python3
"""Care Navigators joined (active) on more than one Genesys queue — daily snapshot."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log
from core.genesys_client import safe_str

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
BOLD = Font(bold=True)
DEFAULT_TITLE_NEEDLE = "Care Navigator"
DEFAULT_MIN_JOINED_QUEUES = 2


def user_title(user: dict[str, Any]) -> str:
    title = user.get("title")
    if title:
        return safe_str(title)
    profile = user.get("profile") or {}
    if isinstance(profile, dict):
        for key in ("title", "jobTitle"):
            if profile.get(key):
                return safe_str(profile.get(key))
    return ""


def title_matches(user: dict[str, Any], needle: str) -> bool:
    return needle.casefold() in user_title(user).casefold()


def fetch_active_users(client) -> list[dict[str, Any]]:
    return client.paged_get("/api/v2/users", params={"state": "active"})


def joined_queues_for_user(client, user_id: str) -> list[dict[str, Any]]:
    rows = client.paged_get(f"/api/v2/users/{user_id}/queues")
    joined = [row for row in rows if row.get("joined") is True]
    joined.sort(key=lambda item: safe_str(item.get("name")).lower())
    return joined


def build_rows(
    client,
    *,
    title_needle: str,
    min_joined_queues: int,
) -> list[dict[str, Any]]:
    users = fetch_active_users(client)
    matches = [user for user in users if title_matches(user, title_needle)]
    tool_log("info", f"Active users: {len(users)}; title match '{title_needle}': {len(matches)}")

    rows: list[dict[str, Any]] = []
    for index, user in enumerate(matches, 1):
        uid = safe_str(user.get("id"))
        if not uid:
            continue
        if index == 1 or index % 10 == 0 or index == len(matches):
            tool_log("info", f"Checking joined queues {index}/{len(matches)}...")
        joined = joined_queues_for_user(client, uid)
        if len(joined) < min_joined_queues:
            continue
        queue_names = [safe_str(q.get("name") or q.get("id")) for q in joined]
        rows.append({
            "user_id": uid,
            "name": safe_str(user.get("name")) or uid,
            "email": safe_str(user.get("email")),
            "title": user_title(user),
            "joined_queue_count": len(joined),
            "joined_queues": " | ".join(queue_names),
        })

    rows.sort(key=lambda item: (-item["joined_queue_count"], item["name"].lower()))
    return rows


def write_report_meta(path: Path, payload: dict[str, Any]) -> Path:
    meta_path = path.parent / f"{path.stem}_meta.json"
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return meta_path


def write_workbook(path: Path, rows: list[dict[str, Any]], meta: dict[str, str]) -> None:
    wb = Workbook()
    ws_about = wb.active
    ws_about.title = "About"
    ws_about.append(["Field", "Value"])
    for key, value in meta.items():
        ws_about.append([key, value])

    ws = wb.create_sheet("Agents")
    headers = [
        "Name",
        "Email",
        "Title",
        "Joined Queue Count",
        "Joined Queues",
        "User ID",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    for row in rows:
        ws.append([
            row["name"],
            row["email"],
            row["title"],
            row["joined_queue_count"],
            row["joined_queues"],
            row["user_id"],
        ])

    for column_cells in ws.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 80)
    ws.freeze_panes = "A2"

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(
        description=(
            "List active Genesys users whose title contains a phrase and who are "
            "joined=true on more than one queue."
        ),
    )
    parser.add_argument(
        "--title-contains",
        default=DEFAULT_TITLE_NEEDLE,
        help=f'Case-insensitive title match (default: "{DEFAULT_TITLE_NEEDLE}")',
    )
    parser.add_argument(
        "--min-joined-queues",
        type=int,
        default=DEFAULT_MIN_JOINED_QUEUES,
        help="Minimum joined=true queue count to include (default: 2 = more than one queue)",
    )
    parser.add_argument("--output", default=None, help="Output .xlsx path")
    add_env_arg(parser)
    args = parser.parse_args()

    if args.min_joined_queues < 2:
        raise SystemExit("--min-joined-queues must be at least 2 for this report")

    client = create_client(log=tool_log, env_file=args.env_file)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    rows = build_rows(
        client,
        title_needle=args.title_contains,
        min_joined_queues=args.min_joined_queues,
    )
    tool_log("info", f"Agents with joined>={args.min_joined_queues} queues: {len(rows)}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output else output_path(
        f"care_navigator_multi_queue_{stamp}.xlsx"
    )
    meta = {
        "Report": "Care Navigator multi-queue join snapshot",
        "Generated At": generated_at,
        "Title Filter": args.title_contains,
        "User State": "ACTIVE",
        "Queue Membership": "joined=true",
        "Minimum Joined Queues": str(args.min_joined_queues),
        "Agents Matching": str(len(rows)),
        "Purpose": "Daily compliance check — Care Navigators should not be active on multiple queues",
    }
    write_workbook(out_path, rows, meta)
    write_report_meta(
        out_path,
        {
            "generated_at": generated_at,
            "title_contains": args.title_contains,
            "min_joined_queues": args.min_joined_queues,
            "agents_matching": len(rows),
            "report_path": str(out_path),
        },
    )
    tool_log("ok", f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
