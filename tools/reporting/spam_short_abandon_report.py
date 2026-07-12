#!/usr/bin/env python3
"""Org-wide inbound short-abandon and Spam Call wrap-up report (daily/weekly/monthly)."""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

SPAM_WRAPUP_NAME = "Spam Call"
ABANDON_MAX_SECONDS = 10
TZ_OFFSET_HOURS = 4  # US Eastern DST for day boundaries

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
TOTAL_FILL = PatternFill("solid", fgColor="FFF2CC")
BOLD = Font(bold=True)


@dataclass
class PeriodBucket:
    abandon_under_10s: int = 0
    spam_count: int = 0
    spam_handle_ms: int = 0
    spam_talk_ms: int = 0
    spam_acw_ms: int = 0

    @property
    def spam_handle_sec(self) -> float:
        return self.spam_handle_ms / 1000.0

    @property
    def spam_talk_sec(self) -> float:
        return self.spam_talk_ms / 1000.0

    @property
    def spam_acw_sec(self) -> float:
        return self.spam_acw_ms / 1000.0

    @property
    def spam_connected_sec(self) -> float:
        return self.spam_talk_sec + self.spam_acw_sec


@dataclass
class ReportData:
    daily: dict[str, PeriodBucket] = field(default_factory=lambda: defaultdict(PeriodBucket))
    weekly: dict[str, PeriodBucket] = field(default_factory=lambda: defaultdict(PeriodBucket))
    monthly: dict[str, PeriodBucket] = field(default_factory=lambda: defaultdict(PeriodBucket))
    total: PeriodBucket = field(default_factory=PeriodBucket)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def local_day_key(dt: datetime) -> str:
    local = dt - timedelta(hours=TZ_OFFSET_HOURS)
    return local.date().isoformat()


def week_key(day_key: str) -> str:
    d = date.fromisoformat(day_key)
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def month_key(day_key: str) -> str:
    return day_key[:7]


def iter_sessions(conv: dict[str, Any]):
    for participant in conv.get("participants") or []:
        purpose = (participant.get("purpose") or "").lower()
        for session in participant.get("sessions") or []:
            media_type = (session.get("mediaType") or "").lower()
            yield purpose, media_type, session


def conversation_has_voice(conv: dict[str, Any]) -> bool:
    return any(media == "voice" for _purpose, media, _session in iter_sessions(conv))


def get_direction(conv: dict[str, Any]) -> str | None:
    value = conv.get("originatingDirection") or conv.get("direction")
    return value.lower() if isinstance(value, str) else None


def is_answered_voice(conv: dict[str, Any]) -> bool:
    for purpose, media, session in iter_sessions(conv):
        if purpose != "agent" or media != "voice":
            continue
        for segment in session.get("segments") or []:
            if (segment.get("segmentType") or "").lower() == "interact":
                return True
    return False


def first_acd_queue_enter(conv: dict[str, Any]) -> datetime | None:
    queue_enter: datetime | None = None
    for purpose, media, session in iter_sessions(conv):
        if purpose != "acd" or media != "voice":
            continue
        for segment in session.get("segments") or []:
            start_dt = parse_dt(segment.get("segmentStart"))
            if start_dt and (queue_enter is None or start_dt < queue_enter):
                queue_enter = start_dt
    return queue_enter


def acd_voice_end(conv: dict[str, Any]) -> datetime | None:
    end: datetime | None = None
    for purpose, media, session in iter_sessions(conv):
        if purpose != "acd" or media != "voice":
            continue
        for segment in session.get("segments") or []:
            end_dt = parse_dt(segment.get("segmentEnd"))
            if end_dt and (end is None or end_dt > end):
                end = end_dt
    return end


def abandon_wait_seconds(conv: dict[str, Any]) -> float | None:
    if get_direction(conv) != "inbound" or not conversation_has_voice(conv):
        return None
    if is_answered_voice(conv):
        return None
    queue_enter = first_acd_queue_enter(conv)
    if not queue_enter:
        return None
    end = acd_voice_end(conv) or parse_dt(conv.get("conversationEnd"))
    if not end:
        return None
    wait = (end - queue_enter).total_seconds()
    return wait if wait >= 0 else None


