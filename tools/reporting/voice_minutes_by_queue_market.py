#!/usr/bin/env python3
"""Voice handle minutes (tHandle) split by queue and derived market."""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log
from core.genesys_client import safe_str
from core.paths import PROJECT_ROOT
from tools.reporting.spam_short_abandon_report import TZ_OFFSET_HOURS
from tools.reporting.staffing_metrics import metric_stats
from tools.reporting.wrapup_code_report import queue_parts

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

DEFAULT_MARKETS_CSV = PROJECT_ROOT / "config" / "area_code_markets.csv"
HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
BOLD = Font(bold=True)
MINUTES_FMT = "#,##0.0"
COUNT_FMT = "#,##0"
PCT_FMT = "0.00%"

MARKET_MAPPING_NOTES = """
Market derivation (first match wins):
1. Patient Services - {MKT} - {Center}  or  Patient Services - {MKT} {Center}
2. Sales - {MKT} - {Center}
3. NCM_{MKT}_{Center}
   → MKT = first 3 chars of captured code (e.g. CLE, STL, OKC, PHL, MKE)
2. Name contains HQ / Headquarters → HQ
3. Scan queue name for known market codes from config/area_code_markets.csv
4. Otherwise → UNKNOWN

Outbound/direct calls with no queueId appear as "(no queue)" / market NO_QUEUE.
Queue-level totals may be lower than org totals when handle time is not queue-attributed.
""".strip()


@dataclass
class QueueTotals:
    inbound_ms: int = 0
    outbound_ms: int = 0
    inbound_connected: int = 0
    outbound_connected: int = 0

    @property
    def total_ms(self) -> int:
        return self.inbound_ms + self.outbound_ms

    def inbound_minutes(self) -> float:
        return self.inbound_ms / 60_000

    def outbound_minutes(self) -> float:
        return self.outbound_ms / 60_000

    def total_minutes(self) -> float:
        return self.total_ms / 60_000

    @property
    def n_connected(self) -> int:
        return self.inbound_connected + self.outbound_connected


def load_known_markets(path: Path) -> set[str]:
    if not path.exists():
        return set()
    markets: set[str] = set()
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            code = (row.get("market") or "").strip().upper()
            if code:
                markets.add(code)
    return markets


def derive_market(queue_name: str, known_markets: set[str]) -> str:
    name = (queue_name or "").strip()
    if not name or name == "(no queue)":
        return "NO_QUEUE"

    market, _center = queue_parts(name)
    if market:
        return market

    upper = name.upper()
    if re.search(r"\bHQ\b", upper) or "HEADQUARTERS" in upper:
        return "HQ"

    # FGC centers roll up to Tampa market in Archwell naming
    if re.search(r"\bFGC\b", upper) or re.search(r"\bTMP\b", upper):
        return "TMP"

    for code in sorted(known_markets, key=len, reverse=True):
        if re.search(rf"(?<![A-Z0-9]){re.escape(code)}(?![A-Z0-9])", upper):
            return code[:3]

    return "UNKNOWN"


def api_interval(start: date, end: date) -> str:
    start_iso = f"{start.isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    end_iso = f"{(end + timedelta(days=1)).isoformat()}T{TZ_OFFSET_HOURS:02d}:00:00.000Z"
    return f"{start_iso}/{end_iso}"


def fetch_queue_metrics(
    client,
    interval: str,
    direction: str,
) -> dict[str, tuple[int, int]]:
    """Return queue_id -> (tHandle sum ms, nConnected count)."""
    body: dict[str, Any] = {
        "interval": interval,
        "groupBy": ["queueId"],
        "filter": {
            "type": "and",
            "predicates": [
                {"type": "dimension", "dimension": "direction", "operator": "matches", "value": direction},
                {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "voice"},
            ],
        },
        "metrics": ["tHandle", "nConnected"],
    }
    label = f"{direction} voice by queue"
    tool_log("info", f"Querying analytics: {label}")
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)

    totals: dict[str, tuple[int, int]] = {}
    for result in resp.get("results") or []:
        group = result.get("group") or {}
        queue_id = safe_str(group.get("queueId")) or ""
        handle_ms = 0
        connected = 0
        for bucket in result.get("data") or []:
            _handle_count, h_sum = metric_stats(bucket, "tHandle")
            handle_ms += h_sum
            conn_count, _conn_sum = metric_stats(bucket, "nConnected")
            connected += conn_count
        if handle_ms > 0 or connected > 0:
            prev_ms, prev_conn = totals.get(queue_id, (0, 0))
            totals[queue_id] = (prev_ms + handle_ms, prev_conn + connected)

    tool_log("info", f"  {label}: {len(totals)} queues with activity")
    return totals


