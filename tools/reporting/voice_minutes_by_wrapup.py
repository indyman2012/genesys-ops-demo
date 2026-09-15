#!/usr/bin/env python3
"""Voice handle minutes (tHandle) grouped by wrap-up code."""
from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
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
MINUTES_FMT = "#,##0.1"
COUNT_FMT = "#,##0"
PCT_FMT = "0.00%"

SYSTEM_WRAPUP_IDS = {
    "ININ-WRAP-UP-TIMEOUT": "ININ-WRAP-UP-TIMEOUT",
}


@dataclass
class HandleTotals:
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


def voice_filter(direction: str) -> dict[str, Any]:
    return {
        "type": "and",
        "predicates": [
            {"type": "dimension", "dimension": "direction", "operator": "matches", "value": direction},
            {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "voice"},
        ],
    }


def fetch_org_totals(client, interval: str, direction: str) -> tuple[int, int]:
    body: dict[str, Any] = {
        "interval": interval,
        "filter": voice_filter(direction),
        "metrics": ["tHandle", "nConnected"],
    }
    label = f"{direction} voice org total"
    tool_log("info", f"Querying analytics: {label}")
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)
    handle_ms = 0
    connected = 0
    for result in resp.get("results") or []:
        for bucket in result.get("data") or []:
            conn_count, _ = metric_stats(bucket, "nConnected")
            _handle_count, handle_sum = metric_stats(bucket, "tHandle")
            handle_ms += handle_sum
            connected += conn_count
    tool_log("info", f"  {label}: {handle_ms / 60_000:,.1f} min, nConnected={connected:,}")
    return handle_ms, connected


def fetch_wrapup_metrics(
    client,
    interval: str,
    direction: str,
) -> dict[str, tuple[int, int]]:
    body: dict[str, Any] = {
        "interval": interval,
        "groupBy": ["wrapUpCode"],
        "filter": voice_filter(direction),
        "metrics": ["tHandle", "nConnected"],
    }
    label = f"{direction} voice by wrapUpCode"
    tool_log("info", f"Querying analytics: {label}")
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)

    totals: dict[str, tuple[int, int]] = {}
    for result in resp.get("results") or []:
        group = result.get("group") or {}
        wrap_id = safe_str(group.get("wrapUpCode")) or ""
        handle_ms = 0
        connected = 0
        for bucket in result.get("data") or []:
            conn_count, _ = metric_stats(bucket, "nConnected")
            _handle_count, h_sum = metric_stats(bucket, "tHandle")
            handle_ms += h_sum
            connected += conn_count
        if handle_ms > 0 or connected > 0:
            prev_ms, prev_conn = totals.get(wrap_id, (0, 0))
            totals[wrap_id] = (prev_ms + handle_ms, prev_conn + connected)

    tool_log("info", f"  {label}: {len(totals)} wrap-up buckets with activity")
    return totals


def fetch_wrapup_queue_metrics(
    client,
    interval: str,
    direction: str,
) -> dict[tuple[str, str], tuple[int, int]]:
    body: dict[str, Any] = {
        "interval": interval,
        "groupBy": ["wrapUpCode", "queueId"],
        "filter": voice_filter(direction),
        "metrics": ["tHandle", "nConnected"],
    }
    label = f"{direction} voice by wrapUpCode+queueId"
    tool_log("info", f"Querying analytics: {label}")
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)

    totals: dict[tuple[str, str], tuple[int, int]] = {}
    for result in resp.get("results") or []:
        group = result.get("group") or {}
        wrap_id = safe_str(group.get("wrapUpCode")) or ""
        queue_id = safe_str(group.get("queueId")) or ""
        handle_ms = 0
        connected = 0
        for bucket in result.get("data") or []:
            conn_count, _ = metric_stats(bucket, "nConnected")
            _handle_count, h_sum = metric_stats(bucket, "tHandle")
            handle_ms += h_sum
            connected += conn_count
        if handle_ms > 0 or connected > 0:
            key = (wrap_id, queue_id)
            prev_ms, prev_conn = totals.get(key, (0, 0))
            totals[key] = (prev_ms + handle_ms, prev_conn + connected)

    tool_log("info", f"  {label}: {len(totals)} wrap-up/queue buckets")
    return totals


def load_wrapup_names(client) -> dict[str, str]:
    names = {wid: label for wid, label in SYSTEM_WRAPUP_IDS.items()}
    for code in client.paged_get("/api/v2/routing/wrapupcodes"):
        cid = safe_str(code.get("id"))
        if cid:
            names[cid] = safe_str(code.get("name")) or cid
    return names