def month_window(months: int) -> tuple[str, list[str], list[date]]:
    today = date.today()
    first_of_current = today.replace(day=1)
    labels: list[str] = []
    year = first_of_current.year
    month = first_of_current.month
    for _ in range(months):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        labels.append(f"{year:04d}-{month:02d}")
    labels.reverse()

    start_y, start_m = map(int, labels[0].split("-"))
    end_y, end_m = map(int, labels[-1].split("-"))
    if end_m == 12:
        end_y += 1
        end_m = 1
    else:
        end_m += 1

    def iso(y: int, m: int) -> str:
        return f"{y:04d}-{m:02d}-01T{TZ_OFFSET_HOURS:02d}:00:00.000Z"

    interval = f"{iso(start_y, start_m)}/{iso(end_y, end_m)}"

    days: list[date] = []
    cursor = date(start_y, start_m, 1)
    end_day = date(end_y, end_m, 1)
    while cursor < end_day:
        days.append(cursor)
        cursor += timedelta(days=1)
    return interval, labels, days


def day_interval(day: date) -> str:
    start = f"{day.isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    next_day = day + timedelta(days=1)
    end = f"{next_day.isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    return f"{start}/{end}"


def add_to_periods(report: ReportData, day: str, *, abandon: int = 0, spam: int = 0,
                   handle_ms: int = 0, talk_ms: int = 0, acw_ms: int = 0) -> None:
    for bucket in (
        report.daily[day],
        report.weekly[week_key(day)],
        report.monthly[month_key(day)],
        report.total,
    ):
        bucket.abandon_under_10s += abandon
        bucket.spam_count += spam
        bucket.spam_handle_ms += handle_ms
        bucket.spam_talk_ms += talk_ms
        bucket.spam_acw_ms += acw_ms


def find_spam_wrapup_id(client) -> str:
    for code in client.paged_get("/api/v2/routing/wrapupcodes"):
        if (code.get("name") or "").strip().lower() == SPAM_WRAPUP_NAME.lower():
            return code["id"]
    raise SystemExit(f'Wrap-up code "{SPAM_WRAPUP_NAME}" not found in org')


def fetch_spam_daily(client, interval: str, spam_id: str) -> dict[str, dict[str, int]]:
    body = {
        "interval": interval,
        "granularity": "P1D",
        "filter": {
            "type": "and",
            "predicates": [
                {"type": "dimension", "dimension": "direction", "operator": "matches", "value": "inbound"},
                {"type": "dimension", "dimension": "wrapUpCode", "operator": "matches", "value": spam_id},
            ],
        },
        "groupBy": ["wrapUpCode"],
        "metrics": ["tAcw", "tHandle", "tTalk"],
    }
    tool_log("info", "Querying Spam Call aggregates")
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)
    daily: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "handle": 0, "talk": 0, "acw": 0})
    for result in resp.get("results") or []:
        for bucket in result.get("data") or []:
            day = (bucket.get("interval") or "").split("/")[0][:10]
            if not day:
                continue
            for metric in bucket.get("metrics") or []:
                stats = metric.get("stats") or {}
                name = metric.get("metric")
                if name == "tAcw":
                    daily[day]["count"] += int(stats.get("count") or 0)
                    daily[day]["acw"] += int(stats.get("sum") or 0)
                elif name == "tHandle":
                    daily[day]["handle"] += int(stats.get("sum") or 0)
                elif name == "tTalk":
                    daily[day]["talk"] += int(stats.get("sum") or 0)
    return daily


