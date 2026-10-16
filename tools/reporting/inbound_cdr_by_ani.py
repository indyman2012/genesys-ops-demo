#!/usr/bin/env python3
"""Pull call detail records for inbound (or any-direction) voice by caller ANI."""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log, write_csv
from core.lookups import get_all_queues
from tools.reporting.direct_outbound_agents_report import day_interval
from tools.reporting.spam_short_abandon_report import TZ_OFFSET_HOURS, parse_dt
from tools.reporting.spam_short_abandon_weekly_report import (
    extract_inbound_ani,
    normalize_ani,
    resolve_queue_id,
)

TARGET_DIGITS = "9547909709"
DEFAULT_DAYS = 2
# Day boundaries match other GenesysCloudToolkit reports (US Eastern DST, UTC-4).
DAY_BOUNDARY_LABEL = f"UTC-{TZ_OFFSET_HOURS:02d} (US Eastern DST)"

CSV_FIELDS = [
    "conversationId",
    "conversationStartUtc",
    "conversationEndUtc",
    "conversationStartLocal",
    "conversationEndLocal",
    "direction",
    "searchMode",
    "ani",
    "dnis",
    "queueId",
    "queueName",
    "agentUserId",
    "agentName",
    "agentEmail",
    "durationSec",
    "talkSec",
    "acwSec",
    "holdSec",
    "disconnectType",
    "wrapUpCode",
    "wrapUpName",
    "outcome",
    "recordingIds",
]


def phone_variants(raw: str) -> list[str]:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise SystemExit(f"Expected 10-digit US number, got: {raw!r}")
    a, b, c = digits[:3], digits[3:6], digits[6:]
    return [
        f"+1{digits}",
        f"1{digits}",
        digits,
        f"({a}) {b}-{c}",
        f"{a}-{b}-{c}",
        f"+1{a}{b}{c}",
        f"tel:+1{digits}",
    ]


