#!/usr/bin/env python3
"""Weekly Patient Services queue staffing gap report (Phase 1 — Erlang C vs on-queue FTE)."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, read_csv, setup_path, tool_log
from core.erlang import required_fte, workload_erlangs
from core.lookups import get_all_queues
from core.paths import PROJECT_ROOT
from tools.reporting.direct_outbound_agents_report import previous_calendar_week
from tools.reporting.spam_short_abandon_report import TZ_OFFSET_HOURS
from tools.reporting.staffing_metrics import (
    AgentBurdenRow,
    build_agent_burden_rows,
    fetch_logged_in_hours,
    fetch_queue_user_interval_occupancy,
    load_role_expectations,
    load_user_directory,
    queue_interval_actual_fte,
)

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

DEFAULT_CONFIG = PROJECT_ROOT / "config" / "staffing_queue_defaults.csv"
DEFAULT_ROLE_CONFIG = PROJECT_ROOT / "config" / "staffing_role_expectations.csv"
INTERVAL_MINUTES = 30
INTERVAL_SECONDS = INTERVAL_MINUTES * 60
QUEUE_PREFIX = "Patient Services"
EXCLUDE_NAME_FRAGMENTS = ("Corporate Training",)
EXCLUDED_QUEUE_PREFIXES = ("NCM_",)
TIME_METRICS = ("tTalk", "tHeld", "tAcw")
HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
SUMMARY_FILL = PatternFill("solid", fgColor="FFF2CC")
GAP_HIGH_FILL = PatternFill("solid", fgColor="F4CCCC")
BOLD = Font(bold=True)

METHODOLOGY_LINES = [
    "Phase 1 — Patient Services queues only. NCM excluded.",
    "Required FTE: Erlang C from 30-minute inbound voice intervals (offered × AHT).",
    "Shrinkage and minimum coverage applied from config/staffing_queue_defaults.csv.",
    "Avg Active Phone FTE: sum of agent handle+hold+ACW time in interval ÷ interval length.",
    "Genesys does not expose per-queue tOnQueueTime in this org; active phone FTE is the available proxy.",
    "Staffing gap = avg required FTE − avg active phone FTE (intervals with offered >= 1).",
    "Joined Now is a point-in-time snapshot at report generation.",
    "Logged In Hrs uses tAgentRoutingStatus (routing-capable time) — user presence API returns incorrect per-user data in this org.",
    "Phone Util % = clock phone hours ÷ routing hours (not burnout-adjusted).",
    "Qualifying Queue Hrs uses burnout factor (per-queue phone time summed).",
    "Phone Allocation Util compares actual phone hours to expected (20% × logged-in per role config).",
    "Agent Burden roles from config/staffing_role_expectations.csv.",
    "Burnout factor: qualifying queue hours sum per-queue phone time without normalizing to 1.0.",
    "ABI = Phone Allocation Utilization × Average Staffing Burden (included roles only).",
    "Not a performance scorecard — staffing and operational burden analysis.",
]


@dataclass
class StaffingDefaults:
    service_level_pct: float = 80.0
    service_level_seconds: float = 60.0
    shrinkage_pct: float = 30.0
    min_coverage_fte: float = 1.0


@dataclass
class IntervalBucket:
    offered: int = 0
    answered: int = 0
    abandon: int = 0
    talk_ms: int = 0
    hold_ms: int = 0
    acw_ms: int = 0
    handle_count: int = 0
    on_queue_ms: int = 0

    def aht_seconds(self) -> float:
        total_ms = self.talk_ms + self.hold_ms + self.acw_ms
        if self.handle_count <= 0 or total_ms <= 0:
            return 0.0
        return total_ms / self.handle_count / 1000.0


@dataclass
class QueueRollup:
    queue_id: str
    queue_name: str
    offered_total: int = 0
    intervals_with_traffic: int = 0
    required_fte_values: list[float] = field(default_factory=list)
    actual_fte_values: list[float] = field(default_factory=list)
    gap_values: list[float] = field(default_factory=list)
    joined_members_snapshot: int = 0

    @property
    def avg_required_fte(self) -> float:
        return round(sum(self.required_fte_values) / len(self.required_fte_values), 2) if self.required_fte_values else 0.0

    @property
    def peak_required_fte(self) -> float:
        return round(max(self.required_fte_values), 2) if self.required_fte_values else 0.0

    @property
    def avg_actual_fte(self) -> float:
        return round(sum(self.actual_fte_values) / len(self.actual_fte_values), 2) if self.actual_fte_values else 0.0

    @property
    def avg_staffing_gap(self) -> float:
        return round(sum(self.gap_values) / len(self.gap_values), 2) if self.gap_values else 0.0

    @property
    def peak_staffing_gap(self) -> float:
        return round(max(self.gap_values), 2) if self.gap_values else 0.0


def week_api_interval(start: date, end: date) -> str:
    start_iso = f"{start.isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    end_iso = f"{(end + timedelta(days=1)).isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    return f"{start_iso}/{end_iso}"


def load_staffing_defaults(config_path: Path, family_prefix: str) -> StaffingDefaults:
    rows = read_csv(config_path)
    for row in rows:
        prefix = (row.get("queue_family_prefix") or "").strip()
        if prefix.lower() != family_prefix.lower():
            continue
        return StaffingDefaults(
            service_level_pct=float(row.get("service_level_pct") or 80),
            service_level_seconds=float(row.get("service_level_seconds") or 60),
            shrinkage_pct=float(row.get("shrinkage_pct") or 30),
            min_coverage_fte=float(row.get("min_coverage_fte") or 1),
        )
    return StaffingDefaults()


def _include_queue(name: str, queue_prefix: str) -> bool:
    normalized = (name or "").strip()
    if not normalized.startswith(queue_prefix):
        return False
    if any(fragment.lower() in normalized.lower() for fragment in EXCLUDE_NAME_FRAGMENTS):
        return False
    if any(normalized.startswith(prefix) for prefix in EXCLUDED_QUEUE_PREFIXES):
        return False
    return True


def include_queue(name: str) -> bool:
    return _include_queue(name, QUEUE_PREFIX)


def metric_stats(bucket: dict[str, Any], metric: str) -> tuple[int, int]:
    for row in bucket.get("metrics") or []:
        if (row.get("metric") or "").strip() != metric:
            continue
        stats = row.get("stats") or {}
        count = int(stats.get("count") or 0)
        total = int(stats.get("sum") or 0)
        return count, total
    return 0, 0


def interval_key(interval_value: str) -> str:
    return (interval_value or "").split("/")[0]


def fetch_conversation_intervals(client, interval: str, queue_ids: set[str]) -> dict[tuple[str, str], IntervalBucket]:
    body = {
        "interval": interval,
        "granularity": "PT30M",
        "groupBy": ["queueId"],
        "filter": {
            "type": "and",
            "predicates": [
                {"type": "dimension", "dimension": "direction", "operator": "matches", "value": "inbound"},
                {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "voice"},
            ],
        },
        "metrics": ["nOffered", "tAnswered", "tAbandon", *TIME_METRICS],
    }
    tool_log("info", "Querying 30-minute inbound voice queue aggregates")
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)
    buckets: dict[tuple[str, str], IntervalBucket] = {}

    for result in resp.get("results") or []:
        group = result.get("group") or {}
        queue_id = (group.get("queueId") or "").strip()
        if queue_id not in queue_ids:
            continue
        for data_bucket in result.get("data") or []:
            key = (queue_id, interval_key(data_bucket.get("interval") or ""))
            row = buckets.setdefault(key, IntervalBucket())
            offered_count, _ = metric_stats(data_bucket, "nOffered")
            answered_count, _ = metric_stats(data_bucket, "tAnswered")
            abandon_count, _ = metric_stats(data_bucket, "tAbandon")
            row.offered += offered_count
            row.answered += answered_count
            row.abandon += abandon_count
            talk_count, talk_ms = metric_stats(data_bucket, "tTalk")
            hold_count, hold_ms = metric_stats(data_bucket, "tHeld")
            acw_count, acw_ms = metric_stats(data_bucket, "tAcw")
            row.talk_ms += talk_ms
            row.hold_ms += hold_ms
            row.acw_ms += acw_ms
            row.handle_count += max(talk_count, hold_count, acw_count, offered_count)
    return buckets


def joined_member_counts(client, queue_id: str) -> int:
    try:
        members = client.paged_get(f"/api/v2/routing/queues/{queue_id}/members")
    except Exception:
        return 0
    return sum(1 for member in members if member.get("joined") is True)


def build_rollups(
    queue_names: dict[str, str],
    conv_buckets: dict[tuple[str, str], IntervalBucket],
    actual_fte_by_interval: dict[tuple[str, str], float],
    defaults: StaffingDefaults,
    *,
    joined_snapshot: dict[str, int],
) -> dict[str, QueueRollup]:
    rollups: dict[str, QueueRollup] = {}
    sl_target = defaults.service_level_pct / 100.0
    shrinkage = defaults.shrinkage_pct / 100.0

    for queue_id, queue_name in queue_names.items():
        rollups[queue_id] = QueueRollup(
            queue_id=queue_id,
            queue_name=queue_name,
            joined_members_snapshot=joined_snapshot.get(queue_id, 0),
        )

    seen: set[tuple[str, str]] = set()
    for key, bucket in conv_buckets.items():
        queue_id, interval_start = key
        if bucket.offered <= 0:
            continue
        seen.add(key)
        rollup = rollups.get(queue_id)
        if rollup is None:
            continue

        aht = bucket.aht_seconds()
        if aht <= 0:
            aht = 240.0  # fallback 4 min when components missing but calls offered

        req = required_fte(
            bucket.offered,
            aht,
            INTERVAL_SECONDS,
            sl_target=sl_target,
            target_seconds=defaults.service_level_seconds,
            shrinkage=shrinkage,
            min_coverage=defaults.min_coverage_fte,
        )
        actual = actual_fte_by_interval.get(key, 0.0)
        gap = round(req - actual, 2)

        rollup.offered_total += bucket.offered
        rollup.intervals_with_traffic += 1
        rollup.required_fte_values.append(req)
        rollup.actual_fte_values.append(actual)
        rollup.gap_values.append(gap)

    return rollups


def rollup_rows(rollups: dict[str, QueueRollup]) -> list[QueueRollup]:
    rows = [r for r in rollups.values() if r.intervals_with_traffic > 0]
    rows.sort(key=lambda r: (-r.avg_staffing_gap, -r.offered_total, r.queue_name))
    return rows


def _format_sheet(ws) -> None:
    for column_cells in ws.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 52)
    ws.freeze_panes = "A2"


def write_workbook(
    path: Path,
    summary_rows: list[QueueRollup],
    agent_rows: list[AgentBurdenRow],
    meta: dict[str, str],
) -> None:
    wb = Workbook()

    ws_about = wb.active
    ws_about.title = "About"
    ws_about.append(["Field", "Value"])
    for key, value in meta.items():
        ws_about.append([key, value])
    ws_about.append([])
    ws_about.append(["Methodology", ""])
    for line in METHODOLOGY_LINES:
        ws_about.append(["", line])
    _format_sheet(ws_about)

    headers = [
        "Queue",
        "Offered (week)",
        "Intervals w/ traffic",
        "Avg Required FTE",
        "Peak Required FTE",
        "Avg Active Phone FTE",
        "Avg Staffing Gap",
        "Peak Staffing Gap",
        "Joined Now (snapshot)",
    ]
    ws = wb.create_sheet("Queue Staffing Gap")
    ws.append(headers)
    for cell in ws[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL

    total_offered = sum(row.offered_total for row in summary_rows)
    gaps = [row.avg_staffing_gap for row in summary_rows if row.avg_staffing_gap]
    org_avg_gap = round(sum(gaps) / len(gaps), 2) if gaps else 0.0
    ws.append([
        "ORG SUMMARY (queues with traffic)",
        total_offered,
        "",
        "",
        "",
        "",
        org_avg_gap,
        "",
        "",
    ])
    for cell in ws[2]:
        cell.font = BOLD
        cell.fill = SUMMARY_FILL

    for row in summary_rows:
        cells = [
            row.queue_name,
            row.offered_total,
            row.intervals_with_traffic,
            row.avg_required_fte,
            row.peak_required_fte,
            row.avg_actual_fte,
            row.avg_staffing_gap,
            row.peak_staffing_gap,
            row.joined_members_snapshot,
        ]
        ws.append(cells)
        if row.avg_staffing_gap >= 1.0:
            for cell in ws[ws.max_row]:
                cell.fill = GAP_HIGH_FILL

    _format_sheet(ws)

    agent_headers = [
        "Agent",
        "Title",
        "Role Match",
        "Logged In Hrs (routing)",
        "Phone Controlled Hrs",
        "Qualifying Queue Hrs (burnout)",
        "Expected Phone Hrs",
        "Phone Util %",
        "Phone Allocation Util",
        "Excess Burden Hrs",
        "Avg Staffing Burden",
        "ABI",
        "Queues Active",
        "In Burden Report",
    ]
    ws_agents = wb.create_sheet("Agent Burden")
    ws_agents.append(agent_headers)
    for cell in ws_agents[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL

    scored = [row for row in agent_rows if row.include_in_burden]
    if scored:
        top_abi = max(scored, key=lambda row: row.abi)
        ws_agents.append([
            "ORG SUMMARY (scored roles)",
            "",
            "",
            "",
            round(sum(row.phone_controlled_hours for row in scored), 1),
            "",
            "",
            "",
            "",
            round(sum(row.excess_burden_hours for row in scored), 1),
            "",
            top_abi.abi,
            "",
            len(scored),
        ])
        for cell in ws_agents[2]:
            cell.font = BOLD
            cell.fill = SUMMARY_FILL

    for row in agent_rows:
        ws_agents.append([
            row.name,
            row.title,
            row.role_match,
            row.logged_in_hours,
            row.phone_controlled_hours,
            row.qualifying_queue_hours,
            row.expected_phone_hours,
            row.phone_utilization_pct if row.phone_utilization_pct is not None else "",
            row.phone_allocation_util,
            row.excess_burden_hours,
            row.avg_staffing_burden,
            row.abi if row.include_in_burden else "",
            row.queues_active,
            "yes" if row.include_in_burden else "no",
        ])
        if row.include_in_burden and row.abi >= 1.35:
            for cell in ws_agents[ws_agents.max_row]:
                cell.fill = GAP_HIGH_FILL

    _format_sheet(ws_agents)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def write_report_meta(path: Path, payload: dict[str, Any]) -> Path:
    meta_path = path.parent / f"{path.stem}_meta.json"
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return meta_path


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Weekly PS queue staffing gap report (Phase 1)")
    parser.add_argument("--output", default=None, help="Output .xlsx path")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Staffing defaults CSV")
    parser.add_argument("--queue-prefix", default=QUEUE_PREFIX, help="Queue name prefix filter")
    parser.add_argument("--role-config", default=str(DEFAULT_ROLE_CONFIG), help="Role expectations CSV")
    add_env_arg(parser)
    args = parser.parse_args()

    queue_prefix = args.queue_prefix
    client = create_client(log=tool_log, env_file=args.env_file)
    days, start_label, end_label = previous_calendar_week()
    interval = week_api_interval(date.fromisoformat(start_label), date.fromisoformat(end_label))
    tool_log("info", f"Window: {start_label} through {end_label}")

    defaults = load_staffing_defaults(Path(args.config), queue_prefix)
    role_expectations = load_role_expectations(read_csv(Path(args.role_config)))
    all_queues = get_all_queues(client)
    scoped = [q for q in all_queues if _include_queue(q.get("name") or "", queue_prefix)]
    queue_names = {(q.get("id") or ""): (q.get("name") or "") for q in scoped}
    queue_ids = set(queue_names)
    tool_log("info", f"Scoped queues: {len(queue_ids)} ({queue_prefix})")

    if not queue_ids:
        raise SystemExit("No queues matched scope")

    occupancy = fetch_queue_user_interval_occupancy(client, interval, queue_ids)
    actual_fte_by_interval = queue_interval_actual_fte(occupancy, INTERVAL_SECONDS)
    conv_buckets = fetch_conversation_intervals(client, interval, queue_ids)

    joined_snapshot: dict[str, int] = {}
    for index, queue_id in enumerate(sorted(queue_ids), start=1):
        if index == 1 or index % 25 == 0:
            tool_log("info", f"Joined snapshot {index}/{len(queue_ids)}")
        joined_snapshot[queue_id] = joined_member_counts(client, queue_id)

    rollups = build_rollups(
        queue_names,
        conv_buckets,
        actual_fte_by_interval,
        defaults,
        joined_snapshot=joined_snapshot,
    )
    summary_rows = rollup_rows(rollups)

    active_user_ids = sorted({user_id for (_q, _i, user_id) in occupancy})
    logged_in_hours = fetch_logged_in_hours(client, interval, active_user_ids)
    user_directory = load_user_directory(client, set(active_user_ids))
    agent_rows = build_agent_burden_rows(
        occupancy,
        logged_in_hours,
        user_directory,
        role_expectations,
        interval_seconds=INTERVAL_SECONDS,
    )
    scored_agents = [row for row in agent_rows if row.include_in_burden]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else output_path(f"queue_staffing_gap_weekly_{stamp}.xlsx")
    meta = {
        "Generated At (UTC)": datetime.now(timezone.utc).isoformat(),
        "Window Start": start_label,
        "Window End": end_label,
        "Period": "previous-week",
        "Queue Scope": f"{queue_prefix} (NCM excluded)",
        "Interval Granularity": f"{INTERVAL_MINUTES} minutes",
        "Service Level Target": f"{defaults.service_level_pct:.0f}% in {defaults.service_level_seconds:.0f}s",
        "Shrinkage": f"{defaults.shrinkage_pct:.0f}%",
        "Minimum Coverage FTE": str(defaults.min_coverage_fte),
        "Config File": str(args.config),
        "Role Config File": str(args.role_config),
        "Queues In Scope": str(len(queue_ids)),
        "Queues With Traffic": str(len(summary_rows)),
        "Agents With PS Phone Activity": str(len(active_user_ids)),
        "Agents Scored For ABI": str(len(scored_agents)),
        "Day Boundary": f"UTC-{TZ_OFFSET_HOURS:02d} (US Eastern DST)",
        "Analytics Interval": interval,
        "Sort Order": "Queues by staffing gap; agents by ABI (scored roles first)",
    }
    write_workbook(out, summary_rows, agent_rows, meta)
    write_report_meta(
        out,
        {
            "report_path": str(out),
            "window_start": start_label,
            "window_end": end_label,
            "queues_in_scope": len(queue_ids),
            "queues_with_traffic": len(summary_rows),
            "agents_scored": len(scored_agents),
            "queue_prefix": queue_prefix,
        },
    )
    tool_log("ok", f"Wrote {out}")
    if summary_rows:
        top = summary_rows[0]
        tool_log(
            "info",
            f"Top gap: {top.queue_name} (avg gap {top.avg_staffing_gap}, offered {top.offered_total})",
        )
    else:
        tool_log("warn", "No queues with inbound traffic in window")
    if scored_agents:
        top_agent = scored_agents[0]
        tool_log("info", f"Top ABI: {top_agent.name} ({top_agent.abi})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