def fetch_abandon_daily(client, days: list[date]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    body_base = {
        "order": "asc",
        "orderBy": "conversationStart",
        "segmentFilters": [
            {
                "type": "and",
                "predicates": [
                    {"type": "dimension", "dimension": "direction", "operator": "matches", "value": "inbound"},
                    {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "voice"},
                    {"type": "dimension", "dimension": "purpose", "operator": "matches", "value": "acd"},
                    {"type": "dimension", "dimension": "disconnectType", "operator": "matches", "value": "peer"},
                ],
            }
        ],
    }

    for index, day in enumerate(days, 1):
        interval = day_interval(day)
        page = 1
        day_count = 0
        while True:
            body = {
                **body_base,
                "interval": interval,
                "paging": {"pageSize": 100, "pageNumber": page},
            }
            resp = client.request("POST", "/api/v2/analytics/conversations/details/query", json_body=body)
            convs = resp.get("conversations") or []
            if not convs:
                break
            for conv in convs:
                wait = abandon_wait_seconds(conv)
                if wait is None or wait >= ABANDON_MAX_SECONDS:
                    continue
                start = parse_dt(conv.get("conversationStart"))
                if not start:
                    continue
                day_key = local_day_key(start)
                if day_key == day.isoformat():
                    day_count += 1
            if len(convs) < 100:
                break
            page += 1
        counts[day.isoformat()] += day_count
        if index % 5 == 0 or index == len(days):
            tool_log("info", f"Abandon scan {index}/{len(days)} days — latest {day.isoformat()}: {day_count}")
    return counts


def merge_report(interval: str, month_labels: list[str], days: list[date],
                 spam_daily: dict[str, dict[str, int]], abandon_daily: dict[str, int]) -> ReportData:
    report = ReportData()
    for day in days:
        day_key = day.isoformat()
        if month_key(day_key) not in month_labels:
            continue
        spam = spam_daily.get(day_key, {})
        add_to_periods(
            report,
            day_key,
            abandon=abandon_daily.get(day_key, 0),
            spam=spam.get("count", 0),
            handle_ms=spam.get("handle", 0),
            talk_ms=spam.get("talk", 0),
            acw_ms=spam.get("acw", 0),
        )
    return report


def fmt_seconds(seconds: float) -> str:
    if seconds <= 0:
        return "0:00:00"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def bucket_rows(title: str, buckets: dict[str, PeriodBucket], *, sort_desc: bool = False) -> list[list[Any]]:
    rows: list[list[Any]] = []
    keys = sorted(buckets.keys(), reverse=sort_desc)
    for key in keys:
        b = buckets[key]
        rows.append([
            title, key,
            b.abandon_under_10s,
            b.spam_count,
            fmt_seconds(b.spam_handle_sec),
            fmt_seconds(b.spam_talk_sec),
            fmt_seconds(b.spam_acw_sec),
            fmt_seconds(b.spam_connected_sec),
            round(b.spam_handle_sec / 3600, 2) if b.spam_count else 0,
        ])
    return rows


def write_workbook(path: Path, report: ReportData, meta: dict[str, str]) -> None:
    wb = Workbook()
    ws_about = wb.active
    ws_about.title = "About"
    ws_about.append(["Field", "Value"])
    for k, v in meta.items():
        ws_about.append([k, v])

    headers = [
        "Grain", "Period",
        "Inbound Abandon <10s (count)",
        "Spam Call (count)",
        "Spam Handle Time (HH:MM:SS)",
        "Spam Talk Time (HH:MM:SS)",
        "Spam ACW Time (HH:MM:SS)",
        "Spam Talk+ACW (HH:MM:SS)",
        "Spam Handle Hours",
    ]

    for sheet_name, grain, buckets in (
        ("Daily", "Day", report.daily),
        ("Weekly", "Week", report.weekly),
        ("Monthly", "Month", report.monthly),
    ):
        ws = wb.create_sheet(sheet_name)
        ws.append(headers)
        for cell in ws[1]:
            cell.font = BOLD
            cell.fill = HEADER_FILL
        for row in bucket_rows(grain, buckets):
            ws.append(row)
        total = report.total if sheet_name == "Daily" else None
        if sheet_name == "Monthly":
            ws.append([
                "TOTAL", "ALL PERIODS",
                report.total.abandon_under_10s,
                report.total.spam_count,
                fmt_seconds(report.total.spam_handle_sec),
                fmt_seconds(report.total.spam_talk_sec),
                fmt_seconds(report.total.spam_acw_sec),
                fmt_seconds(report.total.spam_connected_sec),
                round(report.total.spam_handle_sec / 3600, 2) if report.total.spam_count else 0,
            ])
            for cell in ws[ws.max_row]:
                cell.font = BOLD
                cell.fill = TOTAL_FILL

    ws_all = wb.create_sheet("All Grains")
    ws_all.append(headers)
    for cell in ws_all[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    for row in bucket_rows("Day", report.daily):
        ws_all.append(row)
    ws_all.append([])
    for row in bucket_rows("Week", report.weekly):
        ws_all.append(row)
    ws_all.append([])
    for row in bucket_rows("Month", report.monthly):
        ws_all.append(row)
    ws_all.append([
        "TOTAL", "ALL PERIODS",
        report.total.abandon_under_10s,
        report.total.spam_count,
        fmt_seconds(report.total.spam_handle_sec),
        fmt_seconds(report.total.spam_talk_sec),
        fmt_seconds(report.total.spam_acw_sec),
        fmt_seconds(report.total.spam_connected_sec),
        round(report.total.spam_handle_sec / 3600, 2) if report.total.spam_count else 0,
    ])

    for ws in wb.worksheets:
        for column_cells in ws.columns:
            width = max(len(str(cell.value or "")) for cell in column_cells)
            ws.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 42)
        if ws.max_row > 1:
            ws.freeze_panes = "A2"

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Inbound abandon <10s and Spam Call org-wide report")
    parser.add_argument("--months", type=int, default=3, help="Complete calendar months to include (default: 3)")
    parser.add_argument("--output", default=None, help="Output .xlsx path")
    parser.add_argument("--skip-abandon-scan", action="store_true", help="Spam only (debug)")
    add_env_arg(parser)
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    interval, month_labels, days = month_window(args.months)
    tool_log("info", f"Reporting months: {', '.join(month_labels)}")

    spam_id = find_spam_wrapup_id(client)
    spam_daily = fetch_spam_daily(client, interval, spam_id)

    if args.skip_abandon_scan:
        abandon_daily = {}
        tool_log("warn", "Skipping abandon detail scan")
    else:
        abandon_daily = fetch_abandon_daily(client, days)

    report = merge_report(interval, month_labels, days, spam_daily, abandon_daily)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else output_path(f"spam_short_abandon_report_{stamp}.xlsx")
    meta = {
        "Generated At (UTC)": datetime.now(timezone.utc).isoformat(),
        "Months": ", ".join(month_labels),
        "Scope": "Org-wide inbound voice",
        "Abandon Definition": f"Unanswered inbound; queue wait < {ABANDON_MAX_SECONDS}s",
        "Spam Wrap-Up Code": f"{SPAM_WRAPUP_NAME} ({spam_id})",
        "Spam Connected Time": "Talk + ACW sums from analytics (tTalk, tAcw); Handle from tHandle",
        "Day Boundary": f"UTC-{TZ_OFFSET_HOURS:02d} (US Eastern DST)",
        "Analytics Interval": interval,
        "Total Abandon <10s": str(report.total.abandon_under_10s),
        "Total Spam Calls": str(report.total.spam_count),
        "Total Spam Handle Hours": f"{report.total.spam_handle_sec / 3600:.2f}",
    }
    write_workbook(out, report, meta)
    tool_log("ok", f"Wrote {out}")
    tool_log(
        "info",
        f"Totals — abandon<{ABANDON_MAX_SECONDS}s: {report.total.abandon_under_10s} | "
        f"spam: {report.total.spam_count} | spam handle: {fmt_seconds(report.total.spam_handle_sec)}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
