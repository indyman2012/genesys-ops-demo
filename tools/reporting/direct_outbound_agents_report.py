#!/usr/bin/env python3
"""Agents with high direct (non-queue) outbound call volume over a rolling window."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log
from core.lookups import get_all_queues

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

TZ_OFFSET_HOURS = 4  # US Eastern DST
MIN_DIRECT_OUTBOUND = 100
ADAPTIVE_PRIMARY_THRESHOLD = 30
ADAPTIVE_FALLBACK_THRESHOLD = 25
ADAPTIVE_MIN_AGENTS = 5
HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
BOLD = Font(bold=True)


def rolling_days(days: int) -> tuple[list[date], str, str]:
    today = date.today()
    start = today - timedelta(days=days - 1)
    day_list = [start + timedelta(days=i) for i in range(days)]
    return day_list, start.isoformat(), today.isoformat()


def previous_calendar_week(reference: date | None = None) -> tuple[list[date], str, str]:
    """Return Mon-Sun for the week before the current one when run on Monday."""
    ref = reference or date.today()
    if ref.weekday() == 0:
        end = ref - timedelta(days=1)
    elif ref.weekday() == 6:
        end = ref
    else:
        end = ref - timedelta(days=ref.weekday() + 1)
    start = end - timedelta(days=6)
    day_list = [start + timedelta(days=i) for i in range(7)]
    return day_list, start.isoformat(), end.isoformat()


def previous_business_days(count: int, reference: date | None = None) -> tuple[list[date], str, str]:
    """Return the last N Mon-Fri days ending on reference (inclusive)."""
    ref = reference or date.today()
    days: list[date] = []
    cursor = ref
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return days, days[0].isoformat(), days[-1].isoformat()


def resolve_period(period: str, days: int) -> tuple[list[date], str, str, str]:
    if period == "previous-week":
        day_list, start_label, end_label = previous_calendar_week()
        return day_list, start_label, end_label, "previous-week"
    if period == "business-days":
        day_list, start_label, end_label = previous_business_days(days)
        return day_list, start_label, end_label, f"business-{days}d"
    day_list, start_label, end_label = rolling_days(days)
    return day_list, start_label, end_label, f"rolling-{days}d"


def day_interval(day: date) -> str:
    start = f"{day.isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    next_day = day + timedelta(days=1)
    end = f"{next_day.isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    return f"{start}/{end}"


def iter_sessions(conv: dict[str, Any]):
    for participant in conv.get("participants") or []:
        purpose = (participant.get("purpose") or "").lower()
        for session in participant.get("sessions") or []:
            media_type = (session.get("mediaType") or "").lower()
            yield participant, purpose, session, media_type


def conversation_has_voice(conv: dict[str, Any]) -> bool:
    return any(media == "voice" for _p, _purpose, _session, media in iter_sessions(conv))


AGENT_PURPOSES = frozenset({"agent", "user"})


def conversation_has_queue_assignment(conv: dict[str, Any]) -> bool:
    """True when any participant/session/segment carries a queueId (incl. outbound-with-queue)."""
    for participant in conv.get("participants") or []:
        purpose = (participant.get("purpose") or "").lower()
        if purpose == "acd" or participant.get("queueId"):
            return True
        for session in participant.get("sessions") or []:
            if session.get("queueId"):
                return True
            for segment in session.get("segments") or []:
                if segment.get("queueId"):
                    return True
    return False


def direct_outbound_agent_id(conv: dict[str, Any]) -> str | None:
    if not conversation_has_voice(conv):
        return None
    if conversation_has_queue_assignment(conv):
        return None

    for participant in conv.get("participants") or []:
        purpose = (participant.get("purpose") or "").lower()
        if purpose not in AGENT_PURPOSES:
            continue
        user = participant.get("user") or {}
        user_id = (participant.get("userId") or user.get("id") or "").strip()
        if not user_id:
            continue
        for session in participant.get("sessions") or []:
            if (session.get("mediaType") or "").lower() != "voice":
                continue
            direction = (session.get("direction") or participant.get("direction") or "").lower()
            if direction and direction != "outbound":
                continue
            return user_id
    return None


def fetch_direct_outbound_counts(client, days: list[date]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    body_base = {
        "order": "asc",
        "orderBy": "conversationStart",
        "segmentFilters": [
            {
                "type": "and",
                "predicates": [
                    {"type": "dimension", "dimension": "direction", "operator": "matches", "value": "outbound"},
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
                agent_id = direct_outbound_agent_id(conv)
                if agent_id:
                    counts[agent_id] += 1
                    day_count += 1
            if len(convs) < 100:
                break
            page += 1
        if index % 2 == 0 or index == len(days):
            tool_log("info", f"Outbound scan {index}/{len(days)} days — {day.isoformat()}: {day_count} direct")
    return dict(counts)


def user_title(user: dict[str, Any]) -> str:
    title = user.get("title")
    if title:
        return str(title).strip()
    profile = user.get("profile") or {}
    if isinstance(profile, dict):
        for key in ("title", "jobTitle"):
            if profile.get(key):
                return str(profile[key]).strip()
    return ""


def load_user_info(client, user_ids: set[str]) -> dict[str, dict[str, str]]:
    info: dict[str, dict[str, str]] = {}
    tool_log("info", "Loading user directory for name and title resolution")
    for user in client.paged_get("/api/v2/users", params={"state": "any"}):
        uid = (user.get("id") or "").strip()
        if uid not in user_ids:
            continue
        info[uid] = {
            "name": (user.get("name") or uid).strip(),
            "title": user_title(user),
        }
    missing = user_ids - set(info)
    for uid in sorted(missing):
        try:
            user = client.request("GET", f"/api/v2/users/{uid}")
            info[uid] = {
                "name": (user.get("name") or uid).strip(),
                "title": user_title(user),
            }
        except Exception:
            info[uid] = {"name": uid, "title": ""}
    return info


def build_user_joined_queues(
    client,
    queues: list[dict[str, Any]],
    user_ids: set[str],
) -> dict[str, list[str]]:
    user_queues: dict[str, list[str]] = defaultdict(list)
    total = len(queues)
    for i, q in enumerate(queues, 1):
        qid = q["id"]
        qname = (q.get("name") or qid).strip()
        if i == 1 or i % 25 == 0 or i == total:
            tool_log("info", f"Scanning joined=true members {i}/{total} queues...")
        for member in client.paged_get(
            f"/api/v2/routing/queues/{qid}/members",
            params={"pageSize": 100},
        ):
            if member.get("joined") is not True:
                continue
            user = member.get("user") or {}
            uid = (user.get("id") or member.get("id") or "").strip()
            if not uid or uid not in user_ids:
                continue
            user_queues[uid].append(qname)
    return {uid: sorted(set(names)) for uid, names in user_queues.items()}


def agents_over_threshold(counts: dict[str, int], threshold: int) -> dict[str, int]:
    return {uid: n for uid, n in counts.items() if n > threshold}


def choose_threshold(
    counts: dict[str, int],
    *,
    primary: int,
    fallback: int,
    min_agents: int,
) -> tuple[int, str]:
    primary_hits = len(agents_over_threshold(counts, primary))
    if primary_hits < min_agents:
        fallback_hits = len(agents_over_threshold(counts, fallback))
        tool_log(
            "info",
            f"Only {primary_hits} agents over {primary}; using fallback threshold {fallback} ({fallback_hits} agents)",
        )
        return fallback, "fallback"
    tool_log("info", f"{primary_hits} agents over {primary}; using primary threshold")
    return primary, "primary"


def build_agent_rows(client, qualifying: dict[str, int]) -> list[tuple[str, str, int, str]]:
    if not qualifying:
        return []
    user_ids = set(qualifying)
    users = load_user_info(client, user_ids)
    queues = get_all_queues(client)
    joined_queues = build_user_joined_queues(client, queues, user_ids)
    rows: list[tuple[str, str, int, str]] = []
    for uid, count in sorted(
        qualifying.items(),
        key=lambda item: (-item[1], users.get(item[0], {}).get("name", item[0])),
    ):
        profile = users.get(uid, {})
        name = profile.get("name", uid)
        title = profile.get("title", "")
        queue_list = " | ".join(joined_queues.get(uid, []))
        rows.append((name, title, count, queue_list))
    return rows


def write_report_meta(path: Path, payload: dict[str, Any]) -> Path:
    meta_path = path.parent / f"{path.stem}_meta.json"
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return meta_path


def write_workbook(
    path: Path,
    threshold_rows: list[tuple[str, str, int, str]],
    all_rows: list[tuple[str, str, int, str]],
    meta: dict[str, str],
) -> None:
    wb = Workbook()
    ws_about = wb.active
    ws_about.title = "About"
    ws_about.append(["Field", "Value"])
    for key, value in meta.items():
        ws_about.append([key, value])

    def _write_agent_sheet(title: str, rows: list[tuple[str, str, int, str]]) -> None:
        ws = wb.create_sheet(title)
        headers = ["Name", "Job Title", "Total Count", "Joined Queues"]
        ws.append(headers)
        for cell in ws[1]:
            cell.font = BOLD
            cell.fill = HEADER_FILL
        for name, job_title, count, queues in rows:
            ws.append([name, job_title, count, queues])
        for column_cells in ws.columns:
            width = max(len(str(cell.value or "")) for cell in column_cells)
            ws.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 80)
        ws.freeze_panes = "A2"

    _write_agent_sheet("Agents", threshold_rows)
    _write_agent_sheet("All Agents", all_rows)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(
        description="List agents with >N direct outbound calls (no queue) in the last two weeks",
    )
    parser.add_argument("--days", type=int, default=14, help="Rolling window in days (default: 14)")
    parser.add_argument(
        "--period",
        choices=("rolling", "previous-week", "business-days"),
        default="rolling",
        help="rolling = last N calendar days; previous-week = prior Mon-Sun; business-days = last N weekdays (Mon-Fri)",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=None,
        help=f"Minimum direct outbound calls (default: {MIN_DIRECT_OUTBOUND}, or adaptive primary when --adaptive-threshold)",
    )
    parser.add_argument(
        "--adaptive-threshold",
        action="store_true",
        help=(
            f"Start at --primary-threshold (default {ADAPTIVE_PRIMARY_THRESHOLD}); "
            f"if fewer than --min-agents qualify, use --fallback-threshold (default {ADAPTIVE_FALLBACK_THRESHOLD})"
        ),
    )
    parser.add_argument("--primary-threshold", type=int, default=ADAPTIVE_PRIMARY_THRESHOLD)
    parser.add_argument("--fallback-threshold", type=int, default=ADAPTIVE_FALLBACK_THRESHOLD)
    parser.add_argument(
        "--min-agents",
        type=int,
        default=ADAPTIVE_MIN_AGENTS,
        help="Minimum qualifying agents required to keep the primary threshold",
    )
    parser.add_argument("--output", default=None, help="Output .xlsx path")
    add_env_arg(parser)
    args = parser.parse_args()

    if args.min_count is None:
        args.min_count = args.primary_threshold if args.adaptive_threshold else MIN_DIRECT_OUTBOUND

    client = create_client(log=tool_log, env_file=args.env_file)
    days, start_label, end_label, period_label = resolve_period(args.period, args.days)
    tool_log("info", f"Period: {period_label}")
    tool_log("info", f"Window: {start_label} through {end_label} (inclusive, US Eastern day boundaries)")
    tool_log("info", "Scanning outbound conversation details (no queue assignment)")

    counts = fetch_direct_outbound_counts(client, days)
    threshold_mode = "fixed"
    if args.adaptive_threshold:
        applied_threshold, threshold_mode = choose_threshold(
            counts,
            primary=args.primary_threshold,
            fallback=args.fallback_threshold,
            min_agents=args.min_agents,
        )
    else:
        applied_threshold = args.min_count

    qualifying = agents_over_threshold(counts, applied_threshold)
    tool_log("info", f"Agents over threshold ({applied_threshold}): {len(qualifying)}")
    tool_log("info", f"Agents with any direct outbound: {len(counts)}")

    threshold_rows = build_agent_rows(client, qualifying)
    all_rows = build_agent_rows(client, counts)
    if not threshold_rows:
        tool_log("warn", "No agents matched the applied threshold")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else output_path(f"direct_outbound_agents_{stamp}.xlsx")
    threshold_policy = (
        f">{args.primary_threshold}, fallback >{args.fallback_threshold} if fewer than {args.min_agents} agents"
        if args.adaptive_threshold
        else f">{applied_threshold}"
    )
    meta = {
        "Generated At (UTC)": datetime.now(timezone.utc).isoformat(),
        "Window Start": start_label,
        "Window End": end_label,
        "Period": period_label,
        "Days": str(len(days)),
        "Threshold Policy": threshold_policy,
        "Applied Threshold": str(applied_threshold),
        "Threshold Mode": threshold_mode,
        "Min Direct Outbound Count": str(applied_threshold),
        "Definition": "Outbound voice, no queueId anywhere; attributed to agent/user participant voice leg",
        "Purpose": "Educational: agents dialing direct instead of on behalf of a queue",
        "Joined Queues": "Pipe-separated queue names where routing queue member joined=true",
        "Job Title": "Genesys user profile title field",
        "Day Boundary": f"UTC-{TZ_OFFSET_HOURS:02d} (US Eastern DST)",
        "Agents Matching (Agents sheet)": str(len(threshold_rows)),
        "All Agents With Direct Outbound (All Agents sheet)": str(len(all_rows)),
        "Total Direct Outbound (all agents)": str(sum(counts.values())),
    }
    write_workbook(out, threshold_rows, all_rows, meta)
    write_report_meta(
        out,
        {
            "report_path": str(out),
            "window_start": start_label,
            "window_end": end_label,
            "applied_threshold": applied_threshold,
            "threshold_mode": threshold_mode,
            "threshold_policy": threshold_policy,
            "agents_matching": len(threshold_rows),
            "all_agents_count": len(all_rows),
            "adaptive_threshold": args.adaptive_threshold,
            "primary_threshold": args.primary_threshold,
            "fallback_threshold": args.fallback_threshold,
            "min_agents": args.min_agents,
        },
    )
    tool_log("ok", f"Wrote {out}")
    if threshold_rows:
        top = threshold_rows[0]
        tool_log("info", f"Top agent (threshold sheet): {top[0]} ({top[2]} direct outbound)")
    if all_rows:
        top_all = all_rows[0]
        tool_log("info", f"Top agent (all sheet): {top_all[0]} ({top_all[2]} direct outbound)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
