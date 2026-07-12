#!/usr/bin/env python3
"""Biweekly inbound voice AHT trend report — 4 weekly buckets, HTML charts, queue + agent drill-down."""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log
from core.lookups import get_all_queues
from tools.reporting.spam_short_abandon_report import TZ_OFFSET_HOURS

MIN_AGENT_HANDLES = 10
WEEK_COUNT = 4
EXCLUDED_QUEUE_PREFIXES = ("NCM",)
TIME_METRICS = ("tHandle", "tTalk", "tHeld", "tAcw")
ASA_METRIC = "tAnswered"
METRIC_DEFS: dict[str, dict[str, Any]] = {
    "tHandle": {
        "label": "Handle",
        "baseline_minutes": 4.0,
        "baseline_label": "4:00",
        "color": "#2f5d9a",
    },
    "tTalk": {
        "label": "Talk",
        "baseline_minutes": 3.0,
        "baseline_label": "3:00",
        "color": "#2a9d6f",
    },
    "tHeld": {
        "label": "Hold",
        "baseline_minutes": 1.0,
        "baseline_label": "1:00",
        "color": "#e9c46a",
    },
    "tAcw": {
        "label": "ACW",
        "baseline_minutes": 1.0,
        "baseline_label": "1:00",
        "color": "#e76f51",
    },
}
ASA_BAR_COLOR = "rgba(118, 84, 154, 0.55)"


def empty_counts() -> list[int]:
    return [0] * WEEK_COUNT


def empty_time_minutes() -> dict[str, list[float | None]]:
    return {metric: [None] * WEEK_COUNT for metric in TIME_METRICS}


def empty_asa_daily() -> list[float | None]:
    return [None] * len(current_report_days)


@dataclass
class WeekBucket:
    start: date
    end: date
    label: str
    key: str


@dataclass
class MetricSeries:
    counts: list[int] = field(default_factory=empty_counts)
    time_minutes: dict[str, list[float | None]] = field(default_factory=empty_time_minutes)
    asa_seconds_daily: list[float | None] = field(default_factory=empty_asa_daily)

    def total_handles(self) -> int:
        return sum(self.counts)

    def latest_handle(self) -> float | None:
        return self.time_minutes["tHandle"][-1]


def calendar_weeks(reference: date | None = None) -> tuple[list[WeekBucket], str, str]:
    """Complete Mon-Sun weeks ending on the Sunday before the current/report week."""
    ref = reference or date.today()
    if ref.weekday() == 0:
        latest_end = ref - timedelta(days=1)
    elif ref.weekday() == 6:
        latest_end = ref
    else:
        latest_end = ref - timedelta(days=ref.weekday() + 1)

    buckets: list[WeekBucket] = []
    for offset in range(WEEK_COUNT - 1, -1, -1):
        end = latest_end - timedelta(days=7 * offset)
        start = end - timedelta(days=6)
        key = start.isoformat()
        label = f"{start.strftime('%b %d')}–{end.strftime('%b %d')}"
        buckets.append(WeekBucket(start=start, end=end, label=label, key=key))
    overall_start = buckets[0].start.isoformat()
    overall_end = buckets[-1].end.isoformat()
    return buckets, overall_start, overall_end


