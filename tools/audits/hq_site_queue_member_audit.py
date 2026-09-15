#!/usr/bin/env python3
"""
Audit queue members (joined or not) whose WebRTC/Web phone is on Headquarters.

Read-only. Outputs XLSX under outputs/ with one row per queue membership.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log
from core.genesys_client import safe_str
from core.paths import PROJECT_ROOT

from tools.audits.sales_hq_phone_audit import (
    build_phone_indexes,
    find_web_phone,
    normalize_phone_key,
    phone_site_name,
)
from tools.telephony.reassign_hq_phones_by_active_queue import DEFAULT_HQ_SITE

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
BOLD = Font(bold=True)

OUTPUT_FIELDS = [
    "agent_name",
    "email",
    "user_id",
    "job_title",
    "phone_name",
    "site_name",
    "site_code",
    "queue_name",
    "joined",
]

WORK_EMAIL = "doug.hendrickson@archwellhealth.com"


@dataclass
class QueueMembership:
    queue_name: str
    joined: bool


def is_hq_site(site_name: str, *, hq_site: str) -> bool:
    name = (site_name or "").strip().upper()
    hq = (hq_site or DEFAULT_HQ_SITE).strip().upper()
    return name == hq or name == "HQ"


def site_code_for_phone(phone: dict | None, sites_by_id: dict[str, dict]) -> str:
    if not phone:
        return ""
    site = phone.get("site") or {}
    sid = safe_str(site.get("id"))
    name = safe_str(site.get("name"))
    if len(name) == 6:
        return name.upper()
    if sid and sid in sites_by_id:
        mapped = safe_str(sites_by_id[sid].get("name"))
        if len(mapped) == 6:
            return mapped.upper()
    return name


def scan_all_queue_memberships(client) -> tuple[dict[str, list[QueueMembership]], dict[str, dict]]:
    """Return (user_id -> memberships, user_id -> stub from member payload)."""
    memberships: dict[str, list[QueueMembership]] = defaultdict(list)
    user_stubs: dict[str, dict] = {}
    queues = client.paged_get("/api/v2/routing/queues")
    total = len(queues)
    tool_log("info", f"Scanning members across {total} queues...")
    for index, queue in enumerate(queues, 1):
        qid = safe_str(queue.get("id"))
        qname = safe_str(queue.get("name")) or qid
        if index == 1 or index % 25 == 0 or index == total:
            tool_log("info", f"  Queue members {index}/{total}: {qname[:60]}")
        for member in client.paged_get(f"/api/v2/routing/queues/{qid}/members"):
            user = member.get("user") or {}
            uid = safe_str(user.get("id") or member.get("userId"))
            if not uid:
                continue
            joined = member.get("joined") is True
            memberships[uid].append(QueueMembership(queue_name=qname, joined=joined))
            if uid not in user_stubs:
                user_stubs[uid] = {
                    "id": uid,
                    "name": safe_str(user.get("name")),
                    "email": safe_str(user.get("email")),
                }
    return dict(memberships), user_stubs


def load_user_details(client, uid: str, stub: dict) -> dict:
    try:
        detail = client.request("GET", f"/api/v2/users/{uid}")
        return {
            "name": safe_str(detail.get("name")) or stub.get("name") or uid,
            "email": safe_str(detail.get("email")) or stub.get("email") or "",
            "title": safe_str(detail.get("title")),
        }
    except Exception:
        return {
            "name": stub.get("name") or uid,
            "email": stub.get("email") or "",
            "title": "",
        }


def write_workbook(path: Path, rows: list[dict], *, meta: dict[str, str]) -> None:
    wb = Workbook()
    ws_summary = wb.active
    ws_summary.title = "Summary"
    ws_summary.append(["HQ Site Queue Member Audit"])
    ws_summary["A1"].font = BOLD
    for key, value in meta.items():
        ws_summary.append([key, value])

    ws = wb.create_sheet("HQ Queue Members")
    ws.append(OUTPUT_FIELDS)
    for cell in ws[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    for row in rows:
        ws.append([row.get(field, "") for field in OUTPUT_FIELDS])
    for column_cells in ws.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 56)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    for column_cells in ws_summary.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells)
        ws_summary.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 56)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def send_report_email(report_path: Path, *, unique_agents: int, row_count: int) -> bool:
    from tools.email.config import DEFAULT_ENV_DOUG

    mail_script = PROJECT_ROOT / "tools" / "email" / "gmail_send.py"
    env_file = DEFAULT_ENV_DOUG
    subject = "HQ Web Phone Queue Member Audit"
    body = f"""Hi Doug,

Attached is the queue-member audit for agents whose Web phone is on Headquarters.

Unique agents: {unique_agents}
Rows (one per queue membership): {row_count}

File: {report_path.name}

