"""Demo supervisor reports — sample AHT trend HTML for agent workspace socialization."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.agent_workspace.demo_state import DEMO_QUEUES, MEMBER_SEEDS
from core.paths import PROJECT_ROOT
from tools.reporting import aht_trend_biweekly_report as aht


def _latest_live_report() -> Path | None:
    outputs = PROJECT_ROOT / "outputs"
    if not outputs.is_dir():
        return None
    matches = sorted(outputs.glob("aht_trend_biweekly_*.html"), reverse=True)
    return matches[0] if matches else None


def _demo_metric_series(
    handle_weeks: list[float],
    talk_weeks: list[float],
    hold_weeks: list[float],
    acw_weeks: list[float],
    counts: list[int],
    asa_daily: list[float],
) -> aht.MetricSeries:
    series = aht.MetricSeries()
    series.time_minutes["tHandle"] = handle_weeks
    series.time_minutes["tTalk"] = talk_weeks
    series.time_minutes["tHeld"] = hold_weeks
    series.time_minutes["tAcw"] = acw_weeks
    series.counts = counts
    series.asa_seconds_daily = asa_daily
    return series


def demo_aht_trend_html() -> str:
    buckets, start_label, end_label = aht.calendar_weeks()
    days = aht.reporting_days(buckets)
    aht.current_week_buckets = buckets
    aht.current_report_days = days

    asa_daily = [26 + (index % 7) * 2.5 for index in range(len(days))]
    queue_series: dict[str, aht.MetricSeries] = {}
    agent_series: dict[tuple[str, str], aht.MetricSeries] = {}
    queue_names: dict[str, str] = {}

    demo_queues = DEMO_QUEUES[:3]
    queue_profiles = [
        ([4.12, 4.05, 3.98, 4.18], [2.95, 2.88, 2.82, 2.91], [0.42, 0.38, 0.35, 0.40], [0.75, 0.79, 0.81, 0.87]),
        ([3.88, 3.92, 4.01, 3.95], [2.72, 2.76, 2.80, 2.74], [0.28, 0.31, 0.29, 0.33], [0.88, 0.85, 0.92, 0.88]),
        ([4.35, 4.28, 4.22, 4.30], [3.05, 3.02, 2.98, 3.08], [0.55, 0.48, 0.52, 0.46], [0.75, 0.78, 0.72, 0.76]),
    ]
    count_profiles = [
        [142, 156, 149, 163],
        [118, 125, 131, 128],
        [96, 102, 99, 108],
    ]

    for queue_index, queue in enumerate(demo_queues):
        queue_id = queue["id"]
        queue_names[queue_id] = queue["name"]
        handle, talk, hold, acw = queue_profiles[queue_index]
        queue_series[queue_id] = _demo_metric_series(
            handle, talk, hold, acw, count_profiles[queue_index], asa_daily
        )
        for agent_index, (agent_name, _status) in enumerate(MEMBER_SEEDS[:4]):
            user_id = f"demo-user-{queue_index}-{agent_index}"
            shift = agent_index * 0.08
            agent_series[(queue_id, user_id)] = _demo_metric_series(
                [value + shift for value in handle],
                [value + shift * 0.6 for value in talk],
                hold,
                acw,
                [max(12, count - agent_index * 3) for count in count_profiles[queue_index]],
                [value + agent_index for value in asa_daily],
            )

    user_names = {
        f"demo-user-{queue_index}-{agent_index}": name
        for queue_index in range(len(demo_queues))
        for agent_index, (name, _status) in enumerate(MEMBER_SEEDS[:4])
    }

    payload = aht.build_report_payload(queue_series, agent_series, queue_names, user_names)
    baseline_summary = ", ".join(
        f"{aht.METRIC_DEFS[key]['label']} {aht.METRIC_DEFS[key]['baseline_label']}"
        for key in aht.TIME_METRICS
    )
    meta = {
        "Generated At (UTC)": datetime.now(timezone.utc).isoformat(),
        "Window Start": start_label,
        "Window End": end_label,
        "Weeks": " | ".join(bucket.label for bucket in buckets),
        "Source": "Demo sample data for supervisor report console preview",
        "Baselines": baseline_summary,
        "Queues Listed": str(len(payload["queues"])),
    }
    return aht.render_html(payload, meta)


def aht_trend_report_html(*, prefer_live: bool = True) -> str:
    if prefer_live:
        live = _latest_live_report()
        if live and live.is_file():
            return live.read_text(encoding="utf-8")
    return demo_aht_trend_html()
