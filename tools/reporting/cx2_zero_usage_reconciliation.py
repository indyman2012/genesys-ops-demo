#!/usr/bin/env python3
"""CX2 zero voice handle-time reconciliation report (90d tHandle = 0)."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log
from core.genesys_client import safe_str
from core.paths import PROJECT_ROOT
from tools.reporting.cx2_cost_per_minute_report import (
    CX2_LICENSE,
    DEFAULT_DAYS_SUMMARY,
    DEFAULT_MONTHLY_COST,
    HandleTotals,
    _format_sheet,
    _style_header,
    api_interval,
    cost_per_minute,
    fetch_cx2_user_ids,
    fetch_handle_ms,
    load_agent_contexts,
    merge_direction_totals,
    prorated_cost,
    rolling_period,
)
from tools.users_roles.medical_assistant_telephony_align import (
    index_phones,
    load_phones,
    normalize_phone_key,
    primary_phone_summary,
    user_site,
    work_extension,
)

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

BUCKET_FILLS = {
    "TRUE_WASTE": PatternFill("solid", fgColor="F4CCCC"),
    "PROVISIONING_GAP": PatternFill("solid", fgColor="FCE5CD"),
    "NON_VOICE_ROLE": PatternFill("solid", fgColor="D9EAD3"),
    "NEEDS_REVIEW": PatternFill("solid", fgColor="FFF2CC"),
}
CURRENCY_FMT = '"$"#,##0.00'
MINUTES_FMT = "#,##0.0"
BOLD = Font(bold=True)
TITLE_FONT = Font(bold=True, size=14)
SECTION_FILL = PatternFill("solid", fgColor="D9E1F2")

TELEPHONY_ROLE_NEEDLES = (
    "communicate",
    "agent",
    "telephony",
    "contact center",
    "purecloud user",
    "supervisor",
)

NON_VOICE_TITLE_PATTERNS = (
    r"\badmin(istrator)?\b",
    r"\bback[\s-]?office\b",
    r"\bbilling\b",
    r"\bcoder\b",
    r"\bcoding\b",
    r"\brevenue\b",
    r"\bfinance\b",
    r"\baccount(ing|ant)\b",
    r"\bhuman resources\b",
    r"\bhr\b",
    r"\bit\b",
    r"\binformation technology\b",
    r"\bexecutive\b",
    r"\bdirector\b",
    r"\bvp\b",
    r"\bvice president\b",
    r"\btrainer\b",
    r"\btraining\b",
    r"\bcompliance\b",
    r"\bmarketing\b",
    r"\brecruiter\b",
    r"\btalent\b",
    r"\bpayroll\b",
    r"\blegal\b",
    r"\bdata (analyst|engineer|scientist)\b",
    r"\bsoftware\b",
    r"\bdeveloper\b",
    r"\bproject manager\b",
    r"\bproduct manager\b",
    r"\bquality (analyst|improvement)\b",
    r"\bmedical records\b",
    r"\butilization review\b",
    r"\bpopulation health\b",
    r"\bcare management\b",
    r"\bnon[\s-]?clinical\b",
)
NON_VOICE_TITLE_RE = re.compile("|".join(NON_VOICE_TITLE_PATTERNS), re.I)

NON_VOICE_ROLE_PATTERNS = (
    r"\badministrator\b",
    r"\badmin\b",
    r"\banalyst\b",
    r"\bviewer\b",
    r"\bworkforce management\b",
    r"\bquality admin\b",
    r"\barchitect\b",
    r"\bpartner\b",
)
NON_VOICE_ROLE_RE = re.compile("|".join(NON_VOICE_ROLE_PATTERNS), re.I)

ZERO_HEADERS = [
    "Agent Name",
    "Email",
    "User ID",
    "Job Title",
    "User State",
    "Market",
    "Center / Site Code",
    "Site Name",
    "Phone Assigned",
    "Phone Name",
    "Phone Type",
    "Primary Voice Target",
    "WORK Extension",
    "Queue Count",
    "Queue Memberships",
    "Last Genesys Login",
    "Telephony Roles",
    "All Roles",
    "Recommended Bucket",
    "Monthly License Cost",
    "Notes",
]


@dataclass
class QueueMembership:
    queue_name: str
    joined: bool | None


@dataclass
class ZeroUserRow:
    agent_name: str
    email: str
    user_id: str
    title: str
    state: str
    market: str
    site_code: str
    site_name: str
    phone_assigned: bool
    phone_name: str
    phone_type: str
    primary_voice: str
    work_ext: str
    queue_memberships: list[QueueMembership] = field(default_factory=list)
    last_login: str = ""
    telephony_roles: str = ""
    all_roles: str = ""
    bucket: str = "NEEDS_REVIEW"
    monthly_cost: float = DEFAULT_MONTHLY_COST
    notes: str = ""

    @property
    def queue_count(self) -> int:
        return len(self.queue_memberships)

    def queue_list(self) -> str:
        parts: list[str] = []
        for item in sorted(self.queue_memberships, key=lambda q: q.queue_name.lower()):
            joined = "true" if item.joined is True else "false" if item.joined is False else "unknown"
            parts.append(f"{item.queue_name} (joined={joined})")
        return ", ".join(parts)

    def has_telephony_setup(self) -> bool:
        return self.phone_assigned or bool(self.work_ext) or self.queue_count > 0

    def to_xlsx_row(self) -> list[Any]:
        return [
            self.agent_name,
            self.email,
            self.user_id,
            self.title,
            self.state,
            self.market,
            self.site_code,
            self.site_name,
            "Y" if self.phone_assigned else "N",
            self.phone_name,
            self.phone_type,
            self.primary_voice,
            self.work_ext,
            self.queue_count,
            self.queue_list(),
            self.last_login,
            self.telephony_roles,
            self.all_roles,
            self.bucket,
            round(self.monthly_cost, 2),
            self.notes,
        ]


def index_associated_stations(client) -> tuple[set[str], dict[str, str]]:
    by_user: set[str] = set()
    names: dict[str, str] = {}
    for station in client.paged_get("/api/v2/stations", params={"pageSize": 100}):
        if (station.get("status") or "").upper() != "ASSOCIATED":
            continue
        uid = safe_str(station.get("userId") or station.get("webRtcUserId"))
        if not uid:
            continue
        by_user.add(uid)
        names[uid] = safe_str(station.get("name"))
    return by_user, names


def phone_type_label(phone_name: str) -> str:
    folded = (phone_name or "").casefold()
    if folded.endswith(" web") or "webrtc" in folded:
        return "Web"
    if phone_name:
        return "Other"
    return ""


def resolve_phone_assignment(
    user_id: str,
    user_name: str,
    *,
    phone_by_user: dict[str, dict],
    phone_by_name: dict[str, list[dict]],
    station_users: set[str],
    station_names: dict[str, str],
) -> tuple[bool, str]:
    if user_id in station_users:
        return True, station_names.get(user_id, "")
    phone = phone_by_user.get(user_id)
    if phone:
        return True, safe_str(phone.get("name"))
    named = phone_by_name.get(normalize_phone_key(f"{user_name.strip()} Web"), [])
    if named:
        return True, safe_str(named[0].get("name"))
    return False, ""


def build_phone_by_user(phones: list[dict]) -> dict[str, dict]:
    by_user: dict[str, dict] = {}
    for phone in phones:
        for key in ("webRtcUser", "owner", "assignedUser"):
            obj = phone.get(key) or {}
            if isinstance(obj, dict):
                uid = safe_str(obj.get("id"))
                if uid:
                    by_user[uid] = phone
    return by_user


def build_phone_by_name(phones: list[dict]) -> dict[str, list[dict]]:
    by_name: dict[str, list[dict]] = defaultdict(list)
    for phone in phones:
        name = safe_str(phone.get("name"))
        if name:
            by_name[normalize_phone_key(name)].append(phone)
    return dict(by_name)


def fetch_user_roles(client, user_id: str) -> list[str]:
    resp = client.request("GET", f"/api/v2/users/{user_id}/roles")
    roles = resp.get("roles") if isinstance(resp.get("roles"), list) else []
    names: list[str] = []
    for role in roles:
        if not isinstance(role, dict):
            continue
        name = safe_str(role.get("name")).strip()
        if name:
            names.append(name)
    return sorted(set(names))


def telephony_role_names(role_names: list[str]) -> list[str]:
    matched: list[str] = []
    for name in role_names:
        folded = name.casefold()
        if any(needle in folded for needle in TELEPHONY_ROLE_NEEDLES):
            matched.append(name)
    return matched


def is_non_voice_title(title: str) -> bool:
    return bool(NON_VOICE_TITLE_RE.search(title or ""))


def is_non_voice_roles(role_names: list[str]) -> bool:
    for name in role_names:
        if NON_VOICE_ROLE_RE.search(name):
            return True
    return False


def classify_bucket(row: ZeroUserRow) -> tuple[str, str]:
    notes: list[str] = []
    has_setup = row.has_telephony_setup()
    non_voice_title = is_non_voice_title(row.title)
    non_voice_roles = is_non_voice_roles([r.strip() for r in row.all_roles.split(";") if r.strip()])

    if non_voice_title:
        notes.append("Title suggests non-voice role")
    if non_voice_roles:
        notes.append("Role set suggests non-voice/admin work")

    if (non_voice_title or non_voice_roles) and not has_setup:
        if row.state.lower() == "inactive":
            notes.append("Inactive with no telephony footprint")
        return "NON_VOICE_ROLE", "; ".join(notes)

    if has_setup:
        detail: list[str] = []
        if row.phone_assigned:
            detail.append("phone assigned")
        if row.work_ext:
            detail.append("WORK extension present")
        if row.queue_count:
            detail.append(f"{row.queue_count} queue(s)")
        notes.append("Has telephony setup but zero 90d handle: " + ", ".join(detail))
        return "PROVISIONING_GAP", "; ".join(notes)

    if row.state.lower() == "inactive":
        notes.append("Inactive CX2 seat with no telephony setup")
    else:
        notes.append("Active CX2 seat with no phone, extension, or queues")

    return "TRUE_WASTE", "; ".join(notes)


def build_queue_memberships(client, target_user_ids: set[str]) -> dict[str, list[QueueMembership]]:
    memberships: dict[str, list[QueueMembership]] = defaultdict(list)
    queues = client.paged_get("/api/v2/routing/queues")
    total = len(queues)
    for index, queue in enumerate(queues, 1):
        qid = safe_str(queue.get("id"))
        qname = safe_str(queue.get("name")) or qid
        if index == 1 or index % 25 == 0 or index == total:
            tool_log("info", f"Scanning queue memberships {index}/{total}...")
        for member in client.paged_get(f"/api/v2/routing/queues/{qid}/members"):
            user = member.get("user") or {}
            uid = safe_str(user.get("id") or member.get("userId"))
            if uid not in target_user_ids:
                continue
            memberships[uid].append(
                QueueMembership(
                    queue_name=qname,
                    joined=member.get("joined") if isinstance(member.get("joined"), bool) else None,
                )
            )
    return dict(memberships)


def last_login_from_user(user: dict) -> str:
    for key in ("dateLastLogin", "lastLogin", "dateModified"):
        value = safe_str(user.get(key))
        if value:
            return value[:19].replace("T", " ")
    return ""


def zero_handle_user_ids(
    cx2_ids: set[str],
    summary_totals: dict[str, HandleTotals],
) -> set[str]:
    zero_ids: set[str] = set()
    for uid in cx2_ids:
        totals = summary_totals.get(uid, HandleTotals())
        if totals.total_ms <= 0:
            zero_ids.add(uid)
    return zero_ids


def build_zero_rows(
    client,
    *,
    agents: dict,
    zero_ids: set[str],
    users_by_id: dict[str, dict],
    phone_by_user: dict[str, dict],
    phone_by_name: dict[str, list[dict]],
    station_users: set[str],
    station_names: dict[str, str],
    site_by_user: dict[str, str],
    queue_memberships: dict[str, list[QueueMembership]],
    monthly_cost: float,
) -> list[ZeroUserRow]:
    rows: list[ZeroUserRow] = []
    total = len(zero_ids)
    for index, uid in enumerate(sorted(zero_ids, key=lambda u: agents[u].name.lower()), 1):
        if index == 1 or index % 100 == 0 or index == total:
            tool_log("info", f"Enriching zero-usage users {index}/{total}...")
        agent = agents[uid]
        user = users_by_id.get(uid) or {}
        has_phone, phone_name = resolve_phone_assignment(
            uid,
            agent.name,
            phone_by_user=phone_by_user,
            phone_by_name=phone_by_name,
            station_users=station_users,
            station_names=station_names,
        )
        site = user_site(client, uid, agent.name, site_by_user, phone_by_name)
        if site and site != agent.site_name:
            site_name = site
            site_code = site[:6].upper() if len(site) >= 6 else site.upper()
            market = site[:3].upper() if site else agent.market
        else:
            site_name = agent.site_name
            site_code = agent.site_code
            market = agent.market

        roles = fetch_user_roles(client, uid)
        telephony_roles = telephony_role_names(roles)
        row = ZeroUserRow(
            agent_name=agent.name,
            email=agent.email,
            user_id=uid,
            title=agent.title,
            state=agent.state,
            market=market,
            site_code=site_code,
            site_name=site_name,
            phone_assigned=has_phone,
            phone_name=phone_name,
            phone_type=phone_type_label(phone_name),
            primary_voice=primary_phone_summary(user),
            work_ext=work_extension(user),
            queue_memberships=queue_memberships.get(uid, []),
            last_login=last_login_from_user(user),
            telephony_roles="; ".join(telephony_roles),
            all_roles="; ".join(roles),
            monthly_cost=monthly_cost,
        )
        row.bucket, row.notes = classify_bucket(row)
        rows.append(row)
    return rows


def compute_org_cpm_metrics(
    cx2_ids: set[str],
    summary_totals: dict[str, HandleTotals],
    *,
    zero_ids: set[str],
    monthly_cost: float,
    days_summary: int,
) -> dict[str, Any]:
    """Org-weighted $/min: total allocated license cost / total handle minutes."""
    per_user_cost = prorated_cost(monthly_cost, days_summary)

    total_minutes_all = sum(
        summary_totals.get(uid, HandleTotals()).total_minutes()
        for uid in cx2_ids
    )
    total_allocated_all = len(cx2_ids) * per_user_cost
    cpm_full = cost_per_minute(total_allocated_all, total_minutes_all)

    usage_ids = cx2_ids - zero_ids
    total_minutes_usage = sum(
        summary_totals.get(uid, HandleTotals()).total_minutes()
        for uid in usage_ids
    )
    total_allocated_usage = len(usage_ids) * per_user_cost
    cpm_usage = cost_per_minute(total_allocated_usage, total_minutes_usage)

    return {
        "per_user_allocated_cost": per_user_cost,
        "cx2_user_count": len(cx2_ids),
        "usage_user_count": len(usage_ids),
        "zero_user_count": len(zero_ids),
        "total_handle_minutes_all": total_minutes_all,
        "total_handle_minutes_usage": total_minutes_usage,
        "total_allocated_all": total_allocated_all,
        "total_allocated_usage": total_allocated_usage,
        "cpm_full_org": cpm_full,
        "cpm_with_usage_only": cpm_usage,
    }


def _append_section_header(ws, title: str) -> None:
    ws.append([title, ""])
    for cell in ws[ws.max_row]:
        cell.font = BOLD
        cell.fill = SECTION_FILL


def _fmt_cpm_display(value: float | None) -> str:
    return f"${value:.2f}/min" if value is not None else "n/a"


def _append_metric_row(ws, label: str, value: Any, *, number_format: str | None = None) -> None:
    ws.append([label, value])
    if number_format:
        ws.cell(ws.max_row, 2).number_format = number_format


def write_front_summary_sheet(
    ws,
    *,
    meta: dict[str, str],
    org_metrics: dict[str, Any],
    zero_rows: list[ZeroUserRow],
    bucket_summary: list[list[Any]],
    true_waste_count: int,
    monthly_cost: float,
) -> None:
    ws.append(["CX2 Zero-Usage Reconciliation Report", ""])
    ws["A1"].font = TITLE_FONT
    ws.append([])

    _append_section_header(ws, "Report Parameters")
    _append_metric_row(ws, "Report period", f"{meta['summary_start']} to {meta['summary_end']} ({meta['days_summary']} days)")
    _append_metric_row(ws, "CX2 monthly license cost", monthly_cost, number_format=CURRENCY_FMT)
    _append_metric_row(ws, "CX2 users (licensed)", org_metrics["cx2_user_count"])
    _append_metric_row(ws, "Handle metric", meta["handle_metric"])
    _append_metric_row(ws, "Zero-usage definition", meta["zero_usage_definition"])
    ws.append([])

    _append_section_header(ws, "Org Average Cost Per Minute (90d, org-weighted)")
    _append_metric_row(ws, "Full org (all CX2 users)", _fmt_cpm_display(org_metrics["cpm_full_org"]))
    _append_metric_row(
        ws,
        "  Total allocated license cost (90d)",
        org_metrics["total_allocated_all"],
        number_format=CURRENCY_FMT,
    )
    _append_metric_row(
        ws,
        "  Total handle minutes (all CX2)",
        org_metrics["total_handle_minutes_all"],
        number_format=MINUTES_FMT,
    )
    _append_metric_row(
        ws,
        "With usage only (exclude zero-handle)",
        _fmt_cpm_display(org_metrics["cpm_with_usage_only"]),
    )
    _append_metric_row(ws, "  Users with handle > 0", org_metrics["usage_user_count"])
    _append_metric_row(
        ws,
        "  Total allocated license cost (90d)",
        org_metrics["total_allocated_usage"],
        number_format=CURRENCY_FMT,
    )
    _append_metric_row(
        ws,
        "  Total handle minutes (with usage)",
        org_metrics["total_handle_minutes_usage"],
        number_format=MINUTES_FMT,
    )
    ws.append([])

    _append_section_header(ws, "Zero-Usage Summary")
    zero_count = len(zero_rows)
    _append_metric_row(ws, "Zero-handle users (90d)", zero_count)
    _append_metric_row(ws, "Monthly license cost at risk (all zero-handle)", zero_count * monthly_cost, number_format=CURRENCY_FMT)
    _append_metric_row(
        ws,
        "Estimated reclaimable (TRUE_WASTE only)",
        true_waste_count * monthly_cost,
        number_format=CURRENCY_FMT,
    )
    _append_metric_row(ws, "TRUE_WASTE seat count", true_waste_count)
    ws.append([])

    _append_section_header(ws, "Recommended Bucket Counts")
    ws.append(["Bucket", "Users", "Monthly Cost"])
    for cell in ws[ws.max_row]:
        cell.font = BOLD
        cell.fill = SECTION_FILL
    for row in bucket_summary:
        if row[0] == "TOTAL (all zero-handle)":
            continue
        ws.append([row[0], row[1], row[2]])
        ws.cell(ws.max_row, 3).number_format = CURRENCY_FMT

    ws.column_dimensions["A"].width = 48
    ws.column_dimensions["B"].width = 28
    ws.freeze_panes = "A2"


def write_workbook(
    path: Path,
    *,
    zero_rows: list[ZeroUserRow],
    meta: dict[str, str],
    bucket_summary: list[list[Any]],
    org_metrics: dict[str, Any],
    true_waste_count: int,
    monthly_cost: float,
) -> None:
    wb = Workbook()
    ws_front = wb.active
    ws_front.title = "Summary"
    write_front_summary_sheet(
        ws_front,
        meta=meta,
        org_metrics=org_metrics,
        zero_rows=zero_rows,
        bucket_summary=bucket_summary,
        true_waste_count=true_waste_count,
        monthly_cost=monthly_cost,
    )

    ws_zero = wb.create_sheet("Zero_Usage_90d")
    ws_zero.append(ZERO_HEADERS)
    _style_header(ws_zero)
    for row in zero_rows:
        ws_zero.append(row.to_xlsx_row())
        fill = BUCKET_FILLS.get(row.bucket)
        if fill:
            for cell in ws_zero[ws_zero.max_row]:
                cell.fill = fill
    for row_idx in range(2, ws_zero.max_row + 1):
        ws_zero.cell(row_idx, 20).number_format = CURRENCY_FMT
    _format_sheet(ws_zero)

    ws_summary = wb.create_sheet("Bucket_Summary")
    ws_summary.append(["Recommended Bucket", "User Count", "Monthly License Cost", "Annual License Cost"])
    _style_header(ws_summary)
    for row in bucket_summary:
        ws_summary.append(row)
    for row_idx in range(2, ws_summary.max_row + 1):
        ws_summary.cell(row_idx, 3).number_format = CURRENCY_FMT
        ws_summary.cell(row_idx, 4).number_format = CURRENCY_FMT
    _format_sheet(ws_summary)

    ws_about = wb.create_sheet("About")
    ws_about.append(["Field", "Value"])
    for key, value in meta.items():
        ws_about.append([key, value])
    _format_sheet(ws_about, header_row=1)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def bucket_summary_rows(zero_rows: list[ZeroUserRow], monthly_cost: float) -> list[list[Any]]:
    counts: dict[str, int] = defaultdict(int)
    for row in zero_rows:
        counts[row.bucket] += 1
    order = ["TRUE_WASTE", "PROVISIONING_GAP", "NON_VOICE_ROLE", "NEEDS_REVIEW"]
    summary: list[list[Any]] = []
    for bucket in order:
        count = counts.get(bucket, 0)
        if count == 0:
            continue
        monthly = count * monthly_cost
        summary.append([bucket, count, round(monthly, 2), round(monthly * 12, 2)])
    other = sorted(set(counts) - set(order))
    for bucket in other:
        count = counts[bucket]
        monthly = count * monthly_cost
        summary.append([bucket, count, round(monthly, 2), round(monthly * 12, 2)])
    total_count = len(zero_rows)
    total_monthly = total_count * monthly_cost
    summary.append(["TOTAL (all zero-handle)", total_count, round(total_monthly, 2), round(total_monthly * 12, 2)])
    return summary


def send_report_email(
    report_path: Path,
    meta: dict[str, str],
    summary: dict[str, Any],
) -> None:
    from tools.email.config import default_email_env_file

    mail_script = PROJECT_ROOT / "tools" / "email" / "gmail_send.py"
    env_file = default_email_env_file()
    subject = f"CX2 Zero-Usage Reconciliation ({meta['summary_start']} to {meta['summary_end']})"
    body = f"""Hi Doug,

