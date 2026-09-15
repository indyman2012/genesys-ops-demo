#!/usr/bin/env python3
"""Weekly repeat inbound caller report by queue with week-over-week comparison."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log
from core.lookups import get_all_queues
from tools.reporting.direct_outbound_agents_report import day_interval, previous_calendar_week
from tools.reporting.spam_short_abandon_report import TZ_OFFSET_HOURS, local_day_key, parse_dt
from tools.reporting.spam_short_abandon_weekly_report import (
    UNKNOWN_QUEUE_ID,
    UNKNOWN_QUEUE_NAME,
    extract_inbound_ani,
    normalize_ani,
    resolve_queue_id,
)

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
SUMMARY_FILL = PatternFill("solid", fgColor="FFF2CC")
BOLD = Font(bold=True)
MIN_REPEAT_CONTACTS = 2


@dataclass
class QueueRepeatStats:
    queue_id: str
    queue_name: str
    repeat_anis: int = 0
    repeat_contacts: int = 0


@dataclass
class AniRepeatRow:
    queue_id: str
    queue_name: str
    ani: str
    count: int


def calendar_week_before(end: date) -> tuple[list[date], str, str]:
    start = end - timedelta(days=6)
    days = [start + timedelta(days=i) for i in range(7)]
    return days, start.isoformat(), end.isoformat()


def reporting_weeks() -> tuple[list[date], str, str, list[date], str, str]:
    current_days, cur_start, cur_end = previous_calendar_week()
    prior_end = date.fromisoformat(cur_start) - timedelta(days=1)
    prior_days, prev_start, prev_end = calendar_week_before(prior_end)
    return current_days, cur_start, cur_end, prior_days, prev_start, prev_end


def week_api_interval(start: date, end: date) -> str:
    start_iso = f"{start.isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    end_iso = f"{(end + timedelta(days=1)).isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    return f"{start_iso}/{end_iso}"


def queue_lookup(client) -> dict[str, str]:
    return {(q.get("id") or ""): (q.get("name") or q.get("id") or "") for q in get_all_queues(client)}


def scan_repeat_contacts(client, days: list[date], names: dict[str, str]) -> dict[str, dict[str, int]]:
    """Return queue_id -> ani -> contact count for inbound voice."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    body_base = {
        "order": "asc",
        "orderBy": "conversationStart",
        "segmentFilters": [
            {
                "type": "and",
                "predicates": [
                    {"type": "dimension", "dimension": "direction", "operator": "matches", "value": "inbound"},
                    {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "voice"},
                ],
            }
        ],
    }
    for index, day in enumerate(days, 1):
        interval = day_interval(day)
        page = 1
        day_count = 0
        while True:
            body = {**body_base, "interval": interval, "paging": {"pageSize": 100, "pageNumber": page}}
            resp = client.request("POST", "/api/v2/analytics/conversations/details/query", json_body=body)
            convs = resp.get("conversations") or []
            if not convs:
                break
            for conv in convs:
                start = parse_dt(conv.get("conversationStart"))
                if not start or local_day_key(start) != day.isoformat():
                    continue
                ani = extract_inbound_ani(conv)
                if not ani:
                    continue
                qid = resolve_queue_id(conv) or UNKNOWN_QUEUE_ID
                counts[qid][ani] += 1
                day_count += 1
            if len(convs) < 100:
                break
            page += 1
        if index % 2 == 0 or index == len(days):
            tool_log("info", f"Repeat-caller scan {index}/{len(days)} days — {day.isoformat()}: {day_count}")
    return counts


def summarize_queue_stats(
    counts: dict[str, dict[str, int]],
    names: dict[str, str],
) -> dict[str, QueueRepeatStats]:
    stats: dict[str, QueueRepeatStats] = {}
    for qid, ani_map in counts.items():
        queue_name = names.get(qid, UNKNOWN_QUEUE_NAME if qid == UNKNOWN_QUEUE_ID else qid)
        repeat_anis = 0
        repeat_contacts = 0
        for _ani, count in ani_map.items():
            if count >= MIN_REPEAT_CONTACTS:
                repeat_anis += 1
                repeat_contacts += count
        stats[qid] = QueueRepeatStats(
            queue_id=qid,
            queue_name=queue_name,
            repeat_anis=repeat_anis,
            repeat_contacts=repeat_contacts,
        )
    return stats


def build_detail_rows(
    current: dict[str, dict[str, int]],
    prior: dict[str, dict[str, int]],
    names: dict[str, str],
) -> list[tuple[str, str, str, int, int]]:
    rows: list[tuple[str, str, str, int, int]] = []
    all_keys: set[tuple[str, str]] = set()
    for qid, ani_map in current.items():
        for ani, count in ani_map.items():
            if count >= MIN_REPEAT_CONTACTS:
                all_keys.add((qid, ani))
    for qid, ani_map in prior.items():
        for ani, count in ani_map.items():
            if count >= MIN_REPEAT_CONTACTS:
                all_keys.add((qid, ani))
    for qid, ani in sorted(all_keys, key=lambda item: (names.get(item[0], item[0]).lower(), item[1])):
        cur = current.get(qid, {}).get(ani, 0)
        prev = prior.get(qid, {}).get(ani, 0)
        if cur < MIN_REPEAT_CONTACTS and prev < MIN_REPEAT_CONTACTS:
            continue
        rows.append((names.get(qid, qid), ani, qid, cur, prev))
    rows.sort(key=lambda row: (-row[3], row[0].lower(), row[1]))
    return rows


