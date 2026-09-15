#!/usr/bin/env python3
"""Weekly queue callback conversion report — callbacks created and avg queue wait before convert."""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log
from core.lookups import get_all_queues
from tools.reporting.direct_outbound_agents_report import previous_calendar_week
from tools.reporting.spam_short_abandon_report import TZ_OFFSET_HOURS, fmt_seconds, parse_dt

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
SUMMARY_FILL = PatternFill("solid", fgColor="FFF2CC")
BOLD = Font(bold=True)
UNKNOWN_QUEUE_NAME = "(No Queue)"
MAX_HOLD_SEC = 7200
MAX_ANSWER_SEC = 86400
# PureCloud callback customer dials happen on callback media, not voice on the linked calls object.
MAX_CALLBACK_PICKUP_GAP_SEC = 3600
MAX_CALLBACK_CUSTOMER_LEG_SEC = 7200


@dataclass
class QueueCallbackStats:
    queue_id: str
    queue_name: str
    callbacks_created: int = 0
    wait_sum_ms: int = 0
    wait_count: int = 0
    avg_wait_sec: float = 0.0
    answer_sum_ms: int = 0
    answer_count: int = 0
    avg_answer_sec: float = 0.0
    no_outbound_count: int = 0
    pending_no_agent_count: int = 0


@dataclass
class MissedCallbackRow:
    conversation_id: str
    agent_name: str
    callback_at: datetime | None
    caller_phone: str
    queue_id: str = ""
    reason: str = ""