— GenesysCloudToolkit
"""
    cmd = [
        sys.executable,
        str(mail_script),
        "--env-file",
        str(env_file),
        "--to",
        WORK_EMAIL,
        "--subject",
        subject,
        "--body",
        body,
        "--attach",
        str(report_path),
    ]
    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    return result.returncode == 0


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(
        description="List queue members (any joined state) with Web phone on Headquarters.",
    )
    add_env_arg(parser)
    parser.add_argument("--hq-site", default=DEFAULT_HQ_SITE, help="Headquarters site name")
    parser.add_argument("--output", default=None, help="Output .xlsx path")
    parser.add_argument(
        "--email",
        action="store_true",
        help=f"Email report to {WORK_EMAIL}",
    )
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)

    tool_log("info", "Loading telephony sites...")
    sites = client.paged_get("/api/v2/telephony/providers/edges/sites")
    sites_by_id = {safe_str(s.get("id")): s for s in sites if s.get("id")}
    hq_names = {safe_str(s.get("name")) for s in sites if is_hq_site(safe_str(s.get("name")), hq_site=args.hq_site)}
    tool_log("info", f"HQ site name(s) in org: {', '.join(sorted(hq_names)) or args.hq_site}")

    memberships, user_stubs = scan_all_queue_memberships(client)
    tool_log("info", f"Found {len(memberships)} users with any queue membership")

    tool_log("info", "Loading phones and building indexes...")
    phones = client.paged_get("/api/v2/telephony/providers/edges/phones", params={"pageSize": 100})
    by_user, by_name = build_phone_indexes(client, phones, set(memberships.keys()))

    hq_user_ids: list[str] = []
    phone_by_user: dict[str, dict | None] = {}
    for uid in memberships:
        stub = user_stubs.get(uid, {"id": uid, "name": "", "email": ""})
        phone = find_web_phone(uid, stub.get("name") or "", by_user, by_name)
        site = phone_site_name(phone)
        if is_hq_site(site, hq_site=args.hq_site):
            hq_user_ids.append(uid)
            phone_by_user[uid] = phone

    tool_log("info", f"{len(hq_user_ids)} queue members have Web phone on {args.hq_site}")

    user_cache: dict[str, dict] = {}
    rows: list[dict] = []
    for uid in sorted(hq_user_ids, key=lambda u: (user_stubs.get(u, {}).get("name") or u).casefold()):
        if uid not in user_cache:
            user_cache[uid] = load_user_details(client, uid, user_stubs.get(uid, {}))
        user = user_cache[uid]
        phone = phone_by_user.get(uid)
        site_name = phone_site_name(phone) or args.hq_site
        site_code = site_code_for_phone(phone, sites_by_id)
        phone_name = safe_str((phone or {}).get("name"))

        for membership in sorted(memberships[uid], key=lambda m: m.queue_name.casefold()):
            rows.append({
                "agent_name": user["name"],
                "email": user["email"],
                "user_id": uid,
                "job_title": user["title"],
                "phone_name": phone_name,
                "site_name": site_name,
                "site_code": site_code,
                "queue_name": membership.queue_name,
                "joined": membership.joined,
            })

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else output_path(f"hq_site_queue_member_audit_{stamp}.xlsx")

    unique_agents = len(hq_user_ids)
    joined_rows = sum(1 for r in rows if r["joined"] is True)
    not_joined_rows = len(rows) - joined_rows
    meta = {
        "Generated At (UTC)": datetime.now(timezone.utc).isoformat(),
        "HQ Site Filter": args.hq_site,
        "Users With Any Queue Membership": str(len(memberships)),
        "HQ Web Phone Queue Members": str(unique_agents),
        "Output Rows": str(len(rows)),
        "Joined=true Rows": str(joined_rows),
        "Joined=false Rows": str(not_joined_rows),
        "Web Phone Pattern": "{Name} Web or webRtcUser link",
    }
    write_workbook(out, rows, meta=meta)

    tool_log("ok", f"Wrote {out}")
    tool_log("info", f"Unique agents: {unique_agents} | Rows: {len(rows)}")

    if args.email:
        tool_log("info", f"Emailing report to {WORK_EMAIL} via doug.env...")
        if send_report_email(out, unique_agents=unique_agents, row_count=len(rows)):
            tool_log("ok", "Email sent")
        else:
            tool_log("warn", "Email failed — report saved locally; retry with gmail_send.py + doug.env")

    print()
    print("=" * 80)
    print("HQ SITE QUEUE MEMBER AUDIT")
    print(f"HQ site: {args.hq_site}")
    print(f"Unique agents on HQ Web phone with queue membership: {unique_agents}")
    print(f"Output rows (one per queue): {len(rows)}")
    print("=" * 80)
    if rows:
        print("\nSample rows:")
        for row in rows[:5]:
            joined = "joined" if row["joined"] else "not joined"
            print(
                f"  {row['agent_name']} | {row['queue_name'][:40]} | {joined} | {row['phone_name']}"
            )
    print(f"\nXLSX: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