def load_queue_names(client) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in client.paged_get("/api/v2/routing/queues"):
        qid = safe_str(row.get("id"))
        if qid:
            names[qid] = safe_str(row.get("name")) or qid
    tool_log("info", f"Loaded {len(names)} queues")
    return names


def merge_queue_totals(
    inbound: dict[str, tuple[int, int]],
    outbound: dict[str, tuple[int, int]],
) -> dict[str, QueueTotals]:
    keys = set(inbound) | set(outbound)
    merged: dict[str, QueueTotals] = {}
    for qid in keys:
        in_ms, in_conn = inbound.get(qid, (0, 0))
        out_ms, out_conn = outbound.get(qid, (0, 0))
        merged[qid] = QueueTotals(
            inbound_ms=in_ms,
            outbound_ms=out_ms,
            inbound_connected=in_conn,
            outbound_connected=out_conn,
        )
    return merged


def queue_display_name(queue_id: str, queue_names: dict[str, str]) -> str:
    if not queue_id:
        return "(no queue)"
    return queue_names.get(queue_id, queue_id)


def _format_sheet(ws, *, header_row: int = 1) -> None:
    for column_cells in ws.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells)
        letter = get_column_letter(column_cells[0].column)
        ws.column_dimensions[letter].width = min(max(width + 2, 12), 52)
    ws.freeze_panes = f"A{header_row + 1}"
    if ws.max_row >= header_row and ws.max_column >= 1:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(ws.max_column)}{ws.max_row}"


def _style_header(ws, row: int = 1) -> None:
    for cell in ws[row]:
        cell.font = BOLD
        cell.fill = HEADER_FILL


