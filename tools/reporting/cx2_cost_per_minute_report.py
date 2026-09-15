#!/usr/bin/env python3
"""CX2 license cost-per-minute utilization report (handle time vs allocated license cost)."""
from __future__ import annotations

import argparse
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
from tools.reporting.spam_short_abandon_report import TZ_OFFSET_HOURS
from tools.reporting.staffing_metrics import metric_stats
from tools.users_roles.medical_assistant_telephony_align import (
    index_phones,
    load_phones,
    user_site,
)

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

CX2_LICENSE = "cloudCX2"
DEFAULT_MONTHLY_COST = 156.40
DEFAULT_DAYS_DAILY = 30
DEFAULT_DAYS_SUMMARY = 90
WASTE_THRESHOLD_MINUTES = 5.0
HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
WASTE_FILL = PatternFill("solid", fgColor="F4CCCC")
BOLD = Font(bold=True)
CURRENCY_FMT = '"$"#,##0.00'
MINUTES_FMT = "#,##0.00"
CPM_FMT = '"$"#,##0.0000'


@dataclass
class AgentContext:
    user_id: str
    name: str
    email: str
    title: str
    state: str
    site_name: str = ""
    site_code: str = ""
    market: str = ""


@dataclass
class HandleTotals:
    inbound_ms: int = 0
    outbound_ms: int = 0

    @property
    def total_ms(self) -> int:
        return self.inbound_ms + self.outbound_ms

    def inbound_minutes(self) -> float:
        return self.inbound_ms / 60_000

    def outbound_minutes(self) -> float:
        return self.outbound_ms / 60_000

    def total_minutes(self) -> float:
        return self.total_ms / 60_000


def rolling_period(days: int, *, end: date | None = None) -> tuple[list[date], date, date]:
    end_date = end or date.today()
    start_date = end_date - timedelta(days=days - 1)
    day_list = [start_date + timedelta(days=i) for i in range(days)]
    return day_list, start_date, end_date


def api_interval(start: date, end: date) -> str:
    start_iso = f"{start.isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    end_iso = f"{(end + timedelta(days=1)).isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    return f"{start_iso}/{end_iso}"


def prorated_cost(monthly_cost: float, days: int) -> float:
    return monthly_cost * (days / 30.0)


def daily_license_cost(monthly_cost: float) -> float:
    return monthly_cost / 30.0


def cost_per_minute(allocated_cost: float, handle_minutes: float) -> float | None:
    if handle_minutes <= 0:
        return None
    return allocated_cost / handle_minutes


def market_from_site(site_name: str) -> str:
    site = (site_name or "").strip().upper()
    if not site:
        return ""
    return site[:3]


def site_code_from_name(site_name: str) -> str:
    site = (site_name or "").strip().upper()
    if not site:
        return ""
    if len(site) >= 6:
        return site[:6]
    return site


def fetch_cx2_user_ids(client) -> set[str]:
    user_ids: set[str] = set()
    for row in client.paged_get("/api/v2/license/users"):
        licenses = row.get("licenses") or []
        if CX2_LICENSE not in licenses:
            continue
        uid = safe_str(row.get("id"))
        if uid:
            user_ids.add(uid)
    tool_log("info", f"Found {len(user_ids)} users with {CX2_LICENSE}")
    return user_ids


def load_agent_contexts(
    client,
    user_ids: set[str],
    site_by_user: dict[str, str],
    by_name: dict[str, list[dict]],
) -> dict[str, AgentContext]:
    contexts: dict[str, AgentContext] = {}
    for user in client.paged_get("/api/v2/users", params={"state": "any"}):
        uid = safe_str(user.get("id"))
        if uid not in user_ids:
            continue
        site = user_site(client, uid, safe_str(user.get("name")), site_by_user, by_name)
        contexts[uid] = AgentContext(
            user_id=uid,
            name=safe_str(user.get("name")) or uid,
            email=safe_str(user.get("email")),
            title=safe_str(user.get("title")),
            state=safe_str(user.get("state")) or "unknown",
            site_name=site or "NO_SITE",
            site_code=site_code_from_name(site) if site else "NO_SITE",
            market=market_from_site(site) if site else "NO_SITE",
        )

    missing = user_ids - set(contexts)
    for uid in sorted(missing):
        try:
            user = client.request("GET", f"/api/v2/users/{uid}")
        except Exception:
            contexts[uid] = AgentContext(
                user_id=uid,
                name=uid,
                email="",
                title="",
                state="unknown",
                site_name="NO_SITE",
                site_code="NO_SITE",
                market="NO_SITE",
            )
            continue
        site = user_site(client, uid, safe_str(user.get("name")), site_by_user, by_name)
        contexts[uid] = AgentContext(
            user_id=uid,
            name=safe_str(user.get("name")) or uid,
            email=safe_str(user.get("email")),
            title=safe_str(user.get("title")),
            state=safe_str(user.get("state")) or "unknown",
            site_name=site or "NO_SITE",
            site_code=site_code_from_name(site) if site else "NO_SITE",
            market=market_from_site(site) if site else "NO_SITE",
        )
    return contexts


def fetch_handle_ms(
    client,
    interval: str,
    direction: str,
    *,
    daily: bool = False,
) -> dict[Any, int]:
    body: dict[str, Any] = {
        "interval": interval,
        "groupBy": ["userId"],
        "filter": {
            "type": "and",
            "predicates": [
                {"type": "dimension", "dimension": "direction", "operator": "matches", "value": direction},
                {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "voice"},
            ],
        },
        "metrics": ["tHandle"],
    }
    if daily:
        body["granularity"] = "P1D"

    label = f"{direction} voice handle ({'daily' if daily else 'period'})"
    tool_log("info", f"Querying analytics: {label}")
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)

    totals: dict[Any, int] = defaultdict(int)
    for result in resp.get("results") or []:
        group = result.get("group") or {}
        user_id = safe_str(group.get("userId"))
        if not user_id:
            continue
        for bucket in result.get("data") or []:
            _count, handle_ms = metric_stats(bucket, "tHandle")
            if handle_ms <= 0:
                continue
            if daily:
                day = (bucket.get("interval") or "").split("/")[0][:10]
                if day:
                    totals[(user_id, day)] += handle_ms
            else:
                totals[user_id] += handle_ms
    tool_log("info", f"  {label}: {len(totals)} buckets")
    return dict(totals)