def write_workbook(
    path: Path,
    queue_rows: list[QueueRepeatStats],
    summary: QueueRepeatStats,
    detail_rows: list[tuple[str, str, str, int, int]],
    meta: dict[str, str],
) -> None:
    wb = Workbook()
    ws_about = wb.active
    ws_about.title = "About"
    ws_about.append(["Field", "Value"])
    for key, value in meta.items():
        ws_about.append([key, value])

    headers = [
        "Queue",
        "This Week Repeat ANIs",
        "This Week Repeat Contacts",
        "Prior Week Repeat ANIs",
        "Prior Week Repeat Contacts",
        "WoW Repeat ANI Change",
    ]
    ws = wb.create_sheet("By Queue")
    ws.append(headers)
    for cell in ws[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    ws.append([
        "ORG TOTAL",
        summary.repeat_anis,
        summary.repeat_contacts,
        meta.get("_prior_repeat_anis", 0),
        meta.get("_prior_repeat_contacts", 0),
        summary.repeat_anis - int(meta.get("_prior_repeat_anis", 0)),
    ])
    for cell in ws[2]:
        cell.font = BOLD
        cell.fill = SUMMARY_FILL
    for row in sorted(queue_rows, key=lambda item: (-item.repeat_anis, item.queue_name.lower())):
        prior_anis = int(row.__dict__.get("prior_repeat_anis", 0))
        prior_contacts = int(row.__dict__.get("prior_repeat_contacts", 0))
        ws.append([
            row.queue_name,
            row.repeat_anis,
            row.repeat_contacts,
            prior_anis,
            prior_contacts,
            row.repeat_anis - prior_anis,
        ])

    ws_detail = wb.create_sheet("Repeat Detail")
    detail_headers = ["Queue", "ANI", "This Week Contacts", "Prior Week Contacts"]
    ws_detail.append(detail_headers)
    for cell in ws_detail[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    for queue_name, ani, _qid, cur, prev in detail_rows:
        ws_detail.append([queue_name, ani, cur, prev])

    for worksheet in (ws, ws_detail):
        for column_cells in worksheet.columns:
            width = max(len(str(cell.value or "")) for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 48)
        worksheet.freeze_panes = "A2"

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def write_report_meta(path: Path, payload: dict[str, Any]) -> Path:
    meta_path = path.parent / f"{path.stem}_meta.json"
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return meta_path


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Weekly repeat inbound caller report by queue")
    parser.add_argument("--output", default=None, help="Output .xlsx path")
    add_env_arg(parser)
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    cur_days, cur_start, cur_end, prev_days, prev_start, prev_end = reporting_weeks()
    names = queue_lookup(client)
    tool_log("info", f"This week: {cur_start} through {cur_end}")
    tool_log("info", f"Prior week: {prev_start} through {prev_end}")

    current_counts = scan_repeat_contacts(client, cur_days, names)
    prior_counts = scan_repeat_contacts(client, prev_days, names)
    current_stats = summarize_queue_stats(current_counts, names)
    prior_stats = summarize_queue_stats(prior_counts, names)

    queue_rows: list[QueueRepeatStats] = []
    for qid in set(current_stats) | set(prior_stats):
        cur = current_stats.get(qid) or QueueRepeatStats(qid, names.get(qid, qid))
        prev = prior_stats.get(qid)
        if prev:
            cur.__dict__["prior_repeat_anis"] = prev.repeat_anis
            cur.__dict__["prior_repeat_contacts"] = prev.repeat_contacts
        else:
            cur.__dict__["prior_repeat_anis"] = 0
            cur.__dict__["prior_repeat_contacts"] = 0
        if cur.repeat_anis or cur.__dict__.get("prior_repeat_anis", 0):
            queue_rows.append(cur)

    summary = QueueRepeatStats(
        queue_id="org",
        queue_name="ORG TOTAL",
        repeat_anis=sum(row.repeat_anis for row in queue_rows),
        repeat_contacts=sum(row.repeat_contacts for row in queue_rows),
    )
    prior_repeat_anis = sum(int(row.__dict__.get("prior_repeat_anis", 0)) for row in queue_rows)
    prior_repeat_contacts = sum(int(row.__dict__.get("prior_repeat_contacts", 0)) for row in queue_rows)
    detail_rows = build_detail_rows(current_counts, prior_counts, names)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else output_path(f"repeat_caller_weekly_{stamp}.xlsx")
    meta = {
        "Generated At (UTC)": datetime.now(timezone.utc).isoformat(),
        "This Week Start": cur_start,
        "This Week End": cur_end,
        "Prior Week Start": prev_start,
        "Prior Week End": prev_end,
        "Definition": f"Inbound voice ANI with {MIN_REPEAT_CONTACTS}+ contacts to the same queue within the week",
        "Day Boundary": f"UTC-{TZ_OFFSET_HOURS:02d} (US Eastern DST)",
        "Queues Listed": str(len(queue_rows)),
        "This Week Repeat ANIs": str(summary.repeat_anis),
        "Prior Week Repeat ANIs": str(prior_repeat_anis),
        "_prior_repeat_anis": prior_repeat_anis,
        "_prior_repeat_contacts": prior_repeat_contacts,
    }
    write_workbook(out, queue_rows, summary, detail_rows, meta)
    write_report_meta(
        out,
        {
            "report_path": str(out),
            "window_start": cur_start,
            "window_end": cur_end,
            "prior_week_start": prev_start,
            "prior_week_end": prev_end,
            "queues_listed": len(queue_rows),
            "repeat_anis": summary.repeat_anis,
            "prior_repeat_anis": prior_repeat_anis,
            "repeat_detail_rows": len(detail_rows),
        },
    )
    tool_log("ok", f"Wrote {out}")
    tool_log("info", f"This week repeat ANIs: {summary.repeat_anis} (prior {prior_repeat_anis})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