def write_workbook(
    path: Path,
    *,
    by_queue_rows: list[list[Any]],
    by_market_rows: list[list[Any]],
    summary_rows: list[list[Any]],
    meta: dict[str, str],
) -> None:
    wb = Workbook()

    ws_summary = wb.active
    ws_summary.title = "Summary"
    ws_summary.append(["Field", "Value"])
    for key, value in meta.items():
        ws_summary.append([key, value])
    ws_summary.append([])
    ws_summary.append(["Metric", "Inbound Min", "Outbound Min", "Total Min", "nConnected"])
    _style_header(ws_summary, row=ws_summary.max_row)
    for row in summary_rows:
        ws_summary.append(row)
    _format_sheet(ws_summary, header_row=1)

    queue_headers = [
        "Queue Name",
        "Queue ID",
        "Market",
        "Inbound Handle Min",
        "Outbound Handle Min",
        "Total Handle Min",
        "nConnected",
        "% of Org Total Min",
    ]
    ws_queue = wb.create_sheet("By_Queue")
    ws_queue.append(queue_headers)
    _style_header(ws_queue)
    for row in by_queue_rows:
        ws_queue.append(row)
    for row_idx in range(2, ws_queue.max_row + 1):
        for col in (4, 5, 6):
            ws_queue.cell(row_idx, col).number_format = MINUTES_FMT
        ws_queue.cell(row_idx, 7).number_format = COUNT_FMT
        ws_queue.cell(row_idx, 8).number_format = PCT_FMT
    _format_sheet(ws_queue)

    market_headers = [
        "Market",
        "Queue Count",
        "Inbound Handle Min",
        "Outbound Handle Min",
        "Total Handle Min",
        "nConnected",
        "% of Org Total Min",
    ]
    ws_market = wb.create_sheet("By_Market")
    ws_market.append(market_headers)
    _style_header(ws_market)
    for row in by_market_rows:
        ws_market.append(row)
    for row_idx in range(2, ws_market.max_row + 1):
        for col in (3, 4, 5):
            ws_market.cell(row_idx, col).number_format = MINUTES_FMT
        ws_market.cell(row_idx, 6).number_format = COUNT_FMT
        ws_market.cell(row_idx, 7).number_format = PCT_FMT
    _format_sheet(ws_market)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def build_rows(
    queue_totals: dict[str, QueueTotals],
    queue_names: dict[str, str],
    known_markets: set[str],
) -> tuple[list[list[Any]], list[list[Any]], dict[str, float]]:
    org_total_min = sum(t.total_minutes() for t in queue_totals.values())

    by_queue: list[list[Any]] = []
    market_acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"queues": set(), "inbound": 0.0, "outbound": 0.0, "total": 0.0, "connected": 0}
    )

    for qid, totals in sorted(
        queue_totals.items(),
        key=lambda item: item[1].total_minutes(),
        reverse=True,
    ):
        qname = queue_display_name(qid, queue_names)
        market = derive_market(qname, known_markets)
        inbound_min = round(totals.inbound_minutes(), 1)
        outbound_min = round(totals.outbound_minutes(), 1)
        total_min = round(totals.total_minutes(), 1)
        pct = (total_min / org_total_min) if org_total_min else 0.0
        by_queue.append([
            qname,
            qid or "",
            market,
            inbound_min,
            outbound_min,
            total_min,
            totals.n_connected,
            pct,
        ])
        acc = market_acc[market]
        acc["queues"].add(qid or qname)
        acc["inbound"] += inbound_min
        acc["outbound"] += outbound_min
        acc["total"] += total_min
        acc["connected"] += totals.n_connected

    by_market: list[list[Any]] = []
    for market, acc in sorted(market_acc.items(), key=lambda x: x[1]["total"], reverse=True):
        pct = (acc["total"] / org_total_min) if org_total_min else 0.0
        by_market.append([
            market,
            len(acc["queues"]),
            round(acc["inbound"], 1),
            round(acc["outbound"], 1),
            round(acc["total"], 1),
            acc["connected"],
            pct,
        ])

    org = {
        "inbound_min": sum(t.inbound_minutes() for t in queue_totals.values()),
        "outbound_min": sum(t.outbound_minutes() for t in queue_totals.values()),
        "total_min": org_total_min,
        "connected": sum(t.n_connected for t in queue_totals.values()),
    }
    return by_queue, by_market, org