def week_api_interval(start: date, end: date) -> str:
    start_iso = f"{start.isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    end_iso = f"{(end + timedelta(days=1)).isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    return f"{start_iso}/{end_iso}"


def queue_lookup(client) -> dict[str, str]:
    return {(q.get("id") or ""): (q.get("name") or q.get("id") or "") for q in get_all_queues(client)}


def normalize_phone(value: str | None) -> str:
    if not value:
        return ""
    digits = re.sub(r"\D+", "", value)
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def first_callback_number(callback_obj: dict[str, Any]) -> str:
    for participant in callback_obj.get("participants") or []:
        nums = participant.get("callbackNumbers") or []
        if nums:
            return str(nums[0])
    for participant in callback_obj.get("participants") or []:
        if participant.get("purpose") == "customer":
            address = participant.get("address") or participant.get("ani")
            if address:
                return str(address)
    return ""


def find_agent_assignment(callback_obj: dict[str, Any]) -> tuple[dict[str, Any] | None, datetime | None]:
    agents = [p for p in callback_obj.get("participants") or [] if p.get("purpose") == "agent"]
    if not agents:
        return None, None
    agent = min(
        agents,
        key=lambda participant: participant.get("connectedTime")
        or participant.get("startAlertingTime")
        or participant.get("startTime")
        or "",
    )
    assigned = parse_dt(
        agent.get("connectedTime") or agent.get("startAlertingTime") or agent.get("startTime")
    )
    return agent, assigned


def fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def resolve_agent_name(client, agent: dict[str, Any] | None, cache: dict[str, str]) -> str:
    user_id = ((agent or {}).get("user") or {}).get("id") or ""
    if not user_id:
        return ""
    if user_id in cache:
        return cache[user_id]
    try:
        user = client.request("GET", f"/api/v2/users/{user_id}")
        cache[user_id] = (user.get("name") or user_id).strip()
    except Exception:
        cache[user_id] = user_id
    return cache[user_id]


def has_outbound_voice_segment(call_obj: dict[str, Any]) -> bool:
    for participant in call_obj.get("participants") or []:
        if (participant.get("direction") or "").lower() != "outbound":
            continue
        sessions = participant.get("sessions") or []
        if not sessions:
            return True
        for session in sessions:
            if (session.get("mediaType") or "").lower() == "voice":
                return True
    return False


def customer_callback_times(callback_obj: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    for participant in callback_obj.get("participants") or []:
        if participant.get("purpose") != "customer":
            continue
        return (
            parse_dt(participant.get("connectedTime")),
            parse_dt(participant.get("endTime")),
        )
    return None, None


def has_callback_media_outbound(callback_obj: dict[str, Any], assigned: datetime | None) -> bool:
    """Detect customer dial on callback media when calls object has no voice outbound leg."""
    if not assigned:
        return False
    customer_connected, customer_end = customer_callback_times(callback_obj)
    if not customer_connected or not customer_end or customer_end <= customer_connected:
        return False
    pickup_gap_sec = (assigned - customer_connected).total_seconds()
    if pickup_gap_sec < 0 or pickup_gap_sec > MAX_CALLBACK_PICKUP_GAP_SEC:
        return False
    customer_leg_sec = (customer_end - customer_connected).total_seconds()
    return customer_leg_sec <= MAX_CALLBACK_CUSTOMER_LEG_SEC


def has_outbound_dial(
    callback_obj: dict[str, Any],
    call_obj: dict[str, Any] | None,
    assigned: datetime | None,
) -> bool:
    if call_obj and has_outbound_voice_segment(call_obj):
        return True
    return has_callback_media_outbound(callback_obj, assigned)


def classify_no_outbound(
    callback_obj: dict[str, Any],
    call_obj: dict[str, Any] | None,
    *,
    created: datetime | None,
    assigned: datetime | None,
) -> str:
    if has_outbound_dial(callback_obj, call_obj, assigned):
        return ""
    if not assigned:
        return "Pending (No Agent)"
    agent_parts = [p for p in callback_obj.get("participants") or [] if p.get("purpose") == "agent"]
    disconnects = [p.get("disconnectType") for p in agent_parts if p.get("disconnectType")]
    if "client" in disconnects:
        return "Agent Disconnected - No Outbound"
    if disconnects:
        return "Agent Ended - No Outbound"
    if created and assigned and (assigned - created).total_seconds() > 3600:
        return "Agent Assigned Late - No Outbound"
    return "Agent Assigned - No Outbound"


ACCOUNTABILITY_REASONS = {
    "Agent Disconnected - No Outbound",
    "Agent Ended - No Outbound",
    "Agent Assigned Late - No Outbound",
    "Agent Assigned - No Outbound",
}


def outbound_answer_time(
    call_obj: dict[str, Any],
    callback_number: str,
    assigned_time: datetime,
    agent_user_id: str,
) -> datetime | None:
    target = normalize_phone(callback_number)
    candidates: list[tuple[int, datetime]] = []
    for participant in call_obj.get("participants") or []:
        if participant.get("direction") != "outbound":
            continue
        numbers = [participant.get("dnis"), participant.get("address"), participant.get("ani")]
        if target and target not in {normalize_phone(value) for value in numbers if value}:
            continue
        connected = parse_dt(participant.get("connectedTime"))
        started = parse_dt(participant.get("startTime"))
        event_time = connected or started
        if not event_time or event_time < assigned_time:
            continue
        minutes = (event_time - assigned_time).total_seconds() / 60.0
        if minutes > MAX_ANSWER_SEC / 60:
            continue
        participant_user = ((participant.get("user") or {}).get("id") or "")
        same_agent = bool(agent_user_id and participant_user == agent_user_id)
        is_agent_leg = participant.get("purpose") == "agent"
        rank = 0 if same_agent and is_agent_leg else 1 if is_agent_leg else 2
        candidates.append((rank, event_time))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][1]


def find_call_uri(callback_obj: dict[str, Any]) -> str:
    for uri in callback_obj.get("otherMediaUris") or []:
        if "/conversations/calls/" in uri:
            return uri if uri.startswith("/api") else f"/api/v2/conversations/calls/{callback_obj.get('id')}"
    cid = callback_obj.get("id")
    return f"/api/v2/conversations/calls/{cid}" if cid else ""


def callback_queue_id(callback_obj: dict[str, Any]) -> str:
    for participant in callback_obj.get("participants") or []:
        if participant.get("purpose") == "acd":
            queue = participant.get("queue") or {}
            return (queue.get("id") or "").strip()
    return ""


def callback_created_time(callback_obj: dict[str, Any]):
    for participant in callback_obj.get("participants") or []:
        if participant.get("purpose") == "acd":
            return parse_dt(participant.get("startTime") or participant.get("connectedTime"))
    starts = [parse_dt(p.get("startTime")) for p in callback_obj.get("participants") or []]
    starts = [value for value in starts if value]
    return min(starts) if starts else None


def inbound_customer_start(call_obj: dict[str, Any]):
    candidates = []
    for participant in call_obj.get("participants") or []:
        if participant.get("purpose") not in {"customer", "external"}:
            continue
        if (participant.get("direction") or "").lower() not in {"", "inbound"}:
            continue
        candidates.append(parse_dt(participant.get("startTime")))
    candidates = [value for value in candidates if value]
    return min(candidates) if candidates else None


def query_callback_ids(client, interval: str) -> list[str]:
    ids: list[str] = []
    page = 1
    body: dict[str, Any] = {
        "interval": interval,
        "order": "asc",
        "orderBy": "conversationStart",
        "segmentFilters": [
            {
                "type": "and",
                "predicates": [
                    {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "callback"},
                ],
            }
        ],
    }
    while True:
        body["paging"] = {"pageSize": 100, "pageNumber": page}
        resp = client.request("POST", "/api/v2/analytics/conversations/details/query", json_body=body)
        convs = resp.get("conversations") or []
        if not convs:
            break
        for conv in convs:
            cid = (conv.get("conversationId") or conv.get("id") or "").strip()
            if cid:
                ids.append(cid)
        if len(convs) < 100:
            break
        page += 1
    return ids


def fetch_callback_stats_by_queue(
    client,
    interval: str,
) -> tuple[dict[str, QueueCallbackStats], list[MissedCallbackRow]]:
    tool_log("info", "Indexing callback conversations for the week")
    conversation_ids = query_callback_ids(client, interval)
    tool_log("info", f"Measuring queue hold and answer time for {len(conversation_ids)} callbacks")
    stats: dict[str, QueueCallbackStats] = {}
    missed_rows: list[MissedCallbackRow] = []
    user_names: dict[str, str] = {}
    for index, conversation_id in enumerate(conversation_ids, 1):
        try:
            callback_obj = client.request("GET", f"/api/v2/conversations/callbacks/{conversation_id}")
        except Exception:
            continue
        queue_id = callback_queue_id(callback_obj)
        created = callback_created_time(callback_obj)
        callback_number = first_callback_number(callback_obj)
        agent, assigned_time = find_agent_assignment(callback_obj)
        agent_user_id = ((agent or {}).get("user") or {}).get("id") or ""
        agent_name = resolve_agent_name(client, agent, user_names)
        row = stats.setdefault(
            queue_id,
            QueueCallbackStats(queue_id=queue_id, queue_name=""),
        )
        row.callbacks_created += 1
        hold_sec: float | None = None
        answer_sec: float | None = None
        call_uri = find_call_uri(callback_obj)
        outbound_voice = False
        call_obj: dict[str, Any] | None = None
        if call_uri:
            try:
                call_obj = client.request("GET", call_uri)
                outbound_voice = has_outbound_voice_segment(call_obj)
                if created:
                    customer_start = inbound_customer_start(call_obj)
                    if customer_start and created > customer_start:
                        hold_sec = (created - customer_start).total_seconds()
                    if callback_number and assigned_time:
                        answered_at = outbound_answer_time(
                            call_obj,
                            callback_number,
                            assigned_time,
                            agent_user_id,
                        )
                        if answered_at and created and answered_at > created:
                            answer_sec = (answered_at - created).total_seconds()
            except Exception:
                outbound_voice = False
                call_obj = None
        reason = classify_no_outbound(
            callback_obj,
            call_obj,
            created=created,
            assigned=assigned_time,
        )
        if reason == "Pending (No Agent)":
            row.pending_no_agent_count += 1
        elif reason:
            row.no_outbound_count += 1
            missed_rows.append(
                MissedCallbackRow(
                    conversation_id=conversation_id,
                    agent_name=agent_name,
                    callback_at=created,
                    caller_phone=callback_number,
                    queue_id=queue_id,
                    reason=reason,
                )
            )
        if hold_sec is not None and 0 < hold_sec <= MAX_HOLD_SEC:
            row.wait_sum_ms += int(hold_sec * 1000)
            row.wait_count += 1
        if answer_sec is not None and 0 < answer_sec <= MAX_ANSWER_SEC:
            row.answer_sum_ms += int(answer_sec * 1000)
            row.answer_count += 1
        if index % 250 == 0 or index == len(conversation_ids):
            tool_log("info", f"Processed {index}/{len(conversation_ids)} callbacks")
    for row in stats.values():
        row.avg_wait_sec = (row.wait_sum_ms / row.wait_count / 1000.0) if row.wait_count else 0.0
        row.avg_answer_sec = (row.answer_sum_ms / row.answer_count / 1000.0) if row.answer_count else 0.0
    missed_rows.sort(
        key=lambda item: (
            item.callback_at or datetime.min.replace(tzinfo=timezone.utc),
            item.conversation_id,
        ),
        reverse=True,
    )
    return stats, missed_rows


def write_workbook(
    path: Path,
    rows: list[QueueCallbackStats],
    summary: QueueCallbackStats,
    missed_rows: list[MissedCallbackRow],
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
        "Callbacks Created",
        "Pending (No Agent)",
        "Agent No Outbound",
        "Avg Hold Before Callback",
        "Avg Callback Created to Answered",
    ]
    ws = wb.create_sheet("By Queue")
    ws.append(headers)
    for cell in ws[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    ws.append([
        "ORG TOTAL",
        summary.callbacks_created,
        summary.pending_no_agent_count,
        summary.no_outbound_count,
        fmt_seconds(summary.avg_wait_sec),
        fmt_seconds(summary.avg_answer_sec),
    ])
    for cell in ws[2]:
        cell.font = BOLD
        cell.fill = SUMMARY_FILL
    for row in sorted(rows, key=lambda item: (-item.callbacks_created, item.queue_name.lower())):
        ws.append([
            row.queue_name,
            row.callbacks_created,
            row.pending_no_agent_count,
            row.no_outbound_count,
            fmt_seconds(row.avg_wait_sec),
            fmt_seconds(row.avg_answer_sec),
        ])

    ws_missed = wb.create_sheet("Agent No Outbound")
    missed_headers = [
        "Conversation ID",
        "Reason",
        "Agent Name",
        "Callback Created",
        "Caller Phone Number",
    ]
    ws_missed.append(missed_headers)
    for cell in ws_missed[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    for missed in missed_rows:
        ws_missed.append([
            missed.conversation_id,
            missed.reason,
            missed.agent_name,
            fmt_dt(missed.callback_at),
            missed.caller_phone,
        ])

    for worksheet in (ws, ws_missed):
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
    parser = argparse.ArgumentParser(description="Weekly callback conversion report by queue")
    parser.add_argument("--output", default=None, help="Output .xlsx path")
    add_env_arg(parser)
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    _days, start_label, end_label = previous_calendar_week()
    interval = week_api_interval(date.fromisoformat(start_label), date.fromisoformat(end_label))
    tool_log("info", f"Window: {start_label} through {end_label}")

    names = queue_lookup(client)
    raw_stats, missed_rows = fetch_callback_stats_by_queue(client, interval)
    rows: list[QueueCallbackStats] = []
    total_callbacks = 0
    total_no_outbound = 0
    total_pending = 0
    total_wait_sum_ms = 0
    total_wait_count = 0
    total_answer_sum_ms = 0
    total_answer_count = 0
    for queue_id, row in raw_stats.items():
        row.queue_name = names.get(queue_id, UNKNOWN_QUEUE_NAME if not queue_id else queue_id)
        rows.append(row)
        total_callbacks += row.callbacks_created
        total_no_outbound += row.no_outbound_count
        total_pending += row.pending_no_agent_count
        total_wait_sum_ms += row.wait_sum_ms
        total_wait_count += row.wait_count
        total_answer_sum_ms += row.answer_sum_ms
        total_answer_count += row.answer_count
    summary = QueueCallbackStats(
        queue_id="org",
        queue_name="ORG TOTAL",
        callbacks_created=total_callbacks,
        wait_sum_ms=total_wait_sum_ms,
        wait_count=total_wait_count,
        avg_wait_sec=(total_wait_sum_ms / total_wait_count / 1000.0) if total_wait_count else 0.0,
        answer_sum_ms=total_answer_sum_ms,
        answer_count=total_answer_count,
        avg_answer_sec=(total_answer_sum_ms / total_answer_count / 1000.0) if total_answer_count else 0.0,
        no_outbound_count=total_no_outbound,
        pending_no_agent_count=total_pending,
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else output_path(f"callback_conversion_weekly_{stamp}.xlsx")
    meta = {
        "Generated At (UTC)": datetime.now(timezone.utc).isoformat(),
        "Window Start": start_label,
        "Window End": end_label,
        "Callbacks Created": "Callback conversations created during the week (queue-attributed)",
        "Avg Hold Before Callback": "Inbound call start to callback creation on linked voice leg (<= 2h counted)",
        "Avg Callback Created to Answered": (
            "Callback object created to outbound callback connected (customer answered), <= 24h counted"
        ),
        "No Outbound Call": (
            "Agent touched callback but no customer dial detected on linked calls voice outbound "
            "or callback-media outbound (agent pickup within 1h of customer callback connect, "
            "customer callback leg <= 2h)"
        ),
        "Pending (No Agent)": "Callback created but no agent assignment yet — not an agent miss",
        "Agent No Outbound Detail": "Accountability list only; excludes pending/no-agent callbacks",
        "Day Boundary": f"UTC-{TZ_OFFSET_HOURS:02d} (US Eastern DST)",
        "Analytics Interval": interval,
        "Queues Listed": str(len(rows)),
        "Total Callbacks Created": str(total_callbacks),
        "Total Pending (No Agent)": str(total_pending),
        "Total Agent No Outbound": str(total_no_outbound),
    }
    write_workbook(out, rows, summary, missed_rows, meta)
    write_report_meta(
        out,
        {
            "report_path": str(out),
            "window_start": start_label,
            "window_end": end_label,
            "queues_listed": len(rows),
            "total_callbacks": total_callbacks,
            "org_avg_wait_sec": summary.avg_wait_sec,
            "org_avg_answer_sec": summary.avg_answer_sec,
            "pending_no_agent_count": total_pending,
            "no_outbound_count": total_no_outbound,
            "no_outbound_detail_rows": len(missed_rows),
        },
    )
    tool_log("ok", f"Wrote {out}")
    tool_log("info", f"Pending (no agent): {total_pending}; Agent no outbound: {total_no_outbound}")
    if rows:
        top = max(rows, key=lambda row: row.callbacks_created)
        tool_log("info", f"Top queue: {top.queue_name} ({top.callbacks_created} callbacks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
