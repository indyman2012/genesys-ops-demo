"""Generate Queue Performance PDF reports (overview or single-queue drill-down)."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.queue_performance import fetch_queue_detail, fetch_queue_performance, parse_filters
from core.cli import create_client, setup_path, tool_log
from core.config import env_first
from core.paths import OUTPUTS_DIR, ensure_runtime_dirs

BRAND_NAVY = colors.HexColor("#1f4b99")
BRAND_GREEN = colors.HexColor("#1f6b3a")
BRAND_RED = colors.HexColor("#9b1c1c")
LIGHT_GREEN = colors.HexColor("#d9f2e1")
LIGHT_YELLOW = colors.HexColor("#fff1cc")
LIGHT_RED = colors.HexColor("#fde2e2")
LIGHT_GRAY = colors.HexColor("#eef2f5")

STATUS_FILL = {
    "green": LIGHT_GREEN,
    "metric-good": LIGHT_GREEN,
    "yellow": LIGHT_YELLOW,
    "metric-warning": LIGHT_YELLOW,
    "red": LIGHT_RED,
    "metric-bad": LIGHT_RED,
}


def export_dir() -> Path:
    raw = (env_first("QUEUE_PERFORMANCE_EXPORT_DIR", "QUEUE_PERFORMANCE_SHARED_FOLDER") or "").strip()
    if raw:
        return Path(raw)
    return OUTPUTS_DIR / "queue_performance_exports"


def _fmt_num(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_pct(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_sec(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        total = max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return "—"
    minutes, seconds = divmod(total, 60)
    return f"{minutes}:{seconds:02d}"


def _status_fill(status: str | None) -> colors.Color:
    return STATUS_FILL.get(status or "", LIGHT_GRAY)


def _filter_summary(filters: dict[str, Any]) -> str:
    parts = [
        f"{filters.get('start_date')} through {filters.get('end_date')}",
        f"Prefix: {filters.get('queue_prefix') or 'All'}",
    ]
    if filters.get("division"):
        parts.append(f"Division: {filters['division']}")
    return " · ".join(parts)


def _line_chart(
    labels: list[str],
    series: list[tuple[str, list[float | None], colors.Color]],
    *,
    width: float = 7.5 * inch,
    height: float = 2.2 * inch,
    y_label: str = "",
) -> Drawing | Spacer:
    plot_series: list[tuple[str, list[float | None], colors.Color]] = []
    for name, values, color in series:
        row = [float(v or 0) for v in values]
        if any(row):
            plot_series.append((name, row, color))
    if not plot_series or not labels:
        return Spacer(1, 0.15 * inch)

    plot = LinePlot()
    plot.x = 45
    plot.y = 20
    plot.height = height - 35
    plot.width = width - 60
    plot.data = [[(idx, value) for idx, value in enumerate(row)] for _name, row, _color in plot_series]
    plot.joinedLines = 1
    for idx, (_name, _values, color) in enumerate(plot_series):
        plot.lines[idx].strokeColor = color
        plot.lines[idx].strokeWidth = 1.5
        plot.lines[idx].symbol = makeMarker("Circle")
        plot.lines[idx].symbol.size = 3

    if len(labels) > 1:
        plot.xValueAxis.valueSteps = list(range(len(labels)))
    plot.xValueAxis.labels.boxAnchor = "n"
    plot.xValueAxis.labels.angle = 45
    plot.xValueAxis.labels.fontSize = 7
    plot.xValueAxis.labelTextFormat = lambda i: labels[int(i)] if 0 <= int(i) < len(labels) else ""
    plot.yValueAxis.labels.fontSize = 8
    if y_label:
        plot.yValueAxis.valueMax = max(max(row) for _n, row, _c in plot_series) * 1.15 or 1

    drawing = Drawing(width, height)
    drawing.add(plot)
    legend_x = 50
    for name, _values, color in plot_series:
        drawing.add(String(legend_x, height - 12, name, fontSize=8, fillColor=color))
        legend_x += max(70, len(name) * 5)
    return drawing


def _summary_table(summary: dict[str, Any], queue_count: int) -> Table:
    data = [
        ["Queues", "Offered", "Service level", "Abandon %", "ASA"],
        [
            _fmt_num(queue_count),
            _fmt_num(summary.get("offered")),
            _fmt_pct(summary.get("service_level_pct")),
            _fmt_pct(summary.get("abandon_pct")),
            _fmt_sec(summary.get("asa_sec")),
        ],
    ]
    table = Table(data, colWidths=[1.1 * inch, 1.1 * inch, 1.2 * inch, 1.1 * inch, 1.0 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND_NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white]),
            ]
        )
    )
    return table


def _scorecard_table(rows: list[dict[str, Any]], *, primary_key: str, secondary_key: str | None) -> Table:
    header = [
        "Health",
        "Queue" if primary_key == "queue_name" else "Agent",
    ]
    if secondary_key:
        header.append("Division")
    header.extend(
        ["Offered", "Answered", "Aband", "Abnd %", "SL %", "ASA", "Talk", "ACW"]
    )
    data: list[list[str]] = [header]
    styles: list[tuple] = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]

    metric_cols = {
        "abandon_pct": len(header) - 6,
        "service_level_pct": len(header) - 5,
        "asa_sec": len(header) - 4,
        "avg_talk_sec": len(header) - 3,
        "avg_acw_sec": len(header) - 2,
    }

    for row_idx, row in enumerate(rows, start=1):
        statuses = row.get("statuses") or {}
        line = [
            (row.get("overall_status") or "green").upper(),
            str(row.get(primary_key) or ""),
        ]
        if secondary_key:
            line.append(str(row.get(secondary_key) or "—"))
        line.extend(
            [
                _fmt_num(row.get("offered")),
                _fmt_num(row.get("answered")),
                _fmt_num(row.get("abandoned")),
                _fmt_pct(row.get("abandon_pct")),
                _fmt_pct(row.get("service_level_pct")),
                _fmt_sec(row.get("asa_sec")),
                _fmt_sec(row.get("avg_talk_sec")),
                _fmt_sec(row.get("avg_acw_sec")),
            ]
        )
        data.append(line)
        styles.append(("BACKGROUND", (0, row_idx), (0, row_idx), _status_fill(row.get("overall_status"))))
        for metric, col_idx in metric_cols.items():
            styles.append(("BACKGROUND", (col_idx, row_idx), (col_idx, row_idx), _status_fill(statuses.get(metric))))

    if secondary_key:
        col_widths = [0.55 * inch, 1.55 * inch, 0.95 * inch] + [0.62 * inch] * 8
    else:
        col_widths = [0.55 * inch, 1.8 * inch] + [0.62 * inch] * 8
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle(styles))
    return table


def build_pdf(
    payload: dict[str, Any],
    *,
    title: str,
    subtitle: str,
    scorecard_rows: list[dict[str, Any]],
    scorecard_primary: str,
    scorecard_secondary: str | None,
    scorecard_heading: str,
) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
        topMargin=0.45 * inch,
        bottomMargin=0.45 * inch,
        title=title,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Heading1"],
        fontSize=18,
        textColor=BRAND_NAVY,
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "ReportSubtitle",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.grey,
        spaceAfter=10,
    )
    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontSize=12,
        textColor=BRAND_NAVY,
        spaceBefore=8,
        spaceAfter=6,
    )

    story: list[Any] = []
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story.append(Paragraph(title, title_style))
    story.append(Paragraph(f"{subtitle}<br/>Generated {generated}", subtitle_style))
    story.append(_summary_table(payload.get("summary") or {}, int(payload.get("queue_count") or 0)))
    story.append(Spacer(1, 0.15 * inch))

    trend = payload.get("trend") or []
    labels = [str(row.get("date") or "")[-5:] for row in trend]
    story.append(Paragraph("Volume trend", section_style))
    story.append(
        _line_chart(
            labels,
            [
                ("Offered", [row.get("offered") for row in trend], BRAND_NAVY),
                ("Answered", [row.get("answered") for row in trend], BRAND_GREEN),
                ("Abandoned", [row.get("abandoned") for row in trend], BRAND_RED),
            ],
        )
    )
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph("Performance trend", section_style))
    story.append(
        _line_chart(
            labels,
            [
                ("Service level %", [row.get("service_level_pct") for row in trend], BRAND_GREEN),
                ("Abandon %", [row.get("abandon_pct") for row in trend], BRAND_RED),
                ("ASA (sec)", [row.get("asa_sec") for row in trend], BRAND_NAVY),
            ],
        )
    )

    if scorecard_rows:
        story.append(PageBreak())
        story.append(Paragraph(scorecard_heading, section_style))
        story.append(
            _scorecard_table(
                scorecard_rows,
                primary_key=scorecard_primary,
                secondary_key=scorecard_secondary,
            )
        )

    doc.build(story)
    return buffer.getvalue()


def generate_overview_pdf(client, filters: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    payload = fetch_queue_performance(client, filters)
    subtitle = _filter_summary(payload.get("filters") or filters)
    pdf_bytes = build_pdf(
        payload,
        title="Queue Performance — Overview",
        subtitle=subtitle,
        scorecard_rows=payload.get("queues") or [],
        scorecard_primary="queue_name",
        scorecard_secondary="division_name",
        scorecard_heading="Queue scorecard",
    )
    return pdf_bytes, payload


def generate_detail_pdf(client, queue_id: str, filters: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    payload = fetch_queue_detail(client, queue_id, filters)
    queue = payload.get("queue") or {}
    subtitle = _filter_summary(payload.get("filters") or filters)
    queue_name = queue.get("queue_name") or queue_id
    pdf_bytes = build_pdf(
        {
            **payload,
            "summary": queue,
            "queue_count": payload.get("agent_count") or 0,
        },
        title=f"Queue Performance — {queue_name}",
        subtitle=subtitle,
        scorecard_rows=payload.get("agents") or [],
        scorecard_primary="user_name",
        scorecard_secondary=None,
        scorecard_heading="Agent scorecard",
    )
    return pdf_bytes, payload


def pdf_filename(
    filters: dict[str, Any],
    *,
    queue_name: str | None = None,
    stamp: str | None = None,
) -> str:
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    start = filters.get("start_date", "start")
    end = filters.get("end_date", "end")
    if queue_name:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in queue_name)[:40]
        return f"queue_performance_{safe}_{start}_{end}_{stamp}.pdf"
    return f"queue_performance_{start}_{end}_{stamp}.pdf"


def write_pdf(path: Path, pdf_bytes: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes)
    return path


def generate_and_save(
    client,
    filters: dict[str, Any],
    output_path: Path,
    *,
    queue_id: str | None = None,
) -> Path:
    if queue_id:
        pdf_bytes, payload = generate_detail_pdf(client, queue_id, filters)
        queue_name = (payload.get("queue") or {}).get("queue_name")
        if output_path.suffix.lower() != ".pdf":
            output_path = output_path / pdf_filename(filters, queue_name=queue_name)
    else:
        pdf_bytes, _payload = generate_overview_pdf(client, filters)
        if output_path.suffix.lower() != ".pdf":
            output_path = output_path / pdf_filename(filters)
    write_pdf(output_path, pdf_bytes)
    tool_log("info", f"Wrote Queue Performance PDF: {output_path}")
    return output_path


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Generate Queue Performance PDF report")
    parser.add_argument("--days", type=int, default=7, help="Rolling window when start/end omitted")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--division", default="")
    parser.add_argument("--queue-prefix", default="Patient Services")
    parser.add_argument("--queue-id", help="Optional queue drill-down")
    parser.add_argument(
        "--output",
        help="Output PDF path or directory (default: outputs/queue_performance_exports/)",
    )
    parser.add_argument(
        "--shared-folder",
        help="Also copy to this folder (overrides QUEUE_PERFORMANCE_EXPORT_DIR env)",
    )
    args = parser.parse_args()

    filter_args: dict[str, Any] = {
        "queue_prefix": args.queue_prefix,
        "division": args.division,
    }
    if args.start_date and args.end_date:
        filter_args["start_date"] = args.start_date
        filter_args["end_date"] = args.end_date
    else:
        filter_args["days"] = str(args.days)

    filters = parse_filters(filter_args)
    ensure_runtime_dirs()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = export_dir() / pdf_filename(filters, stamp=stamp)

    client = create_client(log=tool_log)
    saved = generate_and_save(client, filters, output_path, queue_id=args.queue_id)

    shared = Path(args.shared_folder) if args.shared_folder else export_dir()
    if shared.resolve() != saved.parent.resolve():
        shared_copy = shared / saved.name
        write_pdf(shared_copy, saved.read_bytes())
        tool_log("info", f"Copied Queue Performance PDF to shared folder: {shared_copy}")

    print(saved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