def range_api_interval(start: date, end: date) -> str:
    start_iso = f"{start.isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    end_iso = f"{(end + timedelta(days=1)).isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    return f"{start_iso}/{end_iso}"


def day_key_from_interval(interval_value: str) -> str:
    return (interval_value or "").split("/")[0][:10]


def reporting_days(week_buckets: list[WeekBucket]) -> list[date]:
    days: list[date] = []
    cursor = week_buckets[0].start
    end = week_buckets[-1].end
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def week_index_for_day(day: date, week_buckets: list[WeekBucket]) -> int:
    for index, bucket in enumerate(week_buckets):
        if bucket.start <= day <= bucket.end:
            return index
    return max(len(week_buckets) - 1, 0)


def expand_weekly_to_daily(
    weekly_values: list[float | None],
    days: list[date],
    week_buckets: list[WeekBucket],
) -> list[float | None]:
    return [weekly_values[week_index_for_day(day, week_buckets)] for day in days]


def latest_week_asa_avg(asa_daily: list[float | None], week_buckets: list[WeekBucket]) -> float | None:
    if not week_buckets or not current_report_days:
        return None
    last_week = week_buckets[-1]
    values = [
        asa_daily[index]
        for index, day in enumerate(current_report_days)
        if last_week.start <= day <= last_week.end and asa_daily[index] is not None
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def parse_metric_values(data_bucket: dict[str, Any]) -> dict[str, tuple[int, float | None]]:
    parsed: dict[str, tuple[int, float | None]] = {}
    for metric in data_bucket.get("metrics") or []:
        name = (metric.get("metric") or "").strip()
        stats = metric.get("stats") or {}
        count = int(stats.get("count") or 0)
        total_ms = int(stats.get("sum") or 0)
        if count <= 0 or total_ms <= 0:
            parsed[name] = (0, None)
            continue
        if name == ASA_METRIC:
            parsed[name] = (count, round(total_ms / count / 1000.0, 1))
        elif name in TIME_METRICS:
            parsed[name] = (count, round((total_ms / count) / 60000, 2))
    return parsed


def fetch_weekly_metrics(
    client,
    interval: str,
    group_by: list[str],
) -> dict[tuple[str, ...], MetricSeries]:
    body = {
        "interval": interval,
        "granularity": "P1W",
        "groupBy": group_by,
        "filter": {
            "type": "and",
            "predicates": [
                {"type": "dimension", "dimension": "direction", "operator": "matches", "value": "inbound"},
                {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "voice"},
            ],
        },
        "metrics": [*TIME_METRICS],
    }
    tool_log("info", f"Querying weekly handle components ({', '.join(group_by)})")
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)
    week_keys = {bucket.key for bucket in current_week_buckets}
    series_map: dict[tuple[str, ...], MetricSeries] = {}

    for result in resp.get("results") or []:
        group = result.get("group") or {}
        key_parts = [(group.get(dimension) or "").strip() for dimension in group_by]
        row_key = tuple(key_parts)
        series = series_map.setdefault(row_key, MetricSeries())
        for data_bucket in result.get("data") or []:
            wk = day_key_from_interval(data_bucket.get("interval") or "")
            if wk not in week_keys:
                continue
            week_index = next(i for i, bucket in enumerate(current_week_buckets) if bucket.key == wk)
            parsed = parse_metric_values(data_bucket)
            handle_count, _handle_minutes = parsed.get("tHandle", (0, None))
            if handle_count <= 0:
                continue
            series.counts[week_index] = handle_count
            for metric in TIME_METRICS:
                _count, minutes = parsed.get(metric, (0, None))
                series.time_minutes[metric][week_index] = minutes
    return series_map


def fetch_daily_asa(
    client,
    interval: str,
    group_by: list[str],
) -> dict[tuple[str, ...], list[float | None]]:
    body = {
        "interval": interval,
        "granularity": "P1D",
        "groupBy": group_by,
        "filter": {
            "type": "and",
            "predicates": [
                {"type": "dimension", "dimension": "direction", "operator": "matches", "value": "inbound"},
                {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "voice"},
            ],
        },
        "metrics": [ASA_METRIC],
    }
    tool_log("info", f"Querying daily ASA ({', '.join(group_by)})")
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)
    day_keys = {day.isoformat() for day in current_report_days}
    series_map: dict[tuple[str, ...], list[float | None]] = {}

    for result in resp.get("results") or []:
        group = result.get("group") or {}
        key_parts = [(group.get(dimension) or "").strip() for dimension in group_by]
        row_key = tuple(key_parts)
        asa_daily = [None] * len(current_report_days)
        for data_bucket in result.get("data") or []:
            day_key = day_key_from_interval(data_bucket.get("interval") or "")
            if day_key not in day_keys:
                continue
            day_index = next(
                index for index, day in enumerate(current_report_days) if day.isoformat() == day_key
            )
            parsed = parse_metric_values(data_bucket)
            _asa_count, asa_seconds = parsed.get(ASA_METRIC, (0, None))
            asa_daily[day_index] = asa_seconds
        if any(value is not None for value in asa_daily):
            series_map[row_key] = asa_daily
    return series_map


def merge_daily_asa(
    series_map: dict[Any, MetricSeries],
    asa_map: dict[tuple[str, ...], list[float | None]],
) -> None:
    for key, asa_daily in asa_map.items():
        lookup_key: Any = key[0] if len(key) == 1 else key
        series = series_map.get(lookup_key)
        if series is None:
            continue
        series.asa_seconds_daily = asa_daily