def load_queue_names(client) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in client.paged_get("/api/v2/routing/queues"):
        qid = safe_str(row.get("id"))
        if qid:
            names[qid] = safe_str(row.get("name")) or qid
    tool_log("info", f"Loaded {len(names)} queues")
    return names


def wrapup_display_name(wrap_id: str, wrap_names: dict[str, str]) -> str:
    if not wrap_id:
        return "(no wrap-up code)"
    return wrap_names.get(wrap_id, wrap_id)


def queue_display_name(queue_id: str, queue_names: dict[str, str]) -> str:
    if not queue_id:
        return "(no queue)"
    return queue_names.get(queue_id, queue_id)


def merge_wrapup_totals(
    inbound: dict[str, tuple[int, int]],
    outbound: dict[str, tuple[int, int]],
) -> dict[str, HandleTotals]:
    keys = set(inbound) | set(outbound)
    merged: dict[str, HandleTotals] = {}
    for wid in keys:
        in_ms, in_conn = inbound.get(wid, (0, 0))
        out_ms, out_conn = outbound.get(wid, (0, 0))
        merged[wid] = HandleTotals(
            inbound_ms=in_ms,
            outbound_ms=out_ms,
            inbound_connected=in_conn,
            outbound_connected=out_conn,
        )
    return merged


def merge_wrapup_queue_totals(
    inbound: dict[tuple[str, str], tuple[int, int]],
    outbound: dict[tuple[str, str], tuple[int, int]],
) -> dict[tuple[str, str], HandleTotals]:
    keys = set(inbound) | set(outbound)
    merged: dict[tuple[str, str], HandleTotals] = {}
    for key in keys:
        in_ms, in_conn = inbound.get(key, (0, 0))
        out_ms, out_conn = outbound.get(key, (0, 0))
        merged[key] = HandleTotals(
            inbound_ms=in_ms,
            outbound_ms=out_ms,
            inbound_connected=in_conn,
            outbound_connected=out_conn,
        )
    return merged


def build_wrapup_rows(
    wrapup_totals: dict[str, HandleTotals],
    wrap_names: dict[str, str],
) -> tuple[list[list[Any]], dict[str, float]]:
    org_total_min = sum(t.total_minutes() for t in wrapup_totals.values())
    rows: list[list[Any]] = []
    for wid, totals in sorted(
        wrapup_totals.items(),
        key=lambda item: item[1].total_minutes(),
        reverse=True,
    ):
        inbound_min = round(totals.inbound_minutes(), 1)
        outbound_min = round(totals.outbound_minutes(), 1)
        total_min = round(totals.total_minutes(), 1)
        pct = (total_min / org_total_min) if org_total_min else 0.0
        rows.append([
            wrapup_display_name(wid, wrap_names),
            wid,
            inbound_min,
            outbound_min,
            total_min,
            totals.n_connected,
            pct,
        ])
    org = {
        "inbound_min": sum(t.inbound_minutes() for t in wrapup_totals.values()),
        "outbound_min": sum(t.outbound_minutes() for t in wrapup_totals.values()),
        "total_min": org_total_min,
        "connected": sum(t.n_connected for t in wrapup_totals.values()),
    }
    return rows, org


