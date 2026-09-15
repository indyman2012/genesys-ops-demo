#!/usr/bin/env python3
"""Three-month wrap-up code usage report by center, queue, month, and code."""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

MANUAL_SITE_QUEUE_HINTS: dict[str, str] = {
    "CLEELK": "NCM_CLE_Eastlake",
    "CLEMPH": "NCM_CLE_Maple Heights",
    "CLEPMA": "NCM_CLE_Parma",
    "CLEWHI": "NCM_CLE_Woodland Hills",
    "CLEWLK": "NCM_CLE_Westlake",
    "DENLWD": "NCM_DEN_Lakewood",
    "JAXBNT": "NCM_JAX_Brentwood",
    "JAXHBV": "NCM_JAX_Harborview",
    "JAXJXH": "NCM_JAX_Jax Heights",
    "LITFPK": "NCM_LIT_Fair Park",
    "LITPBF": "NCM_LIT_Pine Bluff",
    "PHLGMT": "NCM_PHL_Germantown",
    "PHXPAP": "NCM_PHX_Papago",
    "TMPPPK": "NCM_FGC_Page Park",
    "TMPPTC": "NCM_FGC_Port Charlotte",
    "TMPZHI": "Patient Services - TMP - Zephyrhills",
}

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
SUMMARY_FILL = PatternFill("solid", fgColor="F2F2F2")
CENTER_FILL = PatternFill("solid", fgColor="E2EFDA")
GRAND_FILL = PatternFill("solid", fgColor="FFF2CC")
BOLD = Font(bold=True)


def queue_parts(name: str) -> tuple[str | None, str | None]:
    name = (name or "").strip()
    patterns = [
        r"^Patient Services\s*-\s*([A-Z0-9]{2,4})\s*-\s*(.+)$",
        r"^Patient Services\s*-\s*([A-Z0-9]{2,4})\s+(.+)$",
        r"^NCM_([A-Z0-9]{2,4})_(.+)$",
        r"^Sales\s*-\s*([A-Z0-9]{2,4})\s*-\s*(.+)$",
    ]
    for pat in patterns:
        m = re.match(pat, name, re.I)
        if m:
            return m.group(1).upper()[:3], m.group(2).strip()
    return None, None


def center_tokens(center: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9]+", center)
    tokens: set[str] = set()
    for w in words:
        tokens.add(w.upper())
        if len(w) >= 2:
            tokens.add(w[:2].upper())
        if len(w) >= 3:
            tokens.add(w[:3].upper())
            tokens.add(w[0].upper() + w[1].upper() + w[-1].upper())
    if len(words) >= 2:
        tokens.add("".join(w[0] for w in words).upper())
        tokens.add("".join(w[0] for w in words[:3]).upper())
    compact = re.sub(r"[^A-Za-z]", "", center).upper()
    if len(compact) >= 3:
        tokens.add(compact[:3])
    return tokens


def build_queue_center_map(client) -> dict[str, str]:
    sites = client.paged_get("/api/v2/telephony/providers/edges/sites")
    queues = client.paged_get("/api/v2/routing/queues")
    six_char_sites = {
        (s.get("name") or "").upper(): s
        for s in sites
        if len((s.get("name") or "").strip()) == 6
    }
    queue_by_name = {(q.get("name") or ""): q for q in queues}
    queue_by_id = {q["id"]: q for q in queues}

    queue_rows = []
    for q in queues:
        market, center = queue_parts(q.get("name") or "")
        if market and center:
            queue_rows.append({
                "id": q["id"],
                "name": q["name"],
                "market": market,
                "center": center,
                "tokens": center_tokens(center),
            })

    queue_to_center: dict[str, str] = {}
    for code in sorted(six_char_sites):
        hint = MANUAL_SITE_QUEUE_HINTS.get(code)
        best_id = None
        if hint:
            for qname, q in queue_by_name.items():
                if hint.lower() in qname.lower() or qname.lower() == hint.lower():
                    best_id = q["id"]
                    break
        if not best_id:
            market = code[:3]
            center_code = code[3:]
            candidates = [q for q in queue_rows if q["market"] == market]
            best = None
            for q in candidates:
                if center_code in q["tokens"]:
                    best = q
                    break
            if not best:
                for q in candidates:
                    letters = re.sub(r"[^A-Z]", "", q["center"].upper())
                    if center_code in letters:
                        best = q
                        break
            if not best and len(candidates) == 1:
                best = candidates[0]
            if best:
                best_id = best["id"]
        if best_id:
            queue_to_center[best_id] = code

    # Fallback: derive pseudo-center from queue name for unmapped queues.
    for qid, q in queue_by_id.items():
        if qid in queue_to_center:
            continue
        market, center = queue_parts(q.get("name") or "")
        if market and center:
            compact = re.sub(r"[^A-Z0-9]", "", center.upper())[:6]
            queue_to_center[qid] = f"{market}{compact}"[:6] or "OTHER"
        else:
            queue_to_center[qid] = "UNMAPPED"

    return queue_to_center


