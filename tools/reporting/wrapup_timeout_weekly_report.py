#!/usr/bin/env python3
"""Weekly agent report for ININ-WRAP-UP-TIMEOUT wrap-up incidence (prior Mon-Sun)."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log
from core.lookups import get_all_queues
from tools.reporting.direct_outbound_agents_report import previous_calendar_week
from tools.reporting.spam_short_abandon_report import TZ_OFFSET_HOURS

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

# Genesys ACW-timeout system placeholder; analytics id is ININ-WRAP-UP-TIMEOUT (not in routing/wrapupcodes).
WRAPUP_CODE_ANALYTICS_ID = "ININ-WRAP-UP-TIMEOUT"
WRAPUP_CODE_DISPLAY = "ININ-WRAP-UP-TIMEOUT"
MIN_AGENT_COUNT = 10
UNKNOWN_QUEUE_NAME = "(No Queue)"
HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
SUMMARY_FILL = PatternFill("solid", fgColor="FFF2CC")
BOLD = Font(bold=True)


def week_api_interval(start: date, end: date) -> str:
    start_iso = f"{start.isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    end_iso = f"{(end + timedelta(days=1)).isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    return f"{start_iso}/{end_iso}"


def metric_count(bucket: dict[str, Any]) -> int:
    for metric in bucket.get("metrics") or []:
        stats = metric.get("stats") or {}
        count = int(stats.get("count") or 0)
        if count:
            return count
    return 0


def fetch_timeout_counts_by_agent(client, interval: str, wrapup_id: str) -> dict[str, int]:
    body = {
        "interval": interval,
        "groupBy": ["userId", "wrapUpCode"],
        "filter": {
            "type": "and",
            "predicates": [
                {"type": "dimension", "dimension": "wrapUpCode", "operator": "matches", "value": wrapup_id},
            ],
        },
        "metrics": ["tAcw", "tHandle", "tTalk"],
    }
    tool_log("info", f'Querying "{WRAPUP_CODE_DISPLAY}" aggregates by agent')
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)
    counts: dict[str, int] = {}
    for result in resp.get("results") or []:
        group = result.get("group") or {}
        user_id = (group.get("userId") or "").strip()
        if not user_id:
            continue
        for bucket in result.get("data") or []:
            counts[user_id] = counts.get(user_id, 0) + metric_count(bucket)
    return counts


def fetch_timeout_counts_by_queue(client, interval: str, wrapup_id: str) -> dict[str, int]:
    body = {
        "interval": interval,
        "groupBy": ["queueId", "wrapUpCode"],
        "filter": {
            "type": "and",
            "predicates": [
                {"type": "dimension", "dimension": "wrapUpCode", "operator": "matches", "value": wrapup_id},
            ],
        },
        "metrics": ["tAcw", "tHandle", "tTalk"],
    }
    tool_log("info", f'Querying "{WRAPUP_CODE_DISPLAY}" aggregates by queue')
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)
    counts: dict[str, int] = {}
    for result in resp.get("results") or []:
        group = result.get("group") or {}
        queue_id = (group.get("queueId") or "").strip()
        for bucket in result.get("data") or []:
            counts[queue_id] = counts.get(queue_id, 0) + metric_count(bucket)
    return counts


def load_queue_names(client) -> dict[str, str]:
    return {
        (q.get("id") or ""): (q.get("name") or q.get("id") or "")
        for q in get_all_queues(client)
    }


def build_queue_rows(counts: dict[str, int], names: dict[str, str]) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    for queue_id, count in counts.items():
        if count <= 0:
            continue
        if queue_id:
            label = names.get(queue_id, queue_id)
        else:
            label = UNKNOWN_QUEUE_NAME
        rows.append((label, count))
    return sorted(rows, key=lambda item: (-item[1], item[0].lower()))


def load_user_names(client, user_ids: set[str]) -> dict[str, str]:
    names: dict[str, str] = {}
    tool_log("info", "Loading user directory for name resolution")
    for user in client.paged_get("/api/v2/users", params={"state": "any"}):
        uid = (user.get("id") or "").strip()
        if uid in user_ids:
            names[uid] = (user.get("name") or uid).strip()
    missing = user_ids - set(names)
    for uid in sorted(missing):
        try:
            user = client.request("GET", f"/api/v2/users/{uid}")
            names[uid] = (user.get("name") or uid).strip()
        except Exception:
            names[uid] = uid
    return names


def build_rows(client, counts: dict[str, int], *, min_count: int = MIN_AGENT_COUNT) -> list[tuple[str, int]]:
    if not counts:
        return []
    names = load_user_names(client, set(counts))
    rows = [
        (names.get(uid, uid), count)
        for uid, count in counts.items()
        if count >= min_count
    ]
    return sorted(rows, key=lambda item: (-item[1], item[0].lower()))


def _format_sheet(ws) -> None:
    for column_cells in ws.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 48)
    ws.freeze_panes = "A2"


def write_workbook(
    path: Path,
    agent_rows: list[tuple[str, int]],
    queue_rows: list[tuple[str, int]],
    total: int,
    meta: dict[str, str],
) -> None:
    wb = Workbook()
    ws_about = wb.active
    ws_about.title = "About"
    ws_about.append(["Field", "Value"])
    for key, value in meta.items():
        ws_about.append([key, value])

    headers = ["Name", f"{WRAPUP_CODE_DISPLAY} Count"]
    ws = wb.create_sheet("Agents")
    ws.append(headers)
    for cell in ws[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    ws.append(["ORG TOTAL", total])
    for cell in ws[2]:
        cell.font = BOLD
        cell.fill = SUMMARY_FILL
    for name, count in agent_rows:
        ws.append([name, count])
    _format_sheet(ws)

    queue_headers = ["Queue", f"{WRAPUP_CODE_DISPLAY} Count"]
    ws_queue = wb.create_sheet("By Queue")
    ws_queue.append(queue_headers)
    for cell in ws_queue[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    ws_queue.append(["ORG TOTAL", total])
    for cell in ws_queue[2]:
        cell.font = BOLD
        cell.fill = SUMMARY_FILL
    for name, count in queue_rows:
        ws_queue.append([name, count])
    _format_sheet(ws_queue)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def write_report_meta(path: Path, payload: dict[str, Any]) -> Path:
    meta_path = path.parent / f"{path.stem}_meta.json"
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return meta_path


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Weekly ININ-WRAP-UP-TIMEOUT incidence by agent")
    parser.add_argument("--output", default=None, help="Output .xlsx path")
    add_env_arg(parser)
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    days, start_label, end_label = previous_calendar_week()
    interval = week_api_interval(date.fromisoformat(start_label), date.fromisoformat(end_label))
    tool_log("info", f"Window: {start_label} through {end_label}")

    counts = fetch_timeout_counts_by_agent(client, interval, WRAPUP_CODE_ANALYTICS_ID)
    queue_counts = fetch_timeout_counts_by_queue(client, interval, WRAPUP_CODE_ANALYTICS_ID)
    queue_names = load_queue_names(client)
    org_total = sum(counts.values())
    agents_with_any = sum(1 for count in counts.values() if count > 0)
    agent_rows = build_rows(client, counts)
    queue_rows = build_queue_rows(queue_counts, queue_names)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else output_path(f"wrapup_timeout_weekly_{stamp}.xlsx")
    meta = {
        "Generated At (UTC)": datetime.now(timezone.utc).isoformat(),
        "Window Start": start_label,
        "Window End": end_label,
        "Period": "previous-week",
        "Wrap-Up Code": WRAPUP_CODE_DISPLAY,
        "Analytics Identifier": WRAPUP_CODE_ANALYTICS_ID,
        "Definition": (
            "Genesys system placeholder when ACW expires before the agent selects a wrap-up; "
            "counted via analytics aggregates (agents by userId, queues by queueId)"
        ),
        "Day Boundary": f"UTC-{TZ_OFFSET_HOURS:02d} (US Eastern DST)",
        "Analytics Interval": interval,
        "Minimum Agent Count": str(MIN_AGENT_COUNT),
        "Agents With Any Timeout": str(agents_with_any),
        "Agents Listed": str(len(agent_rows)),
        "Queues Listed": str(len(queue_rows)),
        "Total Timeout Wrap-Ups": str(org_total),
        "Sort Order": "Highest incidence first (Agents sheet min 10 per agent)",
    }
    write_workbook(out, agent_rows, queue_rows, org_total, meta)
    write_report_meta(
        out,
        {
            "report_path": str(out),
            "window_start": start_label,
            "window_end": end_label,
            "min_agent_count": MIN_AGENT_COUNT,
            "agents_with_any": agents_with_any,
            "agents_listed": len(agent_rows),
            "queues_listed": len(queue_rows),
            "total_timeout_wrapups": org_total,
            "wrapup_code": WRAPUP_CODE_DISPLAY,
        },
    )
    tool_log("ok", f"Wrote {out}")
    if agent_rows:
        top = agent_rows[0]
        tool_log("info", f"Top agent: {top[0]} ({top[1]} timeout wrap-ups)")
    else:
        tool_log("warn", f"No agents with {MIN_AGENT_COUNT}+ timeout wrap-ups in window")
    if queue_rows:
        top_queue = queue_rows[0]
        tool_log("info", f"Top queue: {top_queue[0]} ({top_queue[1]} timeout wrap-ups)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