def target_normalized(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise SystemExit(f"Expected 10-digit US number, got: {raw!r}")
    return f"+1{digits}"


def ani_matches(conv: dict[str, Any], normalized_target: str, *, inbound_only: bool) -> bool:
    found = extract_inbound_ani(conv) if inbound_only else extract_any_ani(conv)
    if found and found == normalized_target:
        return True
    if inbound_only:
        return False
    return extract_outbound_dialed(conv) == normalized_target


def extract_any_ani(conv: dict[str, Any]) -> str:
    candidates: list[Any] = []
    for participant in conv.get("participants") or []:
        candidates.extend([participant.get("ani"), participant.get("address")])
        for session in participant.get("sessions") or []:
            if (session.get("mediaType") or "").lower() != "voice":
                continue
            candidates.extend([session.get("ani"), session.get("address")])
    for value in candidates:
        ani = normalize_ani(value)
        if ani:
            return ani
    return ""


def extract_outbound_dialed(conv: dict[str, Any]) -> str:
    candidates: list[Any] = []
    for participant in conv.get("participants") or []:
        for session in participant.get("sessions") or []:
            if (session.get("mediaType") or "").lower() != "voice":
                continue
            direction = (session.get("direction") or participant.get("direction") or "").lower()
            if direction != "outbound":
                continue
            candidates.extend([session.get("dnis"), session.get("address"), participant.get("address")])
    for value in candidates:
        ani = normalize_ani(value)
        if ani:
            return ani
    return ""


def extract_dnis(conv: dict[str, Any]) -> str:
    for participant in conv.get("participants") or []:
        for session in participant.get("sessions") or []:
            if (session.get("mediaType") or "").lower() != "voice":
                continue
            dnis = normalize_ani(session.get("dnis") or session.get("address"))
            if dnis:
                return dnis
    return ""


def extract_agent(conv: dict[str, Any]) -> tuple[str, str, str]:
    for participant in conv.get("participants") or []:
        purpose = (participant.get("purpose") or "").lower()
        if purpose not in {"agent", "user"}:
            continue
        user = participant.get("user") or {}
        user_id = (participant.get("userId") or user.get("id") or "").strip()
        name = (participant.get("name") or user.get("name") or "").strip()
        email = (user.get("email") or participant.get("address") or "").strip()
        for session in participant.get("sessions") or []:
            if (session.get("mediaType") or "").lower() != "voice":
                continue
            for segment in session.get("segments") or []:
                if (segment.get("segmentType") or "").lower() == "interact":
                    return user_id, name, email
        if user_id:
            return user_id, name, email
    return "", "", ""


def ms_to_sec(value: Any) -> float:
    try:
        return round(int(value or 0) / 1000.0, 1)
    except (TypeError, ValueError):
        return 0.0


def extract_metrics(conv: dict[str, Any]) -> tuple[float, float, float, float]:
    talk_ms = 0
    acw_ms = 0
    hold_ms = 0
    for participant in conv.get("participants") or []:
        purpose = (participant.get("purpose") or "").lower()
        if purpose not in {"agent", "user", "acd"}:
            continue
        for session in participant.get("sessions") or []:
            if (session.get("mediaType") or "").lower() != "voice":
                continue
            metrics = session.get("metrics") or []
            if metrics:
                for metric in metrics:
                    name = (metric.get("name") or "").lower()
                    value = int(metric.get("value") or 0)
                    if name == "ttalk":
                        talk_ms += value
                    elif name == "tacw":
                        acw_ms += value
                    elif name == "theld":
                        hold_ms += value
            for segment in session.get("segments") or []:
                talk_ms += int(segment.get("talkTimeMs") or 0)
                acw_ms += int(segment.get("wrapUpTimeMs") or 0)
                hold_ms += int(segment.get("heldTimeMs") or 0)
    start = parse_dt(conv.get("conversationStart"))
    end = parse_dt(conv.get("conversationEnd"))
    duration_sec = 0.0
    if start and end and end >= start:
        duration_sec = round((end - start).total_seconds(), 1)
    return duration_sec, ms_to_sec(talk_ms), ms_to_sec(acw_ms), ms_to_sec(hold_ms)


def extract_disconnect(conv: dict[str, Any]) -> str:
    values: list[str] = []
    for participant in conv.get("participants") or []:
        if participant.get("disconnectType"):
            values.append(str(participant.get("disconnectType")))
        for session in participant.get("sessions") or []:
            if session.get("disconnectType"):
                values.append(str(session.get("disconnectType")))
            for segment in session.get("segments") or []:
                if segment.get("disconnectType"):
                    values.append(str(segment.get("disconnectType")))
    return "; ".join(dict.fromkeys(v for v in values if v))


def extract_wrapup(conv: dict[str, Any]) -> tuple[str, str]:
    for participant in conv.get("participants") or []:
        purpose = (participant.get("purpose") or "").lower()
        if purpose not in {"agent", "user"}:
            continue
        wrap = participant.get("wrapup") or {}
        code = (wrap.get("code") or wrap.get("wrapUpCode") or "").strip()
        name = (wrap.get("name") or wrap.get("wrapUpNote") or "").strip()
        if code or name:
            return code, name
        for session in participant.get("sessions") or []:
            for segment in session.get("segments") or []:
                code = (segment.get("wrapUpCode") or "").strip()
                if code:
                    return code, name
    return "", ""


def extract_recordings(conv: dict[str, Any]) -> str:
    ids: list[str] = []
    for participant in conv.get("participants") or []:
        for session in participant.get("sessions") or []:
            rec = session.get("recording") or {}
            rec_id = (rec.get("id") or rec.get("recordingId") or "").strip()
            if rec_id:
                ids.append(rec_id)
            for segment in session.get("segments") or []:
                rec = segment.get("recording") or {}
                rec_id = (rec.get("id") or rec.get("recordingId") or "").strip()
                if rec_id:
                    ids.append(rec_id)
    return "; ".join(dict.fromkeys(ids))


def fmt_local(dt: datetime | None) -> str:
    if not dt:
        return ""
    local = dt - timedelta(hours=TZ_OFFSET_HOURS)
    return local.strftime("%Y-%m-%d %H:%M:%S")


def fmt_utc(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def conversation_to_row(
    conv: dict[str, Any],
    *,
    queue_names: dict[str, str],
    search_mode: str,
    normalized_target: str,
    inbound_only: bool,
) -> dict[str, str] | None:
    if not ani_matches(conv, normalized_target, inbound_only=inbound_only):
        return None
    cid = (conv.get("conversationId") or conv.get("id") or "").strip()
    start = parse_dt(conv.get("conversationStart"))
    end = parse_dt(conv.get("conversationEnd"))
    direction = (conv.get("originatingDirection") or conv.get("direction") or "").lower()
    qid = resolve_queue_id(conv) or ""
    agent_id, agent_name, agent_email = extract_agent(conv)
    duration_sec, talk_sec, acw_sec, hold_sec = extract_metrics(conv)
    wrap_code, wrap_name = extract_wrapup(conv)
    disconnect = extract_disconnect(conv)
    answered = talk_sec > 0 or bool(agent_id and agent_name)
    outcome = "answered" if answered else ("abandoned" if disconnect else "unknown")
    return {
        "conversationId": cid,
        "conversationStartUtc": fmt_utc(start),
        "conversationEndUtc": fmt_utc(end),
        "conversationStartLocal": fmt_local(start),
        "conversationEndLocal": fmt_local(end),
        "direction": direction,
        "searchMode": search_mode,
        "ani": extract_inbound_ani(conv) if inbound_only else (extract_any_ani(conv) or extract_outbound_dialed(conv)),
        "dnis": extract_dnis(conv),
        "queueId": qid,
        "queueName": queue_names.get(qid, qid),
        "agentUserId": agent_id,
        "agentName": agent_name,
        "agentEmail": agent_email,
        "durationSec": str(duration_sec),
        "talkSec": str(talk_sec),
        "acwSec": str(acw_sec),
        "holdSec": str(hold_sec),
        "disconnectType": disconnect,
        "wrapUpCode": wrap_code,
        "wrapUpName": wrap_name,
        "outcome": outcome,
        "recordingIds": extract_recordings(conv),
    }


def query_with_ani_filter(
    client,
    interval: str,
    ani_value: str,
    *,
    direction: str | None,
) -> list[dict[str, Any]]:
    predicates: list[dict[str, Any]] = [
        {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "voice"},
        {"type": "dimension", "dimension": "ani", "operator": "matches", "value": ani_value},
    ]
    if direction:
        predicates.append(
            {"type": "dimension", "dimension": "direction", "operator": "matches", "value": direction}
        )
    body_base = {
        "order": "asc",
        "orderBy": "conversationStart",
        "interval": interval,
        "segmentFilters": [{"type": "and", "predicates": predicates}],
    }
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        body = {**body_base, "paging": {"pageSize": 100, "pageNumber": page}}
        resp = client.request("POST", "/api/v2/analytics/conversations/details/query", json_body=body)
        convs = resp.get("conversations") or []
        if not convs:
            break
        rows.extend(convs)
        if len(convs) < 100:
            break
        page += 1
    return rows


def scan_days(
    client,
    days: list[date],
    *,
    direction: str,
    normalized_target: str,
    inbound_only: bool,
    queue_names: dict[str, str],
    search_mode: str,
) -> list[dict[str, str]]:
    body_base = {
        "order": "asc",
        "orderBy": "conversationStart",
        "segmentFilters": [
            {
                "type": "and",
                "predicates": [
                    {"type": "dimension", "dimension": "direction", "operator": "matches", "value": direction},
                    {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "voice"},
                ],
            }
        ],
    }
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for index, day in enumerate(days, 1):
        interval = day_interval(day)
        page = 1
        day_hits = 0
        while True:
            body = {**body_base, "interval": interval, "paging": {"pageSize": 100, "pageNumber": page}}
            resp = client.request("POST", "/api/v2/analytics/conversations/details/query", json_body=body)
            convs = resp.get("conversations") or []
            if not convs:
                break
            for conv in convs:
                row = conversation_to_row(
                    conv,
                    queue_names=queue_names,
                    search_mode=search_mode,
                    normalized_target=normalized_target,
                    inbound_only=inbound_only,
                )
                if row and row["conversationId"] not in seen:
                    seen.add(row["conversationId"])
                    rows.append(row)
                    day_hits += 1
            if len(convs) < 100:
                break
            page += 1
        if index % 15 == 0 or index == len(days):
            tool_log("info", f"Scan {index}/{len(days)} days — {day.isoformat()}: {day_hits} matches (total {len(rows)})")
    rows.sort(key=lambda item: item.get("conversationStartUtc") or "")
    return rows


def rolling_days(days: int, *, end: date | None = None) -> tuple[list[date], str, str]:
    end_day = end or date.today()
    start = end_day - timedelta(days=days - 1)
    day_list = [start + timedelta(days=i) for i in range(days)]
    return day_list, start.isoformat(), end_day.isoformat()


def resolve_window(args: argparse.Namespace) -> tuple[list[date], str, str]:
    if args.start or args.end:
        if not args.start or not args.end:
            raise SystemExit("Provide both --start and --end (YYYY-MM-DD), or use --days alone.")
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
        if end < start:
            raise SystemExit("--end must be on or after --start")
        day_list = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        return day_list, start.isoformat(), end.isoformat()
    return rolling_days(args.days)


def week_api_interval(start: date, end: date) -> str:
    start_iso = f"{start.isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    end_iso = f"{(end + timedelta(days=1)).isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    return f"{start_iso}/{end_iso}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export inbound CDR rows for a caller ANI.")
    parser.add_argument("--phone", default=TARGET_DIGITS, help="10-digit caller number (default: 9547909709)")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Rolling calendar-day window ending today (default: {DEFAULT_DAYS}; ignored if --start/--end set)",
    )
    parser.add_argument("--start", help="Window start date YYYY-MM-DD (requires --end)")
    parser.add_argument("--end", help="Window end date YYYY-MM-DD (requires --start)")
    parser.add_argument(
        "--inbound-only",
        action="store_true",
        default=False,
        help="Do not fall back to outbound/any-direction search when inbound is empty",
    )
    add_env_arg(parser)
    args = parser.parse_args()

    normalized = target_normalized(args.phone)
    variants = phone_variants(args.phone)
    days, start_label, end_label = resolve_window(args)
    interval = week_api_interval(days[0], days[-1])

    client = create_client(env_file=args.env_file)
    queue_names = {(q.get("id") or ""): (q.get("name") or "") for q in get_all_queues(client)}

    tool_log(
        "info",
        f"Searching inbound voice for ANI {normalized} — {start_label} to {end_label} "
        f"({len(days)} day(s), boundary {DAY_BOUNDARY_LABEL})",
    )

    rows: list[dict[str, str]] = []
    search_mode = "inbound-ani-filter"
    seen: set[str] = set()

    for variant in variants:
        try:
            convs = query_with_ani_filter(client, interval, variant, direction="inbound")
        except Exception as exc:
            tool_log("warn", f"ANI filter failed for {variant!r}: {exc}")
            continue
        tool_log("info", f"ANI filter {variant!r}: {len(convs)} conversations returned")
        for conv in convs:
            row = conversation_to_row(
                conv,
                queue_names=queue_names,
                search_mode=search_mode,
                normalized_target=normalized,
                inbound_only=True,
            )
            if row and row["conversationId"] not in seen:
                seen.add(row["conversationId"])
                rows.append(row)

    if not rows:
        tool_log("info", "No ANI-filter hits; falling back to day-by-day inbound scan")
        search_mode = "inbound-day-scan"
        rows = scan_days(
            client,
            days,
            direction="inbound",
            normalized_target=normalized,
            inbound_only=True,
            queue_names=queue_names,
            search_mode=search_mode,
        )

    inbound_count = len(rows)
    if not rows and not args.inbound_only:
        tool_log("info", "No inbound matches; trying outbound TO this number")
        search_mode = "outbound-day-scan"
        rows = scan_days(
            client,
            days,
            direction="outbound",
            normalized_target=normalized,
            inbound_only=False,
            queue_names=queue_names,
            search_mode=search_mode,
        )

    if not rows and not args.inbound_only:
        tool_log("info", "No outbound matches; trying any-direction voice day scan")
        search_mode = "any-direction-day-scan"
        seen.clear()
        merged: list[dict[str, str]] = []
        for direction in ("inbound", "outbound"):
            part = scan_days(
                client,
                days,
                direction=direction,
                normalized_target=normalized,
                inbound_only=False,
                queue_names=queue_names,
                search_mode=search_mode,
            )
            for row in part:
                if row["conversationId"] not in seen:
                    seen.add(row["conversationId"])
                    merged.append(row)
        rows = sorted(merged, key=lambda item: item.get("conversationStartUtc") or "")

    stamp = datetime.now().strftime("%Y%m%d")
    digits = re.sub(r"\D", "", args.phone)[-10:]
    out_path = output_path(f"cdr_inbound_{digits}_{stamp}.csv")
    write_csv(out_path, rows, CSV_FIELDS)

    print("=" * 72)
    print("Inbound CDR by ANI")
    print(f"Phone: {normalized}")
    print(f"Date range: {start_label} to {end_label} ({len(days)} calendar day(s))")
    print(f"Day boundary: {DAY_BOUNDARY_LABEL}")
    print(f"Search mode: {search_mode}")
    print(f"Inbound match count: {inbound_count}")
    if inbound_count != len(rows):
        print(f"Broader match count (outbound/any): {len(rows)}")
    print(f"Total rows in CSV: {len(rows)}")
    print(f"CSV: {out_path}")
    print("=" * 72)
    if rows:
        print("Sample rows:")
        for row in rows[:5]:
            print(
                f"  {row['conversationStartLocal']} | {row['direction']} | "
                f"{row['queueName']} | {row['agentName']} | {row['outcome']} | {row['conversationId']}"
            )
    else:
        print("No matching conversations found in the search window.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