def month_window(months: int, tz_offset_hours: int) -> tuple[str, list[str]]:
    today = date.today()
    first_of_current = today.replace(day=1)
    labels: list[str] = []
    year = first_of_current.year
    month = first_of_current.month
    for _ in range(months):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        labels.append(f"{year:04d}-{month:02d}")
    labels.reverse()

    start_year, start_month = map(int, labels[0].split("-"))
    end_year, end_month = map(int, labels[-1].split("-"))
    if end_month == 12:
        end_year += 1
        end_month = 1
    else:
        end_month += 1

    def iso(y: int, m: int) -> str:
        return f"{y:04d}-{m:02d}-01T{tz_offset_hours:02d}:00:00.000Z"

    interval = f"{iso(start_year, start_month)}/{iso(end_year, end_month)}"
    return interval, labels


def fetch_wrapup_counts(client, interval: str) -> list[dict]:
    body = {
        "interval": interval,
        "granularity": "P1M",
        "groupBy": ["queueId", "wrapUpCode"],
        "metrics": ["tAcw"],
    }
    tool_log("info", f"Querying analytics interval {interval}")
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)
    rows: list[dict] = []
    for result in resp.get("results") or []:
        group = result.get("group") or {}
        queue_id = group.get("queueId") or ""
        wrap_id = group.get("wrapUpCode") or ""
        media = group.get("mediaType") or ""
        if not queue_id or not wrap_id:
            continue
        for bucket in result.get("data") or []:
            interval_label = (bucket.get("interval") or "").split("/")[0][:7]
            if len(interval_label) == 7:
                month = interval_label
            else:
                month = (bucket.get("interval") or "unknown")[:7]
            count = 0
            for metric in bucket.get("metrics") or []:
                if metric.get("metric") == "tAcw":
                    count = int((metric.get("stats") or {}).get("count") or 0)
            if count <= 0:
                continue
            rows.append({
                "queue_id": queue_id,
                "wrapup_id": wrap_id,
                "media_type": media,
                "month": month,
                "count": count,
            })
    return rows


def load_wrapup_names(client) -> dict[str, str]:
    codes = client.paged_get("/api/v2/routing/wrapupcodes")
    return {c["id"]: c.get("name") or c["id"] for c in codes}


def load_queue_names(client) -> dict[str, str]:
    queues = client.paged_get("/api/v2/routing/queues")
    return {q["id"]: q.get("name") or q["id"] for q in queues}


def build_report_rows(
    raw_rows: list[dict],
    queue_names: dict[str, str],
    wrap_names: dict[str, str],
    queue_centers: dict[str, str],
    month_labels: list[str],
) -> list[dict]:
    enriched = []
    for row in raw_rows:
        qid = row["queue_id"]
        wid = row["wrapup_id"]
        enriched.append({
            "center": queue_centers.get(qid, "UNMAPPED"),
            "queue_id": qid,
            "queue_name": queue_names.get(qid, qid),
            "month": row["month"],
            "wrapup_code": wrap_names.get(wid, wid),
            "wrapup_id": wid,
            "media_type": row["media_type"],
            "count": row["count"],
            "row_type": "DETAIL",
        })

    # Keep only months in scope (API may return boundary junk).
    enriched = [r for r in enriched if r["month"] in month_labels]

    output: list[dict] = []
    centers = sorted({r["center"] for r in enriched})
    grand_total = 0

    for center in centers:
        center_rows = [r for r in enriched if r["center"] == center]
        center_total = sum(r["count"] for r in center_rows)

        output.append({
            "row_type": "CENTER_HEADER",
            "center": center,
            "queue_name": "",
            "month": "",
            "wrapup_code": "",
            "media_type": "",
            "count": "",
            "pct_of_parent": "",
        })

        queues = sorted({r["queue_name"] for r in center_rows})
        for queue_name in queues:
            queue_rows = [r for r in center_rows if r["queue_name"] == queue_name]
            queue_total = sum(r["count"] for r in queue_rows)

            months = sorted({r["month"] for r in queue_rows})
            for month in months:
                month_rows = [r for r in queue_rows if r["month"] == month]
                month_total = sum(r["count"] for r in month_rows)
                for detail in sorted(month_rows, key=lambda x: (-x["count"], x["wrapup_code"])):
                    pct = (detail["count"] / month_total * 100) if month_total else 0
                    output.append({**detail, "pct_of_parent": round(pct, 2)})
                output.append({
                    "row_type": "MONTH_SUBTOTAL",
                    "center": center,
                    "queue_name": queue_name,
                    "month": month,
                    "wrapup_code": "MONTH TOTAL",
                    "media_type": "",
                    "count": month_total,
                    "pct_of_parent": round((month_total / queue_total * 100) if queue_total else 0, 2),
                })

            output.append({
                "row_type": "QUEUE_SUBTOTAL",
                "center": center,
                "queue_name": queue_name,
                "month": "",
                "wrapup_code": "QUEUE TOTAL",
                "media_type": "",
                "count": queue_total,
                "pct_of_parent": round((queue_total / center_total * 100) if center_total else 0, 2),
            })

        output.append({
            "row_type": "CENTER_SUBTOTAL",
            "center": center,
            "queue_name": "",
            "month": "",
            "wrapup_code": "CENTER TOTAL",
            "media_type": "",
            "count": center_total,
            "pct_of_parent": "",
        })
        grand_total += center_total

    output.append({
        "row_type": "GRAND_TOTAL",
        "center": "ALL CENTERS",
        "queue_name": "",
        "month": "",
        "wrapup_code": "GRAND TOTAL",
        "media_type": "",
        "count": grand_total,
        "pct_of_parent": 100.0,
    })
    return output