def merge_direction_totals(
    inbound: dict[Any, int],
    outbound: dict[Any, int],
) -> dict[Any, HandleTotals]:
    keys = set(inbound) | set(outbound)
    merged: dict[Any, HandleTotals] = {}
    for key in keys:
        merged[key] = HandleTotals(
            inbound_ms=inbound.get(key, 0),
            outbound_ms=outbound.get(key, 0),
        )
    return merged


def _format_sheet(ws, *, header_row: int = 1) -> None:
    for column_cells in ws.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells)
        letter = get_column_letter(column_cells[0].column)
        ws.column_dimensions[letter].width = min(max(width + 2, 12), 52)
    ws.freeze_panes = f"A{header_row + 1}"
    if ws.max_row >= header_row and ws.max_column >= 1:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(ws.max_column)}{ws.max_row}"


def _style_header(ws, row: int = 1) -> None:
    for cell in ws[row]:
        cell.font = BOLD
        cell.fill = HEADER_FILL


def write_workbook(
    path: Path,
    *,
    daily_rows: list[list[Any]],
    summary_rows: list[list[Any]],
    waste_rows: list[list[Any]],
    meta: dict[str, str],
) -> None:
    wb = Workbook()

    ws_about = wb.active
    ws_about.title = "About"
    ws_about.append(["Field", "Value"])
    for key, value in meta.items():
        ws_about.append([key, value])
    _format_sheet(ws_about, header_row=1)

    daily_headers = [
        "Date",
        "Agent Name",
        "Email",
        "User ID",
        "Job Title",
        "Market",
        "Center (Site Name)",
        "Site Code",
        "User State",
        "Inbound Handle Min",
        "Outbound Handle Min",
        "Total Handle Min",
        "Daily License Cost",
        "Cost Per Min (Total)",
        "Cost Per Min (Inbound)",
        "Cost Per Min (Outbound)",
    ]
    ws_daily = wb.create_sheet("Daily_30d")
    ws_daily.append(daily_headers)
    _style_header(ws_daily)
    for row in daily_rows:
        ws_daily.append(row)
    for row_idx in range(2, ws_daily.max_row + 1):
        ws_daily.cell(row_idx, 10).number_format = MINUTES_FMT
        ws_daily.cell(row_idx, 11).number_format = MINUTES_FMT
        ws_daily.cell(row_idx, 12).number_format = MINUTES_FMT
        ws_daily.cell(row_idx, 13).number_format = CURRENCY_FMT
        for col in (14, 15, 16):
            cell = ws_daily.cell(row_idx, col)
            if isinstance(cell.value, (int, float)):
                cell.number_format = CPM_FMT
    _format_sheet(ws_daily)

    summary_headers = [
        "Agent Name",
        "Email",
        "User ID",
        "Job Title",
        "Market",
        "Center (Site Name)",
        "Site Code",
        "User State",
        "Inbound Handle Min (90d)",
        "Outbound Handle Min (90d)",
        "Total Handle Min (90d)",
        "Allocated License Cost (90d)",
        "Cost Per Min (Total)",
        "Cost Per Min (Inbound)",
        "Cost Per Min (Outbound)",
    ]
    ws_summary = wb.create_sheet("Summary_90d")
    ws_summary.append(summary_headers)
    _style_header(ws_summary)
    for row in summary_rows:
        ws_summary.append(row)
    for row_idx in range(2, ws_summary.max_row + 1):
        for col in (9, 10, 11):
            ws_summary.cell(row_idx, col).number_format = MINUTES_FMT
        ws_summary.cell(row_idx, 12).number_format = CURRENCY_FMT
        for col in (13, 14, 15):
            cell = ws_summary.cell(row_idx, col)
            if isinstance(cell.value, (int, float)):
                cell.number_format = CPM_FMT
    _format_sheet(ws_summary)

    waste_headers = [
        "Agent Name",
        "Email",
        "User ID",
        "Job Title",
        "Market",
        "Center (Site Name)",
        "Site Code",
        "User State",
        "Total Handle Min (30d)",
        "Total Handle Min (90d)",
        "Monthly License Cost",
        "Waste Reason",
    ]
    ws_waste = wb.create_sheet("Inactive_Waste")
    ws_waste.append(waste_headers)
    _style_header(ws_waste)
    for row in waste_rows:
        ws_waste.append(row)
        for cell in ws_waste[ws_waste.max_row]:
            cell.fill = WASTE_FILL
    for row_idx in range(2, ws_waste.max_row + 1):
        ws_waste.cell(row_idx, 9).number_format = MINUTES_FMT
        ws_waste.cell(row_idx, 10).number_format = MINUTES_FMT
        ws_waste.cell(row_idx, 11).number_format = CURRENCY_FMT
    _format_sheet(ws_waste)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def build_daily_rows(
    agents: dict[str, AgentContext],
    day_list: list[date],
    daily_totals: dict[Any, HandleTotals],
    monthly_cost: float,
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    day_cost = daily_license_cost(monthly_cost)
    for day in day_list:
        day_key = day.isoformat()
        for uid in sorted(agents, key=lambda u: agents[u].name.lower()):
            agent = agents[uid]
            totals = daily_totals.get((uid, day_key), HandleTotals())
            inbound_min = round(totals.inbound_minutes(), 2)
            outbound_min = round(totals.outbound_minutes(), 2)
            total_min = round(totals.total_minutes(), 2)
            cpm_total = cost_per_minute(day_cost, total_min)
            cpm_in = cost_per_minute(day_cost, inbound_min)
            cpm_out = cost_per_minute(day_cost, outbound_min)
            rows.append([
                day_key,
                agent.name,
                agent.email,
                agent.user_id,
                agent.title,
                agent.market,
                agent.site_name,
                agent.site_code,
                agent.state,
                inbound_min,
                outbound_min,
                total_min,
                round(day_cost, 2),
                round(cpm_total, 4) if cpm_total is not None else "",
                round(cpm_in, 4) if cpm_in is not None else "",
                round(cpm_out, 4) if cpm_out is not None else "",
            ])
    return rows


def build_summary_rows(
    agents: dict[str, AgentContext],
    summary_totals: dict[str, HandleTotals],
    monthly_cost: float,
    summary_days: int,
) -> list[list[Any]]:
    period_cost = prorated_cost(monthly_cost, summary_days)
    rows: list[list[Any]] = []
    for uid in sorted(agents, key=lambda u: agents[u].name.lower()):
        agent = agents[uid]
        totals = summary_totals.get(uid, HandleTotals())
        inbound_min = round(totals.inbound_minutes(), 2)
        outbound_min = round(totals.outbound_minutes(), 2)
        total_min = round(totals.total_minutes(), 2)
        cpm_total = cost_per_minute(period_cost, total_min)
        cpm_in = cost_per_minute(period_cost, inbound_min)
        cpm_out = cost_per_minute(period_cost, outbound_min)
        rows.append([
            agent.name,
            agent.email,
            agent.user_id,
            agent.title,
            agent.market,
            agent.site_name,
            agent.site_code,
            agent.state,
            inbound_min,
            outbound_min,
            total_min,
            round(period_cost, 2),
            round(cpm_total, 4) if cpm_total is not None else "",
            round(cpm_in, 4) if cpm_in is not None else "",
            round(cpm_out, 4) if cpm_out is not None else "",
        ])
    rows.sort(
        key=lambda row: (
            float("inf") if row[12] == "" else row[12],
            row[0],
        ),
        reverse=True,
    )
    return rows


def user_window_minutes(
    daily_totals: dict[Any, HandleTotals],
    user_id: str,
    day_list: list[date],
) -> float:
    total_ms = 0
    for day in day_list:
        totals = daily_totals.get((user_id, day.isoformat()), HandleTotals())
        total_ms += totals.total_ms
    return total_ms / 60_000


def build_waste_rows(
    agents: dict[str, AgentContext],
    daily_totals: dict[Any, HandleTotals],
    day_list: list[date],
    summary_90_totals: dict[str, HandleTotals],
    monthly_cost: float,
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for uid in sorted(agents, key=lambda u: agents[u].name.lower()):
        agent = agents[uid]
        min_30 = user_window_minutes(daily_totals, uid, day_list)
        min_90 = summary_90_totals.get(uid, HandleTotals()).total_minutes()
        reasons: list[str] = []
        if min_30 < WASTE_THRESHOLD_MINUTES:
            reasons.append(f"<{WASTE_THRESHOLD_MINUTES:.0f} min in 30d")
        if min_90 < WASTE_THRESHOLD_MINUTES:
            reasons.append(f"<{WASTE_THRESHOLD_MINUTES:.0f} min in 90d")
        if not reasons:
            continue
        rows.append([
            agent.name,
            agent.email,
            agent.user_id,
            agent.title,
            agent.market,
            agent.site_name,
            agent.site_code,
            agent.state,
            round(min_30, 2),
            round(min_90, 2),
            round(monthly_cost, 2),
            "; ".join(reasons),
        ])
    rows.sort(key=lambda row: (row[9], row[8], row[0]))
    return rows


def send_report_email(report_path: Path, meta: dict[str, str], summary: dict[str, Any]) -> None:
    from tools.email.config import default_email_env_file

    mail_script = PROJECT_ROOT / "tools" / "email" / "gmail_send.py"
    env_file = default_email_env_file()
    subject = f"CX2 License Cost-Per-Minute Report ({meta['daily_start']} to {meta['daily_end']})"
    body = f"""Hi Doug,

Attached is the CX2 license cost-per-minute utilization report from Genesys Cloud.

Daily window: {meta['daily_start']} through {meta['daily_end']} ({meta['days_daily']} days)
Summary window: {meta['summary_start']} through {meta['summary_end']} ({meta['days_summary']} days)
CX2 monthly license cost: ${meta['monthly_cost']}
CX2 users: {summary['cx2_users']}
Daily rows: {summary['daily_rows']}
Summary rows: {summary['summary_rows']}
Inactive/waste seats (<{WASTE_THRESHOLD_MINUTES:.0f} min): {summary['waste_seats']}
Estimated monthly waste (${meta['monthly_cost']}/seat): ${summary['waste_monthly_cost']:.2f}

Top underutilized (highest $/min, 90d):
{summary['top_underutilized']}

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


def run_report(
    client,
    *,
    days_daily: int,
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
    agents = load_agent_contexts(client, cx2_ids, site_by_user, by_name)

    daily_days, daily_start, daily_end = rolling_period(days_daily)
    summary_days, summary_start, summary_end = rolling_period(days_summary)

    daily_interval = api_interval(daily_start, daily_end)
    summary_interval = api_interval(summary_start, summary_end)

    daily_inbound = fetch_handle_ms(client, daily_interval, "inbound", daily=True)
    daily_outbound = fetch_handle_ms(client, daily_interval, "outbound", daily=True)
    daily_totals = merge_direction_totals(daily_inbound, daily_outbound)

    summary_inbound = fetch_handle_ms(client, summary_interval, "inbound", daily=False)
    summary_outbound = fetch_handle_ms(client, summary_interval, "outbound", daily=False)
    summary_totals = merge_direction_totals(summary_inbound, summary_outbound)

    daily_rows = build_daily_rows(agents, daily_days, daily_totals, monthly_cost)
    summary_rows = build_summary_rows(agents, summary_totals, monthly_cost, days_summary)

    waste_rows = build_waste_rows(agents, daily_totals, daily_days, summary_totals, monthly_cost)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_path = output or output_path(f"cx2_cost_per_minute_{timestamp}.xlsx")

    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cx2_license": CX2_LICENSE,
        "monthly_cost": f"{monthly_cost:.2f}",
        "days_daily": str(days_daily),
        "daily_start": daily_start.isoformat(),
        "daily_end": daily_end.isoformat(),
        "days_summary": str(days_summary),
        "summary_start": summary_start.isoformat(),
        "summary_end": summary_end.isoformat(),
        "waste_threshold_minutes": str(WASTE_THRESHOLD_MINUTES),
        "handle_metric": "tHandle (talk + hold + ACW)",
    }

    write_workbook(
        report_path,
        daily_rows=daily_rows,
        summary_rows=summary_rows,
        waste_rows=waste_rows,
        meta=meta,
    )
    tool_log("ok", f"Saved {report_path}")

    ranked = sorted(
        summary_rows,
        key=lambda row: (
            float("inf") if row[12] == "" else row[12],
            row[0],
        ),
        reverse=True,
    )
    top_under = []
    for row in ranked[:10]:
        name = row[0]
        cpm = row[12]
        total_min = row[10]
        if cpm == "":
            top_under.append(f"  - {name}: no handle time (waste)")
        else:
            top_under.append(f"  - {name}: ${cpm:.4f}/min ({total_min} min)")

    waste_seats = len(waste_rows)
    exec_summary = {
        "cx2_users": len(agents),
        "daily_rows": len(daily_rows),
        "summary_rows": len(summary_rows),
        "waste_seats": waste_seats,
        "waste_monthly_cost": waste_seats * monthly_cost,
        "top_underutilized": "\n".join(top_under) if top_under else "  (none)",
    }
    return report_path, meta, exec_summary


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="CX2 license cost-per-minute utilization report.")
    add_env_arg(parser)
    parser.add_argument("--days-daily", type=int, default=DEFAULT_DAYS_DAILY, help="Daily tab window (default 30)")
    parser.add_argument("--days-summary", type=int, default=DEFAULT_DAYS_SUMMARY, help="Summary tab window (default 90)")
    parser.add_argument(
        "--cx2-monthly-cost",
        type=float,
        default=DEFAULT_MONTHLY_COST,
        help=f"Monthly CX2 license cost (default {DEFAULT_MONTHLY_COST})",
    )
    parser.add_argument("--output", default=None, help="Output XLSX path (default: outputs/cx2_cost_per_minute_<ts>.xlsx)")
    parser.add_argument("--email", action="store_true", help="Email report to doug.hendrickson@archwellhealth.com")
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    output = Path(args.output) if args.output else None
    report_path, meta, summary = run_report(
        client,
        days_daily=args.days_daily,
        days_summary=args.days_summary,
        monthly_cost=args.cx2_monthly_cost,
        output=output,
    )

    tool_log("info", f"CX2 users: {summary['cx2_users']}")
    tool_log("info", f"Daily rows: {summary['daily_rows']}")
    tool_log("info", f"Summary rows: {summary['summary_rows']}")
    tool_log("info", f"Waste seats: {summary['waste_seats']} (${summary['waste_monthly_cost']:.2f}/mo)")

    if args.email:
        send_report_email(report_path, meta, summary)
        tool_log("ok", "Emailed report to doug.hendrickson@archwellhealth.com")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