def send_report_email(
    report_path: Path,
    *,
    start: date,
    end: date,
    org: dict[str, float],
    top_markets: list[str],
    top_queues: list[str],
) -> None:
    from tools.email.config import default_email_env_file

    mail_script = PROJECT_ROOT / "tools" / "email" / "gmail_send.py"
    env_file = default_email_env_file()
    subject = f"Voice Handle Minutes by Queue/Market ({start.isoformat()} to {end.isoformat()})"
    body = f"""Hi Doug,

Attached is the voice handle minutes (tHandle) report split by queue and derived market.

Period: {start.isoformat()} through {end.isoformat()} (US Eastern, UTC-{TZ_OFFSET_HOURS:02d})
Org totals (queue-attributed):
  Inbound:  {org['inbound_min']:,.1f} min
  Outbound: {org['outbound_min']:,.1f} min
  Total:    {org['total_min']:,.1f} min
  nConnected: {org['connected']:,}

Top markets by minutes:
{top_markets}

Top queues by minutes:
{top_queues}

File: {report_path.name}

— GenesysCloudToolkit
"""
    cmd = [
        sys.executable,
        str(mail_script),
        "--env-file",
        str(env_file),
        "--to",
        "doug.hendrickson@archwellhealth.com",
        "--subject",
        subject,
        "--body",
        body,
        "--attach",
        str(report_path),
    ]
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def run_report(
    client,
    *,
    start: date,
    end: date,
    known_markets: set[str],
    output: Path | None,
) -> tuple[Path, dict[str, str], dict[str, float], list[list[Any]], list[list[Any]]]:
    interval = api_interval(start, end)
    inbound = fetch_queue_metrics(client, interval, "inbound")
    outbound = fetch_queue_metrics(client, interval, "outbound")
    queue_names = load_queue_names(client)
    queue_totals = merge_queue_totals(inbound, outbound)

    by_queue, by_market, org = build_rows(queue_totals, queue_names, known_markets)

    summary_metric_rows = [
        ["Inbound (queue-attributed)", round(org["inbound_min"], 1), 0, round(org["inbound_min"], 1), sum(t.inbound_connected for t in queue_totals.values())],
        ["Outbound (queue-attributed)", 0, round(org["outbound_min"], 1), round(org["outbound_min"], 1), sum(t.outbound_connected for t in queue_totals.values())],
        ["Total (queue-attributed)", round(org["inbound_min"], 1), round(org["outbound_min"], 1), round(org["total_min"], 1), org["connected"]],
    ]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    default_name = f"voice_minutes_by_queue_market_{start.strftime('%Y%m')}_{timestamp}.xlsx"
    report_path = output or output_path(default_name)

    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "timezone": f"US Eastern (UTC-{TZ_OFFSET_HOURS:02d} day boundary)",
        "media_type": "voice",
        "handle_metric": "tHandle (talk + hold + ACW)",
        "connected_metric": "nConnected",
        "group_by": "queueId",
        "queues_with_activity": str(len(queue_totals)),
        "market_mapping": MARKET_MAPPING_NOTES.replace("\n", " | "),
    }

    write_workbook(
        report_path,
        by_queue_rows=by_queue,
        by_market_rows=by_market,
        summary_rows=summary_metric_rows,
        meta=meta,
    )
    tool_log("ok", f"Saved {report_path}")
    return report_path, meta, org, by_queue, by_market


def parse_date(value: str) -> date:
    return date.fromisoformat(value.strip())


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(
        description="Voice handle minutes by queue and derived market (tHandle, nConnected)."
    )
    add_env_arg(parser)
    parser.add_argument("--start", required=True, help="Period start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="Period end date (YYYY-MM-DD)")
    parser.add_argument(
        "--markets-csv",
        default=str(DEFAULT_MARKETS_CSV),
        help="Known market codes CSV (default: config/area_code_markets.csv)",
    )
    parser.add_argument("--output", default=None, help="Output XLSX path")
    parser.add_argument("--email", action="store_true", help="Email report to doug.hendrickson@archwellhealth.com")
    args = parser.parse_args()

    start = parse_date(args.start)
    end = parse_date(args.end)
    if end < start:
        start, end = end, start

    known_markets = load_known_markets(Path(args.markets_csv))
    client = create_client(log=tool_log, env_file=args.env_file)

    report_path, meta, org, by_queue, by_market = run_report(
        client,
        start=start,
        end=end,
        known_markets=known_markets,
        output=Path(args.output) if args.output else None,
    )

    tool_log("info", f"Inbound:  {org['inbound_min']:,.1f} min")
    tool_log("info", f"Outbound: {org['outbound_min']:,.1f} min")
    tool_log("info", f"Total:    {org['total_min']:,.1f} min")
    tool_log("info", f"nConnected: {org['connected']:,}")

    top_market_lines = []
    for row in by_market[:5]:
        top_market_lines.append(f"  - {row[0]}: {row[4]:,.1f} min ({row[6]:.1%} of total)")
    top_queue_lines = []
    for row in by_queue[:5]:
        top_queue_lines.append(f"  - {row[0]} [{row[2]}]: {row[5]:,.1f} min")

    if args.email:
        send_report_email(
            report_path,
            start=start,
            end=end,
            org=org,
            top_markets="\n".join(top_market_lines) if top_market_lines else "  (none)",
            top_queues="\n".join(top_queue_lines) if top_queue_lines else "  (none)",
        )
        tool_log("ok", "Emailed report to doug.hendrickson@archwellhealth.com")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