def write_workbook(path: Path, report_rows: list[dict], detail_rows: list[dict], meta: dict) -> None:
    wb = Workbook()

    ws_meta = wb.active
    ws_meta.title = "About"
    ws_meta.append(["Field", "Value"])
    for key, value in meta.items():
        ws_meta.append([key, value])

    ws_detail = wb.create_sheet("Detail")
    detail_headers = [
        "Center", "Queue", "Month", "Wrap-Up Code", "Wrap-Up ID",
        "Media Type", "Count",
    ]
    ws_detail.append(detail_headers)
    for row in sorted(detail_rows, key=lambda r: (r["center"], r["queue_name"], r["month"], -r["count"])):
        ws_detail.append([
            row["center"], row["queue_name"], row["month"], row["wrapup_code"],
            row["wrapup_id"], row["media_type"], row["count"],
        ])

    ws = wb.create_sheet("Wrap-Up Report")
    headers = [
        "Row Type", "Center", "Queue", "Month", "Wrap-Up Code",
        "Media Type", "Count", "Pct of Parent",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL

    for row in report_rows:
        ws.append([
            row["row_type"],
            row.get("center", ""),
            row.get("queue_name", ""),
            row.get("month", ""),
            row.get("wrapup_code", ""),
            row.get("media_type", ""),
            row.get("count", ""),
            row.get("pct_of_parent", ""),
        ])
        row_idx = ws.max_row
        row_type = row["row_type"]
        fill = None
        if row_type == "CENTER_HEADER":
            fill = CENTER_FILL
        elif row_type in {"MONTH_SUBTOTAL", "QUEUE_SUBTOTAL", "CENTER_SUBTOTAL"}:
            fill = SUMMARY_FILL
        elif row_type == "GRAND_TOTAL":
            fill = GRAND_FILL
        if fill:
            for cell in ws[row_idx]:
                cell.font = BOLD
                cell.fill = fill

    for sheet in (ws_meta, ws_detail, ws):
        for column_cells in sheet.columns:
            width = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 48)
        if sheet.max_row > 1:
            sheet.freeze_panes = "A2"

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Wrap-up code report by center/queue/month.")
    parser.add_argument("--months", type=int, default=3, help="Number of complete calendar months (default: 3)")
    parser.add_argument(
        "--tz-offset-hours",
        type=int,
        default=4,
        help="UTC offset hours for month boundaries (default: 4 = US Eastern DST)",
    )
    parser.add_argument("--output", default=None, help="Output .xlsx path")
    add_env_arg(parser)
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    interval, month_labels = month_window(args.months, args.tz_offset_hours)
    tool_log("info", f"Months: {', '.join(month_labels)}")

    queue_centers = build_queue_center_map(client)
    queue_names = load_queue_names(client)
    wrap_names = load_wrapup_names(client)
    raw_rows = fetch_wrapup_counts(client, interval)

    detail_rows = []
    for row in raw_rows:
        if row["month"] not in month_labels:
            continue
        qid = row["queue_id"]
        wid = row["wrapup_id"]
        detail_rows.append({
            "center": queue_centers.get(qid, "UNMAPPED"),
            "queue_name": queue_names.get(qid, qid),
            "month": row["month"],
            "wrapup_code": wrap_names.get(wid, wid),
            "wrapup_id": wid,
            "media_type": row["media_type"],
            "count": row["count"],
        })

    report_rows = build_report_rows(raw_rows, queue_names, wrap_names, queue_centers, month_labels)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else output_path(f"wrapup_code_report_{stamp}.xlsx")
    meta = {
        "Generated At (UTC)": datetime.now(timezone.utc).isoformat(),
        "Months": ", ".join(month_labels),
        "Analytics Interval": interval,
        "Detail Rows": len(detail_rows),
        "Metric": "tAcw count (wrap-up indexed)",
        "Timezone Boundary": f"UTC-{args.tz_offset_hours:02d} month starts",
    }
    write_workbook(out, report_rows, detail_rows, meta)
    tool_log("ok", f"Wrote {out}")
    tool_log("info", f"Detail rows: {len(detail_rows)} | Centers: {len({r['center'] for r in detail_rows})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