def build_wrapup_queue_rows(
    wrapup_queue_totals: dict[tuple[str, str], HandleTotals],
    wrap_names: dict[str, str],
    queue_names: dict[str, str],
    known_markets: set[str],
    org_total_min: float,
) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for (wid, qid), totals in sorted(
        wrapup_queue_totals.items(),
        key=lambda item: item[1].total_minutes(),
        reverse=True,
    ):
        qname = queue_display_name(qid, queue_names)
        market = derive_market(qname, known_markets)
        total_min = round(totals.total_minutes(), 1)
        pct = (total_min / org_total_min) if org_total_min else 0.0
        rows.append([
            wrapup_display_name(wid, wrap_names),
            wid,
            qname,
            qid,
            market,
            round(totals.inbound_minutes(), 1),
            round(totals.outbound_minutes(), 1),
            total_min,
            totals.n_connected,
            pct,
        ])
    return rows


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
    meta: dict[str, str],
    summary_rows: list[list[Any]],
    by_wrapup_rows: list[list[Any]],
    by_wrapup_queue_rows: list[list[Any]],
) -> None:
    wb = Workbook()

    ws_summary = wb.active
    ws_summary.title = "Summary"
    ws_summary.append(["Field", "Value"])
    for key, value in meta.items():
        ws_summary.append([key, value])
    ws_summary.append([])
    ws_summary.append([
        "Metric",
        "Inbound Min",
        "Outbound Min",
        "Total Min",
        "nConnected",
        "Notes",
    ])
    _style_header(ws_summary, row=ws_summary.max_row)
    for row in summary_rows:
        ws_summary.append(row)
    _format_sheet(ws_summary, header_row=1)

    wrap_headers = [
        "Wrap-Up Code",
        "Wrap-Up ID",
        "Inbound Handle Min",
        "Outbound Handle Min",
        "Total Handle Min",
        "nConnected",
        "% of Wrap-Up Total Min",
    ]
    ws_wrap = wb.create_sheet("By_WrapUp")
    ws_wrap.append(wrap_headers)
    _style_header(ws_wrap)
    for row in by_wrapup_rows:
        ws_wrap.append(row)
    for row_idx in range(2, ws_wrap.max_row + 1):
        for col in (3, 4, 5):
            ws_wrap.cell(row_idx, col).number_format = MINUTES_FMT
        ws_wrap.cell(row_idx, 6).number_format = COUNT_FMT
        ws_wrap.cell(row_idx, 7).number_format = PCT_FMT
    _format_sheet(ws_wrap)

    if by_wrapup_queue_rows:
        queue_headers = [
            "Wrap-Up Code",
            "Wrap-Up ID",
            "Queue Name",
            "Queue ID",
            "Market",
            "Inbound Handle Min",
            "Outbound Handle Min",
            "Total Handle Min",
            "nConnected",
            "% of Wrap-Up Total Min",
        ]
        ws_wq = wb.create_sheet("By_WrapUp_Queue")
        ws_wq.append(queue_headers)
        _style_header(ws_wq)
        for row in by_wrapup_queue_rows:
            ws_wq.append(row)
        for row_idx in range(2, ws_wq.max_row + 1):
            for col in (6, 7, 8):
                ws_wq.cell(row_idx, col).number_format = MINUTES_FMT
            ws_wq.cell(row_idx, 9).number_format = COUNT_FMT
            ws_wq.cell(row_idx, 10).number_format = PCT_FMT
        _format_sheet(ws_wq)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def send_report_email(
    report_path: Path,
    *,
    start: date,
    end: date,
    org_wrap: dict[str, float],
    org_api: dict[str, float],
    top_wrapups: list[str],
) -> None:
    from tools.email.config import default_email_env_file

    mail_script = PROJECT_ROOT / "tools" / "email" / "gmail_send.py"
    env_file = default_email_env_file()
    subject = f"Voice Handle Minutes by Wrap-Up ({start.isoformat()} to {end.isoformat()})"
    body = f"""Hi Doug,

Attached is the voice handle minutes (tHandle) report grouped by wrap-up code.

Period: {start.isoformat()} through {end.isoformat()} (US Eastern, UTC-{TZ_OFFSET_HOURS:02d})
Wrap-up attributed totals:
  Inbound:  {org_wrap['inbound_min']:,.1f} min
  Outbound: {org_wrap['outbound_min']:,.1f} min
  Total:    {org_wrap['total_min']:,.1f} min
  nConnected: {org_wrap['connected']:,}

Org voice tHandle cross-check (no groupBy):
  Inbound:  {org_api['inbound_min']:,.1f} min
  Outbound: {org_api['outbound_min']:,.1f} min
  Total:    {org_api['total_min']:,.1f} min
  nConnected: {org_api['connected']:,}

Top wrap-up codes by minutes:
{top_wrapups}

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
    include_queue_tab: bool,
) -> tuple[Path, dict[str, str], dict[str, float], dict[str, float], list[list[Any]], list[list[Any]]]:
    interval = api_interval(start, end)

    in_org_ms, in_org_conn = fetch_org_totals(client, interval, "inbound")
    out_org_ms, out_org_conn = fetch_org_totals(client, interval, "outbound")
    org_api = {
        "inbound_min": in_org_ms / 60_000,
        "outbound_min": out_org_ms / 60_000,
        "total_min": (in_org_ms + out_org_ms) / 60_000,
        "connected": in_org_conn + out_org_conn,
    }

    inbound = fetch_wrapup_metrics(client, interval, "inbound")
    outbound = fetch_wrapup_metrics(client, interval, "outbound")
    wrap_names = load_wrapup_names(client)
    wrapup_totals = merge_wrapup_totals(inbound, outbound)
    by_wrapup, org_wrap = build_wrapup_rows(wrapup_totals, wrap_names)

    by_wrapup_queue: list[list[Any]] = []
    if include_queue_tab:
        queue_names = load_queue_names(client)
        in_wq = fetch_wrapup_queue_metrics(client, interval, "inbound")
        out_wq = fetch_wrapup_queue_metrics(client, interval, "outbound")
        wq_totals = merge_wrapup_queue_totals(in_wq, out_wq)
        by_wrapup_queue = build_wrapup_queue_rows(
            wq_totals,
            wrap_names,
            queue_names,
            known_markets,
            org_wrap["total_min"],
        )

    summary_rows = [
        [
            "Wrap-up attributed (groupBy wrapUpCode)",
            round(org_wrap["inbound_min"], 1),
            round(org_wrap["outbound_min"], 1),
            round(org_wrap["total_min"], 1),
            org_wrap["connected"],
            "Sum of wrap-up buckets",
        ],
        [
            "Org voice tHandle (no groupBy)",
            round(org_api["inbound_min"], 1),
            round(org_api["outbound_min"], 1),
            round(org_api["total_min"], 1),
            org_api["connected"],
            "Cross-check total",
        ],
        [
            "Delta (org - wrap-up sum)",
            round(org_api["inbound_min"] - org_wrap["inbound_min"], 1),
            round(org_api["outbound_min"] - org_wrap["outbound_min"], 1),
            round(org_api["total_min"] - org_wrap["total_min"], 1),
            org_api["connected"] - org_wrap["connected"],
            "Non-zero if segments lack wrapUpCode",
        ],
    ]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    default_name = f"voice_minutes_by_wrapup_{start.strftime('%Y%m')}_{timestamp}.xlsx"
    report_path = output or output_path(default_name)

    meta = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "timezone": f"US Eastern (UTC-{TZ_OFFSET_HOURS:02d} day boundary)",
        "media_type": "voice",
        "handle_metric": "tHandle (talk + hold + ACW)",
        "connected_metric": "nConnected",
        "group_by": "wrapUpCode (+ queueId on By_WrapUp_Queue tab)",
        "wrapup_codes_with_activity": str(len(wrapup_totals)),
    }

    write_workbook(
        report_path,
        meta=meta,
        summary_rows=summary_rows,
        by_wrapup_rows=by_wrapup,
        by_wrapup_queue_rows=by_wrapup_queue,
    )
    tool_log("ok", f"Saved {report_path}")
    return report_path, meta, org_wrap, org_api, by_wrapup, by_wrapup_queue


def parse_date(value: str) -> date:
    return date.fromisoformat(value.strip())


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(
        description="Voice handle minutes grouped by wrap-up code (tHandle, nConnected)."
    )
    add_env_arg(parser)
    parser.add_argument("--start", required=True, help="Period start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="Period end date (YYYY-MM-DD)")
    parser.add_argument(
        "--markets-csv",
        default=str(DEFAULT_MARKETS_CSV),
        help="Known market codes CSV for queue/market on By_WrapUp_Queue tab",
    )
    parser.add_argument("--output", default=None, help="Output XLSX path")
    parser.add_argument(
        "--no-queue-tab",
        action="store_true",
        help="Omit By_WrapUp_Queue sheet (skips wrapUpCode+queueId query)",
    )
    parser.add_argument("--email", action="store_true", help="Email report to doug.hendrickson@archwellhealth.com")
    args = parser.parse_args()

    start = parse_date(args.start)
    end = parse_date(args.end)
    if end < start:
        start, end = end, start

    known_markets = load_known_markets(Path(args.markets_csv))
    client = create_client(log=tool_log, env_file=args.env_file)

    report_path, _meta, org_wrap, org_api, by_wrapup, _by_wq = run_report(
        client,
        start=start,
        end=end,
        known_markets=known_markets,
        output=Path(args.output) if args.output else None,
        include_queue_tab=not args.no_queue_tab,
    )

    tool_log("info", f"Wrap-up inbound:  {org_wrap['inbound_min']:,.1f} min")
    tool_log("info", f"Wrap-up outbound: {org_wrap['outbound_min']:,.1f} min")
    tool_log("info", f"Wrap-up total:    {org_wrap['total_min']:,.1f} min")
    tool_log("info", f"Org API total:    {org_api['total_min']:,.1f} min")
    tool_log("info", f"nConnected:       {org_wrap['connected']:,}")

    top_lines = []
    for row in by_wrapup[:10]:
        top_lines.append(f"  - {row[0]}: {row[4]:,.1f} min ({row[6]:.1%} of wrap-up total)")
    top_text = "\n".join(top_lines) if top_lines else "  (none)"

    if args.email:
        send_report_email(
            report_path,
            start=start,
            end=end,
            org_wrap=org_wrap,
            org_api=org_api,
            top_wrapups=top_text,
        )
        tool_log("ok", "Emailed report to doug.hendrickson@archwellhealth.com")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
