"""Excel export for Queue Performance dashboard data."""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
BOLD = Font(bold=True)

SCORECARD_HEADERS = [
    "Health",
    "Name",
    "Division",
    "Offered",
    "Answered",
    "Abandoned",
    "Abandon %",
    "Service Level %",
    "ASA",
    "Talk",
    "Hold",
    "ACW",
    "Handle",
]

AGENT_HEADERS = [
    "Health",
    "Agent",
    "Offered",
    "Answered",
    "Abandoned",
    "Abandon %",
    "Service Level %",
    "ASA",
    "Talk",
    "Hold",
    "ACW",
    "Handle",
]

TREND_HEADERS = [
    "Date",
    "Offered",
    "Answered",
    "Abandoned",
    "Abandon %",
    "Service Level %",
    "ASA",
    "Avg Talk",
    "Avg Hold",
    "Avg ACW",
    "Avg Handle",
]


def sec_to_mmss(value: Any) -> str:
    if value is None or value == "":
        return ""
    try:
        total = max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return str(value)
    minutes, seconds = divmod(total, 60)
    return f"{minutes}:{seconds:02d}"


def pct_value(value: Any) -> Any:
    if value is None or value == "":
        return ""
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return value


def excel_filename(filters: dict[str, Any], *, queue_name: str | None = None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    start = filters.get("start_date", "start")
    end = filters.get("end_date", "end")
    if queue_name:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in queue_name)[:40]
        return f"queue_performance_{safe}_{start}_{end}_{stamp}.xlsx"
    return f"queue_performance_{start}_{end}_{stamp}.xlsx"


def _write_header_row(ws, headers: list[str]) -> None:
    ws.append(headers)
    for cell in ws[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL


def _autosize_columns(ws) -> None:
    for column_cells in ws.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 10), 48)
    ws.freeze_panes = "A2"


def _scorecard_row(row: dict[str, Any], *, include_division: bool) -> list[Any]:
    line = [
        (row.get("overall_status") or "").upper(),
        row.get("queue_name") or row.get("user_name") or "",
    ]
    if include_division:
        line.append(row.get("division_name") or "")
    line.extend(
        [
            row.get("offered"),
            row.get("answered"),
            row.get("abandoned"),
            pct_value(row.get("abandon_pct")),
            pct_value(row.get("service_level_pct")),
            sec_to_mmss(row.get("asa_sec")),
            sec_to_mmss(row.get("avg_talk_sec")),
            sec_to_mmss(row.get("avg_hold_sec")),
            sec_to_mmss(row.get("avg_acw_sec")),
            sec_to_mmss(row.get("avg_handle_sec")),
        ]
    )
    return line


def _trend_row(row: dict[str, Any]) -> list[Any]:
    return [
        row.get("date"),
        row.get("offered"),
        row.get("answered"),
        row.get("abandoned"),
        pct_value(row.get("abandon_pct")),
        pct_value(row.get("service_level_pct")),
        sec_to_mmss(row.get("asa_sec")),
        sec_to_mmss(row.get("avg_talk_sec")),
        sec_to_mmss(row.get("avg_hold_sec")),
        sec_to_mmss(row.get("avg_acw_sec")),
        sec_to_mmss(row.get("avg_handle_sec")),
    ]


def _about_rows(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    filters = payload.get("filters") or {}
    summary = payload.get("summary") or payload.get("queue") or {}
    rows = [
        ("Generated At (UTC)", datetime.now(timezone.utc).isoformat()),
        ("Scorecard Window Start", filters.get("start_date")),
        ("Scorecard Window End", filters.get("end_date")),
        ("Queue Prefix", filters.get("queue_prefix")),
        ("Division", filters.get("division") or "All"),
        ("Trend Chart Days", payload.get("trend_chart_days") or 90),
        ("Offered", summary.get("offered")),
        ("Answered", summary.get("answered")),
        ("Abandoned", summary.get("abandoned")),
        ("Abandon %", pct_value(summary.get("abandon_pct"))),
        ("Service Level %", pct_value(summary.get("service_level_pct"))),
        ("ASA", sec_to_mmss(summary.get("asa_sec"))),
        ("Avg Talk", sec_to_mmss(summary.get("avg_talk_sec"))),
        ("Avg ACW", sec_to_mmss(summary.get("avg_acw_sec"))),
        ("Avg Handle", sec_to_mmss(summary.get("avg_handle_sec"))),
    ]
    if payload.get("queue_count") is not None:
        rows.append(("Queues on Scorecard", payload.get("queue_count")))
    if payload.get("agent_count") is not None:
        rows.append(("Agents on Scorecard", payload.get("agent_count")))
    return rows


def build_workbook_bytes(payload: dict[str, Any], *, detail: bool = False) -> bytes:
    wb = Workbook()
    ws_about = wb.active
    ws_about.title = "About"
    ws_about.append(["Field", "Value"])
    for key, value in _about_rows(payload):
        ws_about.append([key, value])
    _autosize_columns(ws_about)

    ws_trend = wb.create_sheet("Trend 90 Day")
    _write_header_row(ws_trend, TREND_HEADERS)
    for row in payload.get("trend_90d") or payload.get("trend") or []:
        ws_trend.append(_trend_row(row))
    _autosize_columns(ws_trend)

    filter_trend = payload.get("trend") or []
    if filter_trend and filter_trend != (payload.get("trend_90d") or []):
        ws_filter_trend = wb.create_sheet("Trend Scorecard Window")
        _write_header_row(ws_filter_trend, TREND_HEADERS)
        for row in filter_trend:
            ws_filter_trend.append(_trend_row(row))
        _autosize_columns(ws_filter_trend)

    if detail:
        ws_agents = wb.create_sheet("Agents")
        _write_header_row(ws_agents, AGENT_HEADERS)
        for row in payload.get("agents") or []:
            ws_agents.append(_scorecard_row(row, include_division=False))
        _autosize_columns(ws_agents)
        queue = payload.get("queue") or {}
        if queue.get("queue_name"):
            ws_queue = wb.create_sheet("Queue Summary")
            _write_header_row(ws_queue, SCORECARD_HEADERS)
            ws_queue.append(_scorecard_row(queue, include_division=True))
            _autosize_columns(ws_queue)
    else:
        ws_queues = wb.create_sheet("Queues")
        _write_header_row(ws_queues, SCORECARD_HEADERS)
        for row in payload.get("queues") or []:
            ws_queues.append(_scorecard_row(row, include_division=True))
        _autosize_columns(ws_queues)

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
