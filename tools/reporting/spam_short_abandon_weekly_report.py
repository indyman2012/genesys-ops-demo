#!/usr/bin/env python3
"""Weekly inbound short-abandon and Spam Call report by queue (prior Mon-Sun)."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log
from core.lookups import get_all_queues
from tools.reporting.direct_outbound_agents_report import previous_calendar_week
from tools.reporting.spam_short_abandon_report import (
    ABANDON_MAX_SECONDS,
    SPAM_WRAPUP_NAME,
    TZ_OFFSET_HOURS,
    abandon_wait_seconds,
    day_interval,
    find_spam_wrapup_id,
    fmt_seconds,
    local_day_key,
    parse_dt,
)

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
SUMMARY_FILL = PatternFill("solid", fgColor="FFF2CC")
BOLD = Font(bold=True)
UNKNOWN_QUEUE_ID = "unknown_queue"
UNKNOWN_QUEUE_NAME = "Unassigned / Unknown Queue"
MIN_ANI_APPEARANCES = 3


@dataclass
class QueueBucket:
    queue_id: str
    queue_name: str
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
class AniBucket:
    ani: str
    abandon_count: int = 0
    spam_count: int = 0

    @property
    def total(self) -> int:
        return self.abandon_count + self.spam_count


def normalize_ani(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith("tel:"):
        raw = raw[4:]
    if lowered.startswith("sip:") or "@" in raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) > 10:
        return f"+{digits}"
    return ""


def extract_inbound_ani(conv: dict[str, Any]) -> str:
    candidates: list[Any] = []
    for participant in conv.get("participants") or []:
        purpose = (participant.get("purpose") or "").lower()
        if purpose in {"external", "customer", "ivr", "user"}:
            candidates.extend([participant.get("ani"), participant.get("address")])
        for session in participant.get("sessions") or []:
            if (session.get("mediaType") or "").lower() != "voice":
                continue
            direction = (session.get("direction") or participant.get("direction") or "").lower()
            if direction and direction != "inbound":
                continue
            candidates.extend([session.get("ani"), session.get("address")])
    for value in candidates:
        ani = normalize_ani(value)
        if ani:
            return ani
    return ""


def record_ani(ani_stats: dict[str, AniBucket], ani: str, *, abandon: int = 0, spam: int = 0) -> None:
    if not ani:
        return
    bucket = ani_stats.get(ani)
    if bucket is None:
        bucket = AniBucket(ani=ani)
        ani_stats[ani] = bucket
    bucket.abandon_count += abandon
    bucket.spam_count += spam


def week_api_interval(start: date, end: date) -> str:
    start_iso = f"{start.isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    end_iso = f"{(end + timedelta(days=1)).isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    return f"{start_iso}/{end_iso}"


def queue_lookup(client) -> dict[str, str]:
    return {(q.get("id") or ""): (q.get("name") or q.get("id") or "") for q in get_all_queues(client)}


def resolve_queue_id(conv: dict[str, Any]) -> str | None:
    for participant in conv.get("participants") or []:
        purpose = (participant.get("purpose") or "").lower()
        if purpose != "acd":
            continue
        candidates = [participant.get("queueId")]
        for session in participant.get("sessions") or []:
            candidates.append(session.get("queueId"))
            for segment in session.get("segments") or []:
                candidates.append(segment.get("queueId"))
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def fetch_spam_by_queue(client, interval: str, spam_id: str, names: dict[str, str]) -> dict[str, QueueBucket]:
    body = {
        "interval": interval,
        "groupBy": ["queueId"],
        "filter": {
            "type": "and",
            "predicates": [
                {"type": "dimension", "dimension": "direction", "operator": "matches", "value": "inbound"},
                {"type": "dimension", "dimension": "wrapUpCode", "operator": "matches", "value": spam_id},
            ],
        },
        "metrics": ["tAcw", "tHandle", "tTalk"],
    }
    tool_log("info", "Querying Spam Call aggregates by queue")
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)
    buckets: dict[str, QueueBucket] = {}

    def bucket_for(qid: str) -> QueueBucket:
        if qid not in buckets:
            buckets[qid] = QueueBucket(queue_id=qid, queue_name=names.get(qid, UNKNOWN_QUEUE_NAME if qid == UNKNOWN_QUEUE_ID else qid))
        return buckets[qid]

    for result in resp.get("results") or []:
        group = result.get("group") or {}
        qid = (group.get("queueId") or "").strip() or UNKNOWN_QUEUE_ID
        item = bucket_for(qid)
        for data in result.get("data") or []:
            for metric in data.get("metrics") or []:
                stats = metric.get("stats") or {}
                name = metric.get("metric")
                if name == "tAcw":
                    item.spam_count += int(stats.get("count") or 0)
                    item.spam_acw_ms += int(stats.get("sum") or 0)
                elif name == "tHandle":
                    item.spam_handle_ms += int(stats.get("sum") or 0)
                elif name == "tTalk":
                    item.spam_talk_ms += int(stats.get("sum") or 0)
    return buckets


def fetch_abandon_by_queue(
    client,
    days: list[date],
    names: dict[str, str],
    ani_stats: dict[str, AniBucket],
) -> dict[str, QueueBucket]:
    buckets: dict[str, QueueBucket] = {}
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

    def bucket_for(qid: str) -> QueueBucket:
        if qid not in buckets:
            buckets[qid] = QueueBucket(queue_id=qid, queue_name=names.get(qid, UNKNOWN_QUEUE_NAME if qid == UNKNOWN_QUEUE_ID else qid))
        return buckets[qid]

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
                wait = abandon_wait_seconds(conv)
                if wait is None or wait >= ABANDON_MAX_SECONDS:
                    continue
                start = parse_dt(conv.get("conversationStart"))
                if not start or local_day_key(start) != day.isoformat():
                    continue
                qid = resolve_queue_id(conv) or UNKNOWN_QUEUE_ID
                bucket_for(qid).abandon_under_10s += 1
                record_ani(ani_stats, extract_inbound_ani(conv), abandon=1)
                day_count += 1
            if len(convs) < 100:
                break
            page += 1
        if index % 2 == 0 or index == len(days):
            tool_log("info", f"Abandon scan {index}/{len(days)} days — {day.isoformat()}: {day_count}")
    return buckets


def fetch_spam_anis(client, days: list[date], spam_id: str, ani_stats: dict[str, AniBucket]) -> int:
    body_base = {
        "order": "asc",
        "orderBy": "conversationStart",
        "segmentFilters": [
            {
                "type": "and",
                "predicates": [
                    {"type": "dimension", "dimension": "direction", "operator": "matches", "value": "inbound"},
                    {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "voice"},
                    {"type": "dimension", "dimension": "wrapUpCode", "operator": "matches", "value": spam_id},
                ],
            }
        ],
    }
    total = 0
    tool_log("info", "Scanning Spam Call conversations for repeat ANIs")
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
                record_ani(ani_stats, ani, spam=1)
                day_count += 1
                total += 1
            if len(convs) < 100:
                break
            page += 1
        if index % 2 == 0 or index == len(days):
            tool_log("info", f"Spam ANI scan {index}/{len(days)} days — {day.isoformat()}: {day_count}")
    return total


def repeat_anis(ani_stats: dict[str, AniBucket], *, minimum: int = MIN_ANI_APPEARANCES) -> list[AniBucket]:
    rows = [bucket for bucket in ani_stats.values() if bucket.total >= minimum]
    return sorted(rows, key=lambda item: (-item.total, -item.spam_count, -item.abandon_count, item.ani))


def merge_queue_buckets(*maps: dict[str, QueueBucket]) -> dict[str, QueueBucket]:
    merged: dict[str, QueueBucket] = {}
    for source in maps:
        for qid, bucket in source.items():
            if qid not in merged:
                merged[qid] = QueueBucket(queue_id=bucket.queue_id, queue_name=bucket.queue_name)
            target = merged[qid]
            target.abandon_under_10s += bucket.abandon_under_10s
            target.spam_count += bucket.spam_count
            target.spam_handle_ms += bucket.spam_handle_ms
            target.spam_talk_ms += bucket.spam_talk_ms
            target.spam_acw_ms += bucket.spam_acw_ms
    return merged


def summarize(buckets: dict[str, QueueBucket]) -> QueueBucket:
    total = QueueBucket(queue_id="SUMMARY", queue_name="ORG TOTAL")
    for bucket in buckets.values():
        total.abandon_under_10s += bucket.abandon_under_10s
        total.spam_count += bucket.spam_count
        total.spam_handle_ms += bucket.spam_handle_ms
        total.spam_talk_ms += bucket.spam_talk_ms
        total.spam_acw_ms += bucket.spam_acw_ms
    return total


def queue_row(bucket: QueueBucket) -> list[Any]:
    return [
        bucket.queue_name,
        bucket.abandon_under_10s,
        bucket.spam_count,
        fmt_seconds(bucket.spam_handle_sec),
        fmt_seconds(bucket.spam_talk_sec),
        fmt_seconds(bucket.spam_acw_sec),
        fmt_seconds(bucket.spam_connected_sec),
        round(bucket.spam_handle_sec / 3600, 2) if bucket.spam_count else 0,
    ]


def write_workbook(
    path: Path,
    rows: list[QueueBucket],
    summary: QueueBucket,
    repeat_ani_rows: list[AniBucket],
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
        "Inbound Abandon <10s (count)",
        "Spam Call (count)",
        "Spam Handle Time (HH:MM:SS)",
        "Spam Talk Time (HH:MM:SS)",
        "Spam ACW Time (HH:MM:SS)",
        "Spam Talk+ACW (HH:MM:SS)",
        "Spam Handle Hours",
    ]
    ws = wb.create_sheet("By Queue")
    ws.append(headers)
    for cell in ws[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    ws.append(queue_row(summary))
    for cell in ws[2]:
        cell.font = BOLD
        cell.fill = SUMMARY_FILL
    active_rows = [b for b in rows if b.abandon_under_10s or b.spam_count]
    for bucket in sorted(active_rows, key=lambda b: b.queue_name.lower()):
        ws.append(queue_row(bucket))

    ani_headers = [
        "ANI",
        "Total Appearances",
        "Abandon <10s (count)",
        "Spam Call (count)",
        "Block Candidate",
    ]
    ws_ani = wb.create_sheet("Repeat ANIs")
    ws_ani.append(ani_headers)
    for cell in ws_ani[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    for bucket in repeat_ani_rows:
        ws_ani.append([
            bucket.ani,
            bucket.total,
            bucket.abandon_count,
            bucket.spam_count,
            "Yes",
        ])

    for worksheet in (ws, ws_ani):
        for column_cells in worksheet.columns:
            width = max(len(str(cell.value or "")) for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 48)
        worksheet.freeze_panes = "A2"

    for column_cells in ws_about.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells)
        ws_about.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 48)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def write_report_meta(path: Path, payload: dict[str, Any]) -> Path:
    meta_path = path.parent / f"{path.stem}_meta.json"
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return meta_path


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Weekly spam/abandon report by queue (prior Mon-Sun)")
    parser.add_argument("--output", default=None, help="Output .xlsx path")
    parser.add_argument("--skip-abandon-scan", action="store_true", help="Spam only (debug)")
    add_env_arg(parser)
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    days, start_label, end_label = previous_calendar_week()
    names = queue_lookup(client)
    names[UNKNOWN_QUEUE_ID] = UNKNOWN_QUEUE_NAME
    interval = week_api_interval(date.fromisoformat(start_label), date.fromisoformat(end_label))
    tool_log("info", f"Window: {start_label} through {end_label}")

    spam_id = find_spam_wrapup_id(client)
    spam_buckets = fetch_spam_by_queue(client, interval, spam_id, names)
    ani_stats: dict[str, AniBucket] = {}
    if args.skip_abandon_scan:
        abandon_buckets = {}
        tool_log("warn", "Skipping abandon detail scan")
    else:
        abandon_buckets = fetch_abandon_by_queue(client, days, names, ani_stats)
    fetch_spam_anis(client, days, spam_id, ani_stats)
    repeat_ani_rows = repeat_anis(ani_stats)

    merged = merge_queue_buckets(spam_buckets, abandon_buckets)
    summary = summarize(merged)
    queue_rows = list(merged.values())

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else output_path(f"spam_short_abandon_weekly_{stamp}.xlsx")
    meta = {
        "Generated At (UTC)": datetime.now(timezone.utc).isoformat(),
        "Window Start": start_label,
        "Window End": end_label,
        "Period": "previous-week",
        "Scope": "Inbound voice by queue",
        "Abandon Definition": f"Unanswered inbound; queue wait < {ABANDON_MAX_SECONDS}s",
        "Spam Wrap-Up Code": f"{SPAM_WRAPUP_NAME} ({spam_id})",
        "Day Boundary": f"UTC-{TZ_OFFSET_HOURS:02d} (US Eastern DST)",
        "Analytics Interval": interval,
        "Queues With Activity": str(len([b for b in queue_rows if b.abandon_under_10s or b.spam_count])),
        "Total Abandon <10s": str(summary.abandon_under_10s),
        "Total Spam Calls": str(summary.spam_count),
        "Total Spam Handle Hours": f"{summary.spam_handle_sec / 3600:.2f}",
        "Repeat ANI Minimum": str(MIN_ANI_APPEARANCES),
        "Repeat ANIs Listed": str(len(repeat_ani_rows)),
    }
    write_workbook(out, queue_rows, summary, repeat_ani_rows, meta)
    write_report_meta(
        out,
        {
            "report_path": str(out),
            "window_start": start_label,
            "window_end": end_label,
            "queues_with_activity": len([b for b in queue_rows if b.abandon_under_10s or b.spam_count]),
            "total_abandon": summary.abandon_under_10s,
            "total_spam": summary.spam_count,
            "total_spam_handle_hours": round(summary.spam_handle_sec / 3600, 2),
            "repeat_anis_listed": len(repeat_ani_rows),
        },
    )
    tool_log("ok", f"Wrote {out}")
    tool_log(
        "info",
        f"Summary — abandon<{ABANDON_MAX_SECONDS}s: {summary.abandon_under_10s} | "
        f"spam: {summary.spam_count} | spam handle: {fmt_seconds(summary.spam_handle_sec)} | "
        f"repeat ANIs (>={MIN_ANI_APPEARANCES}): {len(repeat_ani_rows)}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
