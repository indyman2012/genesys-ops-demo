#!/usr/bin/env python3
"""Wrap-up code counts for selected Patient Services market queues over a rolling day window."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
SUMMARY_FILL = PatternFill("solid", fgColor="FFF2CC")
BOLD = Font(bold=True)
DEFAULT_MARKETS = ("CLT", "BHM", "MGM")


def queue_matches_market(name: str, market: str) -> bool:
    name = (name or "").strip()
    market = market.upper()
    prefix = f"Patient Services - {market}"
    return name.startswith(prefix)


def select_queues(client, markets: tuple[str, ...]) -> tuple[list[dict], list[str]]:
    queues = client.paged_get("/api/v2/routing/queues")
    selected: list[dict] = []
    missing: list[str] = []
    for market in markets:
        hits = [q for q in queues if queue_matches_market(q.get("name") or "", market)]
        if not hits:
            missing.append(market)
        selected.extend(hits)
    deduped = {q["id"]: q for q in selected}
    return list(deduped.values()), missing


def rolling_interval(days: int, tz_offset_hours: int) -> tuple[str, date, date]:
    end = date.today()
    start = end - timedelta(days=days - 1)
    start_iso = f"{start.isoformat()}T{tz_offset_hours:02d}:00:00.000Z"
    end_exclusive = end + timedelta(days=1)
    end_iso = f"{end_exclusive.isoformat()}T{tz_offset_hours:02d}:00:00.000Z"
    return f"{start_iso}/{end_iso}", start, end


def fetch_wrapup_counts(client, interval: str, queue_ids: list[str]) -> list[dict]:
    if not queue_ids:
        return []
    body = {
        "interval": interval,
        "granularity": "P1D",
        "groupBy": ["queueId", "wrapUpCode"],
        "filter": {
            "type": "or",
            "predicates": [
                {
                    "type": "dimension",
                    "dimension": "queueId",
                    "operator": "matches",
                    "value": queue_id,
                }
                for queue_id in queue_ids
            ],
        },
        "metrics": ["tAcw"],
    }
    tool_log("info", f"Querying analytics for {len(queue_ids)} queue(s)")
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)
    rows: list[dict] = []
    for result in resp.get("results") or []:
        group = result.get("group") or {}
        queue_id = group.get("queueId") or ""
        wrap_id = group.get("wrapUpCode") or ""
        media = group.get("mediaType") or ""
        if not queue_id or not wrap_id:
            continue
        count = 0
        for bucket in result.get("data") or []:
            for metric in bucket.get("metrics") or []:
                if metric.get("metric") == "tAcw":
                    count += int((metric.get("stats") or {}).get("count") or 0)
        if count <= 0:
            continue
        rows.append(
            {
                "queue_id": queue_id,
                "wrapup_id": wrap_id,
                "media_type": media,
                "count": count,
            }
        )
    return rows


def load_wrapup_names(client) -> dict[str, str]:
    codes = client.paged_get("/api/v2/routing/wrapupcodes")
    names = {c["id"]: c.get("name") or c["id"] for c in codes}
    names["ININ-WRAP-UP-TIMEOUT"] = "ININ-WRAP-UP-TIMEOUT"
    return names


def aggregate_rows(
    raw_rows: list[dict],
    queue_names: dict[str, str],
    wrap_names: dict[str, str],
) -> list[dict]:
    merged: dict[tuple[str, str, str], int] = {}
    for row in raw_rows:
        key = (row["queue_id"], row["wrapup_id"], row["media_type"])
        merged[key] = merged.get(key, 0) + row["count"]

    output: list[dict] = []
    for (queue_id, wrapup_id, media_type), count in merged.items():
        output.append(
            {
                "queue_id": queue_id,
                "queue_name": queue_names.get(queue_id, queue_id),
                "wrapup_id": wrapup_id,
                "wrapup_code": wrap_names.get(wrapup_id, wrapup_id),
                "media_type": media_type,
                "count": count,
            }
        )
    return output


def write_workbook(
    path: Path,
    *,
    detail_rows: list[dict],
    by_wrapup_rows: list[dict],
    meta: dict,
) -> None:
    wb = Workbook()

    ws_meta = wb.active
    ws_meta.title = "About"
    ws_meta.append(["Field", "Value"])
    for key, value in meta.items():
        ws_meta.append([key, value])

    ws_wrap = wb.create_sheet("By Wrap-Up Code")
    wrap_headers = ["Wrap-Up Code", "Queue", "Media Type", "Count", "Pct of Queue"]
    ws_wrap.append(wrap_headers)
    for cell in ws_wrap[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    for row in by_wrapup_rows:
        ws_wrap.append(
            [
                row["wrapup_code"],
                row["queue_name"],
                row["media_type"],
                row["count"],
                row["pct_of_queue"],
            ]
        )

    ws_queue = wb.create_sheet("By Queue")
    queue_headers = ["Queue", "Wrap-Up Code", "Media Type", "Count", "Pct of Queue"]
    ws_queue.append(queue_headers)
    for cell in ws_queue[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    for row in sorted(
        detail_rows,
        key=lambda r: (r["queue_name"], r["wrapup_code"], r["media_type"]),
    ):
        ws_queue.append(
            [
                row["queue_name"],
                row["wrapup_code"],
                row["media_type"],
                row["count"],
                row["pct_of_queue"],
            ]
        )

    ws_totals = wb.create_sheet("Queue Totals")
    ws_totals.append(["Queue", "Total Wrap-Ups", "Pct of All"])
    for cell in ws_totals[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    grand_total = sum(r["count"] for r in detail_rows)
    queue_totals: dict[str, int] = {}
    for row in detail_rows:
        queue_totals[row["queue_name"]] = queue_totals.get(row["queue_name"], 0) + row["count"]
    for queue_name, total in sorted(queue_totals.items(), key=lambda item: (-item[1], item[0])):
        pct = round((total / grand_total * 100) if grand_total else 0, 2)
        ws_totals.append([queue_name, total, pct])
    ws_totals.append(["ALL QUEUES", grand_total, 100.0 if grand_total else 0])
    for cell in ws_totals[ws_totals.max_row]:
        cell.font = BOLD
        cell.fill = SUMMARY_FILL

    for sheet in wb.worksheets:
        for column_cells in sheet.columns:
            width = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 48)
        if sheet.max_row > 1:
            sheet.freeze_panes = "A2"

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def build_sorted_rows(detail_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    queue_totals: dict[str, int] = {}
    for row in detail_rows:
        queue_totals[row["queue_name"]] = queue_totals.get(row["queue_name"], 0) + row["count"]

    enriched = []
    for row in detail_rows:
        queue_total = queue_totals.get(row["queue_name"], 0)
        enriched.append(
            {
                **row,
                "pct_of_queue": round((row["count"] / queue_total * 100) if queue_total else 0, 2),
            }
        )

    by_wrapup = sorted(
        enriched,
        key=lambda r: (r["wrapup_code"].lower(), r["queue_name"].lower(), r["media_type"]),
    )
    return enriched, by_wrapup


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(
        description="Wrap-up code report for Patient Services market queues."
    )
    parser.add_argument("--days", type=int, default=60, help="Rolling day window (default: 60)")
    parser.add_argument(
        "--markets",
        default=",".join(DEFAULT_MARKETS),
        help="Comma-separated market codes after 'Patient Services -' (default: CLT,BHM,MGM)",
    )
    parser.add_argument(
        "--tz-offset-hours",
        type=int,
        default=4,
        help="UTC offset hours for day boundaries (default: 4 = US Eastern DST)",
    )
    parser.add_argument("--output", default=None, help="Output .xlsx path")
    add_env_arg(parser)
    args = parser.parse_args()

    markets = tuple(m.strip().upper() for m in args.markets.split(",") if m.strip())
    client = create_client(log=tool_log, env_file=args.env_file)
    selected_queues, missing_markets = select_queues(client, markets)
    queue_ids = [q["id"] for q in selected_queues]
    queue_names = {q["id"]: q.get("name") or q["id"] for q in selected_queues}
    wrap_names = load_wrapup_names(client)

    interval, start_date, end_date = rolling_interval(args.days, args.tz_offset_hours)
    tool_log("info", f"Window: {start_date.isoformat()} through {end_date.isoformat()}")

    raw_rows = fetch_wrapup_counts(client, interval, queue_ids)
    detail_rows = aggregate_rows(raw_rows, queue_names, wrap_names)
    detail_rows, by_wrapup_rows = build_sorted_rows(detail_rows)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else output_path(f"ps_wrapup_report_{stamp}.xlsx")
    meta = {
        "Generated At (UTC)": datetime.now(timezone.utc).isoformat(),
        "Window Start": start_date.isoformat(),
        "Window End": end_date.isoformat(),
        "Days": args.days,
        "Markets Requested": ", ".join(markets),
        "Queues Included": len(selected_queues),
        "Queue Names": "; ".join(sorted(queue_names.values())),
        "Markets With No Matching Queue": ", ".join(missing_markets) if missing_markets else "(none)",
        "Analytics Interval": interval,
        "Metric": "tAcw count (wrap-up indexed)",
        "Primary Sort": "Wrap-up code, then queue",
    }
    write_workbook(out, detail_rows=detail_rows, by_wrapup_rows=by_wrapup_rows, meta=meta)

    meta_path = out.parent / f"{out.stem}_meta.json"
    meta_path.write_text(
        json.dumps(
            {
                **meta,
                "report_path": str(out),
                "detail_rows": len(detail_rows),
                "total_wrapups": sum(r["count"] for r in detail_rows),
                "missing_markets": missing_markets,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    tool_log("ok", f"Wrote {out}")
    tool_log("info", f"Detail rows: {len(detail_rows)} | Queues: {len(selected_queues)}")
    if missing_markets:
        tool_log("info", f"No queues found for markets: {', '.join(missing_markets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