Attached is the CX2 zero voice handle-time reconciliation report (90-day tHandle sum = 0).

Window: {meta['summary_start']} through {meta['summary_end']} ({meta['days_summary']} days)
CX2 monthly license cost: ${meta['monthly_cost']}
CX2 users scanned: {summary['cx2_users']}
Zero-handle users: {summary['zero_users']}

Org average cost per minute (90d, org-weighted):
  Full org (all CX2): ${summary['cpm_full_org']:.2f}/min
  With usage only: ${summary['cpm_with_usage_only']:.2f}/min

Bucket counts:
{summary['bucket_lines']}

Estimated reclaimable (TRUE_WASTE only): ${summary['true_waste_monthly']:.2f}/mo (${summary['true_waste_annual']:.2f}/yr)

File: {report_path.name}

— GenesysCloudToolkit
"""
    cmd = [
        sys.executable,
        str(mail_script),
        "--env-file",
        str(env_file),
        "--to",
        "doug.hendrickson@archwellhealth.com",
        "--subject",
        subject,
        "--body",
        body,
        "--attach",
        str(report_path),
    ]
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def load_users_by_id(client, user_ids: set[str]) -> dict[str, dict]:
    users: dict[str, dict] = {}
    for user in client.paged_get("/api/v2/users", params={"state": "any"}):
        uid = safe_str(user.get("id"))
        if uid in user_ids:
            users[uid] = user
    missing = user_ids - set(users.keys())
    for uid in sorted(missing):
        try:
            users[uid] = client.request("GET", f"/api/v2/users/{uid}")
        except Exception:
            users[uid] = {"id": uid}
    return users


def run_report(
    client,
    *,
    days_summary: int,
    monthly_cost: float,
    output: Path | None,
) -> tuple[Path, dict[str, str], dict[str, Any]]:
    cx2_ids = fetch_cx2_user_ids(client)
    if not cx2_ids:
        raise SystemExit(f"No users found with license {CX2_LICENSE}")

    tool_log("info", "Loading phone/site directory")
    phones = load_phones(client)
    site_by_user, by_name = index_phones(phones)
    phone_by_user = build_phone_by_user(phones)
    phone_by_name = build_phone_by_name(phones)
    station_users, station_names = index_associated_stations(client)
    agents = load_agent_contexts(client, cx2_ids, site_by_user, by_name)

    _, summary_start, summary_end = rolling_period(days_summary)
    summary_interval = api_interval(summary_start, summary_end)
    summary_inbound = fetch_handle_ms(client, summary_interval, "inbound", daily=False)
    summary_outbound = fetch_handle_ms(client, summary_interval, "outbound", daily=False)
    summary_totals = merge_direction_totals(summary_inbound, summary_outbound)

    zero_ids = zero_handle_user_ids(cx2_ids, summary_totals)
    tool_log("info", f"Zero-handle CX2 users (90d): {len(zero_ids)}")

    users_by_id = load_users_by_id(client, zero_ids)
    queue_memberships = build_queue_memberships(client, zero_ids)
    zero_rows = build_zero_rows(
        client,
        agents=agents,
        zero_ids=zero_ids,
        users_by_id=users_by_id,
        phone_by_user=phone_by_user,
        phone_by_name=phone_by_name,
        station_users=station_users,
        station_names=station_names,
        site_by_user=site_by_user,
        queue_memberships=queue_memberships,
        monthly_cost=monthly_cost,
    )

    org_metrics = compute_org_cpm_metrics(
        cx2_ids,
        summary_totals,
        zero_ids=zero_ids,
        monthly_cost=monthly_cost,
        days_summary=days_summary,
    )
    def _fmt_cpm(value: float | None) -> str:
        return f"${value:.2f}/min" if value is not None else "n/a"

    tool_log(
        "info",
        f"Org avg $/min — full org: {_fmt_cpm(org_metrics['cpm_full_org'])}, "
        f"with usage: {_fmt_cpm(org_metrics['cpm_with_usage_only'])}",
    )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = output or output_path(f"cx2_zero_usage_reconciliation_{timestamp}.xlsx")
    bucket_rows = bucket_summary_rows(zero_rows, monthly_cost)

    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cx2_license": CX2_LICENSE,
        "monthly_cost": f"{monthly_cost:.2f}",
        "days_summary": str(days_summary),
        "summary_start": summary_start.isoformat(),
        "summary_end": summary_end.isoformat(),
        "zero_usage_definition": "Sum of voice tHandle (inbound + outbound) = 0 over summary window",
        "excluded_from_report": "Minimal usage (<5 min) users remain in cost-per-minute report",
        "handle_metric": "tHandle (talk + hold + ACW)",
    }

    bucket_counts = defaultdict(int)
    for row in zero_rows:
        bucket_counts[row.bucket] += 1
    true_waste = bucket_counts.get("TRUE_WASTE", 0)

    write_workbook(
        report_path,
        zero_rows=zero_rows,
        meta=meta,
        bucket_summary=bucket_rows,
        org_metrics=org_metrics,
        true_waste_count=true_waste,
        monthly_cost=monthly_cost,
    )
    tool_log("ok", f"Saved {report_path}")
    bucket_lines = "\n".join(
        f"  - {bucket}: {count} (${count * monthly_cost:.2f}/mo)"
        for bucket, count in sorted(bucket_counts.items())
    )
    exec_summary = {
        "cx2_users": len(cx2_ids),
        "zero_users": len(zero_rows),
        "bucket_counts": dict(bucket_counts),
        "bucket_lines": bucket_lines or "  (none)",
        "true_waste_count": true_waste,
        "true_waste_monthly": true_waste * monthly_cost,
        "true_waste_annual": true_waste * monthly_cost * 12,
        "cpm_full_org": org_metrics["cpm_full_org"] or 0.0,
        "cpm_with_usage_only": org_metrics["cpm_with_usage_only"] or 0.0,
    }
    return report_path, meta, exec_summary


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="CX2 zero voice handle-time reconciliation report.")
    add_env_arg(parser)
    parser.add_argument("--days-summary", type=int, default=DEFAULT_DAYS_SUMMARY, help="Analytics window (default 90)")
    parser.add_argument(
        "--cx2-monthly-cost",
        type=float,
        default=DEFAULT_MONTHLY_COST,
        help=f"Monthly CX2 license cost (default {DEFAULT_MONTHLY_COST})",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output XLSX path (default: outputs/cx2_zero_usage_reconciliation_<ts>.xlsx)",
    )
    parser.add_argument("--email", action="store_true", help="Email report to doug.hendrickson@archwellhealth.com")
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    output = Path(args.output) if args.output else None
    report_path, meta, summary = run_report(
        client,
        days_summary=args.days_summary,
        monthly_cost=args.cx2_monthly_cost,
        output=output,
    )

    tool_log("info", f"CX2 users scanned: {summary['cx2_users']}")
    tool_log("info", f"Zero-handle users: {summary['zero_users']}")
    for bucket, count in sorted(summary["bucket_counts"].items()):
        tool_log("info", f"  {bucket}: {count}")
    cpm_full = summary["cpm_full_org"]
    cpm_usage = summary["cpm_with_usage_only"]
    tool_log(
        "info",
        f"Org avg $/min — full org: ${cpm_full:.2f}/min, with usage: ${cpm_usage:.2f}/min",
    )
    tool_log(
        "info",
        f"Reclaimable TRUE_WASTE: {summary['true_waste_count']} seats "
        f"(${summary['true_waste_monthly']:.2f}/mo)",
    )

    if args.email:
        send_report_email(report_path, meta, summary)
        tool_log("ok", "Emailed report to doug.hendrickson@archwellhealth.com")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