def load_queue_names(client) -> dict[str, str]:
    return {
        (q.get("id") or ""): (q.get("name") or q.get("id") or "")
        for q in get_all_queues(client)
    }


def load_user_names(client, user_ids: set[str]) -> dict[str, str]:
    names: dict[str, str] = {}
    for user in client.paged_get("/api/v2/users", params={"state": "any"}):
        uid = (user.get("id") or "").strip()
        if uid in user_ids:
            names[uid] = (user.get("name") or uid).strip()
    for uid in sorted(user_ids - set(names)):
        try:
            user = client.request("GET", f"/api/v2/users/{uid}")
            names[uid] = (user.get("name") or uid).strip()
        except Exception:
            names[uid] = uid
    return names


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "item"


def include_queue(queue_name: str) -> bool:
    normalized = (queue_name or "").strip().upper()
    return not any(normalized.startswith(prefix) for prefix in EXCLUDED_QUEUE_PREFIXES)


def fmt_aht(minutes: float | None) -> str:
    if minutes is None:
        return "—"
    whole = int(minutes)
    secs = int(round((minutes - whole) * 60))
    if secs == 60:
        whole += 1
        secs = 0
    return f"{whole}:{secs:02d}"


def fmt_asa(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    return f"{seconds:.1f}s"


def series_payload(series: MetricSeries) -> dict[str, Any]:
    return {
        "counts": series.counts,
        "handle_minutes": series.time_minutes["tHandle"],
        "talk_minutes": series.time_minutes["tTalk"],
        "hold_minutes": series.time_minutes["tHeld"],
        "acw_minutes": series.time_minutes["tAcw"],
        "handle_minutes_daily": expand_weekly_to_daily(
            series.time_minutes["tHandle"], current_report_days, current_week_buckets
        ),
        "talk_minutes_daily": expand_weekly_to_daily(
            series.time_minutes["tTalk"], current_report_days, current_week_buckets
        ),
        "hold_minutes_daily": expand_weekly_to_daily(
            series.time_minutes["tHeld"], current_report_days, current_week_buckets
        ),
        "acw_minutes_daily": expand_weekly_to_daily(
            series.time_minutes["tAcw"], current_report_days, current_week_buckets
        ),
        "asa_seconds_daily": series.asa_seconds_daily,
        "latest_asa": latest_week_asa_avg(series.asa_seconds_daily, current_week_buckets),
        "total_handles": series.total_handles(),
        "latest_handle": series.latest_handle(),
    }


def metric_defs_payload() -> dict[str, Any]:
    return {
        key: {
            "label": value["label"],
            "baseline_minutes": value["baseline_minutes"],
            "baseline_label": value["baseline_label"],
            "color": value["color"],
        }
        for key, value in METRIC_DEFS.items()
    }


def build_report_payload(
    queue_series: dict[str, MetricSeries],
    agent_series: dict[tuple[str, str], MetricSeries],
    queue_names: dict[str, str],
    user_names: dict[str, str],
) -> dict[str, Any]:
    week_labels = [bucket.label for bucket in current_week_buckets]
    queues: list[dict[str, Any]] = []

    for queue_id, series in queue_series.items():
        if series.total_handles() <= 0:
            continue
        queue_name = queue_names.get(queue_id, queue_id or "(No Queue)")
        if not include_queue(queue_name):
            continue
        agents: list[dict[str, Any]] = []
        for (qid, user_id), agent_data in agent_series.items():
            if qid != queue_id or agent_data.total_handles() < MIN_AGENT_HANDLES:
                continue
            agent_row = {
                "id": slugify(f"{queue_id}-{user_id}"),
                "name": user_names.get(user_id, user_id),
                **series_payload(agent_data),
            }
            agents.append(agent_row)
        agents.sort(key=lambda row: (-(row["latest_handle"] or 0), row["name"].lower()))
        queue_row = {
            "id": slugify(queue_name),
            "queue_id": queue_id,
            "name": queue_name,
            **series_payload(series),
            "agents": agents,
        }
        queues.append(queue_row)

    queues.sort(key=lambda row: (-(row["latest_handle"] or 0), row["name"].lower()))
    day_labels = [day.strftime("%a %b %d") for day in current_report_days]
    day_ticks = [
        {
            "label": day.strftime("%a %b %d"),
            "show": day.weekday() == 0 or day == current_report_days[-1],
        }
        for day in current_report_days
    ]
    return {
        "week_labels": week_labels,
        "day_labels": day_labels,
        "day_ticks": day_ticks,
        "metric_defs": metric_defs_payload(),
        "asa_color": ASA_BAR_COLOR,
        "min_agent_handles": MIN_AGENT_HANDLES,
        "queues": queues,
    }


def render_html(payload: dict[str, Any], meta: dict[str, str]) -> str:
    data_json = json.dumps(payload)
    meta_rows = "".join(
        f"<tr><th>{html.escape(key)}</th><td>{html.escape(value)}</td></tr>"
        for key, value in meta.items()
    )
    latest_label = html.escape(payload["week_labels"][-1])
    summary_rows: list[str] = []
    agent_rows: list[str] = []
    for queue in payload["queues"]:
        handle_cells = "".join(f"<td>{fmt_aht(value)}</td>" for value in queue["handle_minutes"])
        count_cells = "".join(f"<td>{count}</td>" for count in queue["counts"])
        summary_rows.append(
            "<tr>"
            f"<td>{html.escape(queue['name'])}</td>"
            f"{handle_cells}"
            f"<td>{fmt_aht(queue['talk_minutes'][-1])}</td>"
            f"<td>{fmt_aht(queue['hold_minutes'][-1])}</td>"
            f"<td>{fmt_aht(queue['acw_minutes'][-1])}</td>"
            f"<td>{fmt_asa(queue['latest_asa'])}</td>"
            f"{count_cells}"
            f"<td>{queue['counts'][-1]}</td>"
            f"<td>{len(queue['agents'])}</td>"
            "</tr>"
        )
        for agent in queue["agents"]:
            handle_cells = "".join(f"<td>{fmt_aht(value)}</td>" for value in agent["handle_minutes"])
            count_cells = "".join(f"<td>{count}</td>" for count in agent["counts"])
            agent_rows.append(
                "<tr>"
                f"<td>{html.escape(queue['name'])}</td>"
                f"<td>{html.escape(agent['name'])}</td>"
                f"{handle_cells}"
                f"<td>{fmt_aht(agent['talk_minutes'][-1])}</td>"
                f"<td>{fmt_aht(agent['hold_minutes'][-1])}</td>"
                f"<td>{fmt_aht(agent['acw_minutes'][-1])}</td>"
                f"<td>{fmt_asa(agent['latest_asa'])}</td>"
                f"{count_cells}"
                f"<td>{agent['total_handles']}</td>"
                "</tr>"
            )

    queue_sections: list[str] = []
    for queue in payload["queues"]:
        agent_blocks: list[str] = []
        for agent in queue["agents"]:
            agent_blocks.append(
                f"""
                <details class="agent-block">
                  <summary>{html.escape(agent['name'])} — latest handle {fmt_aht(agent['latest_handle'])} ({agent['total_handles']} handled)</summary>
                  <div class="chart-stack">
                    <div class="chart-wrap chart-lines">
                      <canvas class="lazy-chart" data-chart-id="{html.escape(agent['id'])}" data-role="lines"></canvas>
                    </div>
                    <div class="chart-wrap chart-asa-daily">
                      <canvas class="lazy-chart" data-chart-id="{html.escape(agent['id'])}" data-role="asa"></canvas>
                    </div>
                  </div>
                </details>
                """
            )
        agents_html = "\n".join(agent_blocks) if agent_blocks else '<p class="muted">No agents met the minimum handle threshold for this queue.</p>'
        queue_sections.append(
            f"""
            <section class="queue-segment" data-queue-id="{html.escape(queue['id'])}" hidden>
              <div class="queue-segment-header">
                <h3>{html.escape(queue['name'])}</h3>
                <p class="queue-meta">Latest handle {fmt_aht(queue['latest_handle'])} · {queue['total_handles']} handled ({WEEK_COUNT} wk) · {len(queue['agents'])} agents listed</p>
              </div>
              <div class="chart-stack">
                <div class="chart-wrap chart-lines">
                  <canvas class="lazy-chart" data-chart-id="{html.escape(queue['id'])}" data-role="lines"></canvas>
                </div>
                <div class="chart-wrap chart-asa-daily">
                  <canvas class="lazy-chart" data-chart-id="{html.escape(queue['id'])}" data-role="asa"></canvas>
                </div>
              </div>
              <div class="agent-list">
                <h4>Agents in queue</h4>
                {agents_html}
              </div>
            </section>
            """
        )

    queue_options = "".join(
        f'<option value="{html.escape(queue["id"])}">{html.escape(queue["name"])}</option>'
        for queue in sorted(payload["queues"], key=lambda row: row["name"].lower())
    )
    week_handle_headers = "".join(
        f"<th>{html.escape(label)} Handle</th>" for label in payload["week_labels"]
    )
    week_count_headers = "".join(
        f"<th>{html.escape(label)} Handled</th>" for label in payload["week_labels"]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AHT Component Trend Report</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg: #f7f3ed;
      --card: #fffdf9;
      --ink: #2d2418;
      --muted: #6d5f4d;
      --line: #d9cdbb;
      --accent: #2f5d9a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 15px/1.5 Georgia, "Times New Roman", serif;
      color: var(--ink);
      background: linear-gradient(180deg, #efe6d7 0%, var(--bg) 180px);
    }}
    header, main {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 24px;
    }}
    h1, h2, h3 {{ font-family: "Segoe UI", Arial, sans-serif; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 18px 20px;
      margin-bottom: 20px;
      box-shadow: 0 8px 24px rgba(45, 36, 24, 0.06);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-family: "Segoe UI", Arial, sans-serif;
      font-size: 14px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 8px 10px;
      text-align: left;
    }}
    th {{ background: #e8eef7; }}
    a {{ color: var(--accent); }}
    .muted {{ color: var(--muted); }}
    .legend-note {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px 18px;
      margin-top: 8px;
      font-family: "Segoe UI", Arial, sans-serif;
      font-size: 13px;
    }}
    .legend-item {{ display: inline-flex; align-items: center; gap: 6px; }}
    .swatch {{
      width: 14px;
      height: 14px;
      border-radius: 3px;
      display: inline-block;
    }}
    .queue-picker {{
      margin: 18px 0 8px;
      font-family: "Segoe UI", Arial, sans-serif;
    }}
    .queue-picker label {{
      display: block;
      font-weight: 600;
      margin-bottom: 8px;
    }}
    .queue-picker select {{
      width: 100%;
      max-width: 720px;
      padding: 10px 12px;
      font-size: 15px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
    }}
    .queue-segment {{
      margin-top: 18px;
      padding-top: 8px;
      border-top: 1px solid var(--line);
    }}
    .queue-segment-header h3 {{
      margin: 0 0 6px;
      font-size: 1.2rem;
    }}
    .queue-segment-header .queue-meta {{
      margin: 0 0 12px;
      color: var(--muted);
      font-family: "Segoe UI", Arial, sans-serif;
      font-size: 14px;
    }}
    .agent-list h4 {{
      margin: 18px 0 10px;
      font-family: "Segoe UI", Arial, sans-serif;
    }}
    .queue-empty {{
      margin: 12px 0 0;
      color: var(--muted);
      font-family: "Segoe UI", Arial, sans-serif;
    }}
    .chart-wrap {{
      position: relative;
      height: 360px;
      margin: 0;
    }}
    .chart-stack {{
      margin: 14px 0 8px;
      border: 1px solid var(--line);
      border-radius: 10px;
      overflow: hidden;
      background: #fff;
    }}
    .chart-lines {{ height: 340px; }}
    .chart-asa-daily {{
      height: 130px;
      border-top: 1px solid var(--line);
      background: #faf7ff;
    }}
    .agent-block {{
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px 12px;
      margin: 10px 0;
      background: #fff;
    }}
    .agent-block summary {{
      cursor: pointer;
      font-family: "Segoe UI", Arial, sans-serif;
      font-weight: 600;
    }}
    .meta-table th {{ width: 220px; }}
  </style>
</head>
<body>
  <header>
    <h1>Inbound Voice Handle Time — {WEEK_COUNT}-Week Component Trend</h1>
    <p class="muted">Select a queue below. Handle/Talk/Hold/ACW are weekly averages (flat segments per week). Daily ASA underneath is the operational view — spikes on a given weekday should match what you remember happening that day.</p>
    <div class="legend-note">
      <span class="legend-item"><span class="swatch" style="background:#2f5d9a"></span>Handle (4:00)</span>
      <span class="legend-item"><span class="swatch" style="background:#2a9d6f"></span>Talk (3:00)</span>
      <span class="legend-item"><span class="swatch" style="background:#e9c46a"></span>Hold (1:00)</span>
      <span class="legend-item"><span class="swatch" style="background:#e76f51"></span>ACW (1:00)</span>
      <span class="legend-item"><span class="swatch" style="background:rgba(118,84,154,0.55)"></span>Daily ASA bars</span>
    </div>
  </header>
  <main>
    <section class="card">
      <h2>Queue Trends</h2>
      <div class="queue-picker">
        <label for="queue-select">Select queue</label>
        <select id="queue-select">
          <option value="">Choose a queue...</option>
          {queue_options}
        </select>
      </div>
      <p id="queue-empty" class="queue-empty">No queue selected. Choose one from the dropdown to load its charts.</p>
      <div id="queue-segments">
        {''.join(queue_sections)}
      </div>
    </section>
    <section class="card">
      <h2>Raw Data — Queues</h2>
      <table>
        <thead>
          <tr>
            <th>Queue</th>
            {week_handle_headers}
            <th>{latest_label} Talk</th>
            <th>{latest_label} Hold</th>
            <th>{latest_label} ACW</th>
            <th>{latest_label} ASA (avg)</th>
            {week_count_headers}
            <th>Latest Week Handled</th>
            <th>Agents Listed</th>
          </tr>
        </thead>
        <tbody>
          {''.join(summary_rows)}
        </tbody>
      </table>
    </section>
    <section class="card">
      <h2>Raw Data — Agents</h2>
      <p class="muted">Agents with {MIN_AGENT_HANDLES}+ handled in the queue over the {WEEK_COUNT}-week window.</p>
      <table>
        <thead>
          <tr>
            <th>Queue</th>
            <th>Agent</th>
            {week_handle_headers}
            <th>{latest_label} Talk</th>
            <th>{latest_label} Hold</th>
            <th>{latest_label} ACW</th>
            <th>{latest_label} ASA (avg)</th>
            {week_count_headers}
            <th>Total Handled</th>
          </tr>
        </thead>
        <tbody>
          {''.join(agent_rows)}
        </tbody>
      </table>
    </section>
    <section class="card">
      <h2>About</h2>
      <table class="meta-table">{meta_rows}</table>
    </section>
  </main>
  <script>
    const REPORT = {data_json};
    const chartState = new Map();

    const baselinePlugin = {{
      id: "baselinePlugin",
      afterDraw(chart, args, options) {{
        const baselines = (options && options.baselines) || [];
        const yScale = chart.scales.y;
        const area = chart.chartArea;
        if (!area || !yScale) return;
        const ctx = chart.ctx;
        baselines.forEach((baseline, index) => {{
          const value = Number(baseline.value);
          if (!Number.isFinite(value)) return;
          const y = yScale.getPixelForValue(value);
          if (!Number.isFinite(y)) return;
          ctx.save();
          ctx.beginPath();
          ctx.setLineDash([6, 6]);
          ctx.lineWidth = 2;
          ctx.strokeStyle = baseline.color || "rgba(90, 70, 50, 0.75)";
          ctx.moveTo(area.left, y);
          ctx.lineTo(area.right, y);
          ctx.stroke();
          const label = baseline.label || "Baseline";
          ctx.setLineDash([]);
          ctx.font = "11px Arial";
          const pad = 5;
          const width = ctx.measureText(label).width + pad * 2;
          const height = 18;
          const x = area.left + 8;
          const boxY = Math.max(area.top + 4 + (index * 22), y - height - 4);
          ctx.fillStyle = "rgba(247,243,237,0.94)";
          ctx.strokeStyle = baseline.color || "rgba(90,70,50,0.75)";
          ctx.fillRect(x, boxY, width, height);
          ctx.strokeRect(x, boxY, width, height);
          ctx.fillStyle = "rgba(60,45,30,0.95)";
          ctx.fillText(label, x + pad, boxY + 13);
          ctx.restore();
        }});
      }}
    }};
    Chart.register(baselinePlugin);

    function metricValues(row, key) {{
      return row[key].map((value) => (value === null || value === undefined ? null : Number(value)));
    }}

    function dayTickLabels() {{
      return REPORT.day_labels.map((label, index) => (
        REPORT.day_ticks[index] && REPORT.day_ticks[index].show ? label : ""
      ));
    }}

    function buildLineDatasets(row) {{
      const defs = REPORT.metric_defs;
      return [
        {{ key: "handle_minutes_daily", metric: "tHandle" }},
        {{ key: "talk_minutes_daily", metric: "tTalk" }},
        {{ key: "hold_minutes_daily", metric: "tHeld" }},
        {{ key: "acw_minutes_daily", metric: "tAcw" }},
      ].map((entry) => {{
        const defn = defs[entry.metric];
        return {{
          type: "line",
          label: defn.label,
          data: metricValues(row, entry.key),
          borderColor: defn.color,
          backgroundColor: defn.color,
          tension: 0,
          spanGaps: false,
          pointRadius: 0,
          pointHoverRadius: 4,
          borderWidth: 2,
        }};
      }});
    }}

    function baselineOptions() {{
      return Object.values(REPORT.metric_defs).map((defn) => ({{
        value: defn.baseline_minutes,
        color: defn.color,
        label: defn.label + " " + defn.baseline_label,
      }}));
    }}

    function renderLinesChart(canvas, row) {{
      return new Chart(canvas, {{
        data: {{
          labels: REPORT.day_labels,
          datasets: buildLineDatasets(row),
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          interaction: {{ mode: "index", intersect: false }},
          scales: {{
            x: {{
              ticks: {{
                maxRotation: 0,
                autoSkip: false,
                callback: function(value, index) {{
                  return dayTickLabels()[index] || "";
                }},
              }},
              grid: {{ display: false }},
            }},
            y: {{
              title: {{ display: true, text: "Minutes (weekly avg)" }},
              beginAtZero: true,
            }},
          }},
          plugins: {{
            baselinePlugin: {{ baselines: baselineOptions() }},
            legend: {{ display: true }},
          }},
        }},
      }});
    }}

    function renderAsaChart(canvas, row) {{
      return new Chart(canvas, {{
        data: {{
          labels: REPORT.day_labels,
          datasets: [{{
            type: "bar",
            label: "ASA",
            data: metricValues(row, "asa_seconds_daily"),
            backgroundColor: REPORT.asa_color,
            borderColor: "rgba(118, 84, 154, 0.85)",
            borderWidth: 1,
            maxBarThickness: 6,
            barPercentage: 1.0,
            categoryPercentage: 0.9,
          }}],
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          interaction: {{ mode: "index", intersect: false }},
          scales: {{
            x: {{
              ticks: {{
                maxRotation: 55,
                minRotation: 55,
                autoSkip: false,
              }},
            }},
            y: {{
              title: {{ display: true, text: "ASA (seconds)" }},
              beginAtZero: true,
            }},
          }},
          plugins: {{
            legend: {{ display: false }},
          }},
        }},
      }});
    }}

    function lookupSeries(chartId) {{
      const queue = REPORT.queues.find((item) => item.id === chartId);
      if (queue) return queue;
      for (const item of REPORT.queues) {{
        const agent = item.agents.find((row) => row.id === chartId);
        if (agent) return agent;
      }}
      return null;
    }}

    function ensureChart(canvas) {{
      const chartId = canvas.dataset.chartId;
      const role = canvas.dataset.role;
      const stateKey = chartId + ":" + role;
      if (!chartId || chartState.has(stateKey)) return;
      const row = lookupSeries(chartId);
      if (!row) return;
      const chart = role === "asa" ? renderAsaChart(canvas, row) : renderLinesChart(canvas, row);
      chartState.set(stateKey, chart);
    }}

    function wireLazyCharts(root) {{
      const canvases = root.querySelectorAll("canvas.lazy-chart");
      canvases.forEach((canvas) => {{
        if (canvas.dataset.wired === "1") return;
        canvas.dataset.wired = "1";
        const details = canvas.closest("details");
        if (!details) {{
          ensureChart(canvas);
          return;
        }}
        const openHandler = () => {{
          if (details.open) {{
            details.querySelectorAll("canvas.lazy-chart").forEach((item) => ensureChart(item));
          }}
        }};
        details.addEventListener("toggle", openHandler);
        if (details.open) openHandler();
      }});
    }}

    function activateQueueSegment(queueId) {{
      const emptyState = document.getElementById("queue-empty");
      document.querySelectorAll(".queue-segment").forEach((segment) => {{
        const active = Boolean(queueId) && segment.dataset.queueId === queueId;
        segment.hidden = !active;
        if (active) {{
          wireLazyCharts(segment);
          segment.querySelectorAll('canvas[data-role="lines"], canvas[data-role="asa"]').forEach((canvas) => {{
            ensureChart(canvas);
          }});
        }}
      }});
      if (emptyState) {{
        emptyState.hidden = Boolean(queueId);
      }}
    }}

    const queueSelect = document.getElementById("queue-select");
    if (queueSelect) {{
      queueSelect.addEventListener("change", () => {{
        activateQueueSegment(queueSelect.value);
      }});
    }}
  </script>
</body>
</html>
"""


def write_report_meta(path: Path, payload: dict[str, Any]) -> Path:
    meta_path = path.parent / f"{path.stem}_meta.json"
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return meta_path


current_week_buckets: list[WeekBucket] = []
current_report_days: list[date] = []


def main() -> int:
    global current_week_buckets, current_report_days
    setup_path()
    parser = argparse.ArgumentParser(description="Biweekly 4-week inbound voice AHT HTML trend report")
    parser.add_argument("--output", default=None, help="Output .html path")
    add_env_arg(parser)
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    current_week_buckets, start_label, end_label = calendar_weeks()
    current_report_days = reporting_days(current_week_buckets)
    interval = range_api_interval(current_week_buckets[0].start, current_week_buckets[-1].end)
    tool_log("info", f"Window: {start_label} through {end_label} ({WEEK_COUNT} weeks, {len(current_report_days)} days)")

    queue_names = load_queue_names(client)
    queue_series_raw = fetch_weekly_metrics(client, interval, ["queueId"])
    agent_series_raw = fetch_weekly_metrics(client, interval, ["queueId", "userId"])
    queue_asa_daily = fetch_daily_asa(client, interval, ["queueId"])
    agent_asa_daily = fetch_daily_asa(client, interval, ["queueId", "userId"])

    queue_series = {qid: series for (qid,), series in queue_series_raw.items() if qid}
    agent_series = {
        (qid, uid): series
        for (qid, uid), series in agent_series_raw.items()
        if qid and uid
    }
    merge_daily_asa(queue_series, queue_asa_daily)
    merge_daily_asa(agent_series, agent_asa_daily)
    user_ids = {uid for (_qid, uid) in agent_series}
    tool_log("info", f"Resolving {len(user_ids)} agent names")
    user_names = load_user_names(client, user_ids)

    payload = build_report_payload(queue_series, agent_series, queue_names, user_names)
    baseline_summary = ", ".join(
        f"{METRIC_DEFS[key]['label']} {METRIC_DEFS[key]['baseline_label']}" for key in TIME_METRICS
    )
    meta = {
        "Generated At (UTC)": datetime.now(timezone.utc).isoformat(),
        "Window Start": start_label,
        "Window End": end_label,
        "Weeks": " | ".join(bucket.label for bucket in current_week_buckets),
        "Handle Components": "tHandle, tTalk, tHeld, tAcw (weekly inbound voice averages, minutes)",
        "ASA Strip": "Daily tAnswered average (seconds) — one bar per day, labeled with weekday",
        "Baselines": baseline_summary,
        "Agent Minimum Handles": str(MIN_AGENT_HANDLES),
        "Excluded Queues": ", ".join(f"names starting with {prefix}" for prefix in EXCLUDED_QUEUE_PREFIXES),
        "Queues Listed": str(len(payload["queues"])),
        "Day Boundary": f"UTC-{TZ_OFFSET_HOURS:02d} (US Eastern DST)",
        "Analytics Interval": interval,
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else output_path(f"aht_trend_biweekly_{stamp}.html")
    out.write_text(render_html(payload, meta), encoding="utf-8")
    write_report_meta(
        out,
        {
            "report_path": str(out),
            "window_start": start_label,
            "window_end": end_label,
            "queues_listed": len(payload["queues"]),
            "baselines": baseline_summary,
            "week_labels": payload["week_labels"],
        },
    )
    tool_log("ok", f"Wrote {out}")
    if payload["queues"]:
        top = payload["queues"][0]
        tool_log("info", f"Highest latest handle queue: {top['name']} ({fmt_aht(top['latest_handle'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
