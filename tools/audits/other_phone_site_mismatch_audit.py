#!/usr/bin/env python3
"""Audit active users: Other Phone area code vs WEB phone site market (first 3 chars of site name)."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, read_csv, setup_path, tool_log
from core.genesys_client import safe_str
from core.paths import PROJECT_ROOT

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

DEFAULT_MARKETS = PROJECT_ROOT / "config" / "area_code_markets.csv"
HQ_SITE_NAMES = frozenset({"HEADQUARTERS"})
HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
MISMATCH_FILL = PatternFill("solid", fgColor="F4CCCC")
OK_FILL = PatternFill("solid", fgColor="D9EADC")
BOLD = Font(bold=True)
MAX_WORKERS = 4


@dataclass
class MarketRow:
    market: str
    city: str
    area_codes: list[str]
    notes: str


@dataclass
class AuditRow:
    user_id: str
    name: str
    email: str
    title: str
    other_phone: str
    area_code: str
    expected_markets: str
    expected_cities: str
    phone_name: str
    phone_site: str
    phone_market: str
    status: str
    notes: str


def load_market_rows(path: Path) -> list[MarketRow]:
    rows: list[MarketRow] = []
    for raw in read_csv(path):
        market = (raw.get("market") or "").strip().upper()
        if not market:
            continue
        codes = [c.strip() for c in (raw.get("area_codes") or "").replace(" ", "").split(",") if c.strip()]
        rows.append(
            MarketRow(
                market=market,
                city=(raw.get("city") or "").strip(),
                area_codes=codes,
                notes=(raw.get("notes") or "").strip(),
            )
        )
    return rows


def build_area_code_index(rows: list[MarketRow]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    code_to_markets: dict[str, set[str]] = defaultdict(set)
    market_to_city: dict[str, str] = {}
    for row in rows:
        market_to_city[row.market] = row.city
        for code in row.area_codes:
            code_to_markets[code].add(row.market)
    return dict(code_to_markets), market_to_city


def normalize_area_code(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) >= 10:
        return digits[:3]
    return ""


def phone_addresses(addresses: list) -> list[dict]:
    return [
        item
        for item in (addresses or [])
        if isinstance(item, dict) and safe_str(item.get("mediaType")).upper() == "PHONE"
    ]


def other_phone_entry(addresses: list) -> dict | None:
    for item in phone_addresses(addresses):
        if safe_str(item.get("type")).upper() == "OTHER":
            return item
    return None


def format_phone(entry: dict | None) -> str:
    if not entry:
        return ""
    extension = safe_str(entry.get("extension")).strip()
    if extension:
        return f"ext {extension}"
    return safe_str(entry.get("display") or entry.get("address"))


def normalize_phone_key(name: str) -> str:
    """Collapse whitespace and normalize trailing WEB suffix for matching."""
    parts = " ".join((name or "").split())
    folded = parts.casefold()
    if folded.endswith(" web"):
        return folded
    if folded.endswith("web") and len(folded) > 3:
        return f"{folded[:-3].rstrip()} web"
    return folded


def expected_phone_key(user_name: str) -> str:
    return normalize_phone_key(f"{user_name.strip()} Web")


def display_phone_name(user_name: str) -> str:
    return f"{user_name.strip()} Web"


def site_market(site_name: str) -> str:
    name = (site_name or "").strip().upper()
    if not name:
        return ""
    if name in HQ_SITE_NAMES:
        return "HQ"
    if len(name) >= 3:
        return name[:3]
    return name


def index_phones_by_name(phones: list[dict]) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = defaultdict(list)
    for phone in phones:
        name = safe_str(phone.get("name"))
        if name:
            index[normalize_phone_key(name)].append(phone)
    return index


def load_site_names(client) -> dict[str, str]:
    sites = client.paged_get("/api/v2/telephony/providers/edges/sites")
    return {safe_str(site.get("id")): safe_str(site.get("name")) for site in sites if site.get("id")}


def phone_site_name(phone: dict | None, site_names: dict[str, str]) -> str:
    if not phone:
        return ""
    site = phone.get("site") or {}
    direct = safe_str(site.get("name"))
    if direct:
        return direct
    return site_names.get(safe_str(site.get("id")), "")


def fetch_user_detail(client, user_id: str) -> dict:
    return client.request("GET", f"/api/v2/users/{user_id}")


def evaluate_user(
    user_summary: dict,
    detail: dict,
    phones_by_name: dict[str, list[dict]],
    site_names: dict[str, str],
    code_to_markets: dict[str, set[str]],
    market_to_city: dict[str, str],
) -> AuditRow:
    user_id = safe_str(user_summary.get("id") or detail.get("id"))
    name = safe_str(user_summary.get("name") or detail.get("name"))
    email = safe_str(user_summary.get("email") or detail.get("email"))
    title = safe_str(detail.get("title"))

    other = other_phone_entry(detail.get("addresses") or [])
    other_display = format_phone(other)
    area_code = ""
    if other and not other_display.startswith("ext "):
        area_code = normalize_area_code(
            safe_str(other.get("address") or other.get("display") or other_display)
        )

    phone_name = display_phone_name(name)
    phone_matches = phones_by_name.get(expected_phone_key(name), [])
    phone = phone_matches[0] if len(phone_matches) == 1 else None

    site_name = phone_site_name(phone, site_names)
    phone_market = site_market(site_name)
    expected = sorted(code_to_markets.get(area_code, set())) if area_code else []
    expected_label = ", ".join(expected)
    expected_cities = ", ".join(market_to_city.get(m, m) for m in expected)

    status = "OK"
    notes = ""

    if not other:
        status = "NO_OTHER_PHONE"
        notes = "No Other Phone on user record"
    elif other_display.startswith("ext "):
        status = "OTHER_PHONE_EXTENSION"
        notes = "Other Phone is an extension, not a NANP number"
    elif not area_code:
        status = "INVALID_OTHER_PHONE"
        notes = "Could not parse a 10-digit US area code"
    elif not expected:
        status = "UNKNOWN_AREA_CODE"
        notes = "Area code not in area_code_markets.csv"
    elif not phone_matches:
        status = "NO_PHONE_FOUND"
        notes = f'No phone named "{phone_name}"'
    elif len(phone_matches) > 1:
        status = "MULTIPLE_PHONES"
        notes = f"{len(phone_matches)} phones named {phone_name}"
    elif site_name.upper() in HQ_SITE_NAMES:
        status = "HQ_PHONE_SITE"
        notes = "Phone is assigned to Headquarters"
    elif phone_market in expected:
        status = "OK"
        if len(expected) > 1:
            notes = f"Area code shared across markets: {expected_label}"
    else:
        status = "MISMATCH"
        notes = f"Other Phone implies {expected_label}; phone site is {phone_market}"

    return AuditRow(
        user_id=user_id,
        name=name,
        email=email,
        title=title,
        other_phone=other_display,
        area_code=area_code,
        expected_markets=expected_label,
        expected_cities=expected_cities,
        phone_name=phone_name,
        phone_site=site_name,
        phone_market=phone_market,
        status=status,
        notes=notes,
    )


def write_workbook(path: Path, rows: list[AuditRow], meta: dict[str, str]) -> None:
    wb = Workbook()
    ws_about = wb.active
    ws_about.title = "About"
    ws_about.append(["Field", "Value"])
    for key, value in meta.items():
        ws_about.append([key, value])

    headers = [
        "Status",
        "Name",
        "Email",
        "Title",
        "Other Phone",
        "Area Code",
        "Expected Market(s)",
        "Expected City(ies)",
        "Phone Name",
        "Phone Site",
        "Phone Market",
        "Notes",
        "User ID",
    ]
    action_statuses = frozenset({
        "MISMATCH",
        "NO_PHONE_FOUND",
        "MULTIPLE_PHONES",
        "NO_OTHER_PHONE",
        "OTHER_PHONE_EXTENSION",
        "INVALID_OTHER_PHONE",
        "USER_DETAIL_ERROR",
    })

    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row.status] += 1

    ws_summary = wb.create_sheet("Summary")
    ws_summary.append(["Other Phone vs Phone Site Audit — Summary"])
    ws_summary["A1"].font = BOLD
    ws_summary.append([])
    ws_summary.append(["Status", "Count", "HR Action"])
    for cell in ws_summary[3]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    action_notes = {
        "MISMATCH": "HR review: correct Other Phone and/or reassign WEB phone site to match employee market",
        "NO_PHONE_FOUND": "Create or rename WEB phone ({Name} Web) and assign correct site",
        "MULTIPLE_PHONES": "Resolve duplicate WEB phones; keep one at correct site",
        "NO_OTHER_PHONE": "Add Other Phone on user record (HR source of truth)",
        "OTHER_PHONE_EXTENSION": "Replace extension with full NANP Other Phone number",
        "INVALID_OTHER_PHONE": "Fix Other Phone format on user record",
        "HQ_PHONE_SITE": "Informational — HQ-area-code staff correctly at Headquarters",
        "OK": "No action needed",
        "UNKNOWN_AREA_CODE": "Add area code to config/area_code_markets.csv",
        "USER_DETAIL_ERROR": "Retry audit or investigate API error",
    }
    for status in sorted(counts, key=lambda s: (0 if s in action_statuses else 1, s)):
        note = action_notes.get(status, "")
        ws_summary.append([status, counts[status], note])
        row_idx = ws_summary.max_row
        if status == "MISMATCH":
            for cell in ws_summary[row_idx]:
                cell.fill = MISMATCH_FILL
        elif status == "OK":
            for cell in ws_summary[row_idx]:
                cell.fill = OK_FILL
    ws_summary.append([])
    ws_summary.append(["Corrective action rows", sum(counts.get(s, 0) for s in action_statuses)])
    ws_summary.append(["Mismatches only", counts.get("MISMATCH", 0)])

    def append_data_sheet(title: str, filtered: list[AuditRow], *, highlight_mismatch: bool = False) -> None:
        sheet = wb.create_sheet(title)
        sheet.append(headers)
        for cell in sheet[1]:
            cell.font = BOLD
            cell.fill = HEADER_FILL
        for row in filtered:
            sheet.append([
                row.status,
                row.name,
                row.email,
                row.title,
                row.other_phone,
                row.area_code,
                row.expected_markets,
                row.expected_cities,
                row.phone_name,
                row.phone_site,
                row.phone_market,
                row.notes,
                row.user_id,
            ])
            if highlight_mismatch and row.status == "MISMATCH":
                for cell in sheet[sheet.max_row]:
                    cell.fill = MISMATCH_FILL
        for column_cells in sheet.columns:
            width = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 48)
        sheet.freeze_panes = "A2"

    action_rows = [row for row in rows if row.status in action_statuses]
    mismatch_rows = [row for row in rows if row.status == "MISMATCH"]
    append_data_sheet("Corrective Action", action_rows, highlight_mismatch=True)
    append_data_sheet("Mismatches Only", mismatch_rows, highlight_mismatch=True)
    append_data_sheet("Audit", rows)

    for sheet in (ws_summary,):
        for column_cells in sheet.columns:
            width = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 48)

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Other Phone area code vs WEB phone site audit")
    parser.add_argument("--markets", default=str(DEFAULT_MARKETS), help="area_code_markets.csv path")
    parser.add_argument("--output", default=None, help="Output .xlsx path")
    parser.add_argument("--include-inactive", action="store_true", help="Include inactive users")
    add_env_arg(parser)
    args = parser.parse_args()

    market_rows = load_market_rows(Path(args.markets))
    if not market_rows:
        raise SystemExit(f"No market rows in {args.markets}")
    code_to_markets, market_to_city = build_area_code_index(market_rows)

    client = create_client(log=tool_log, env_file=args.env_file)
    tool_log("info", f"Loaded {len(market_rows)} markets from {args.markets}")

    tool_log("info", "Loading sites...")
    site_names = load_site_names(client)
    tool_log("info", f"Loaded {len(site_names)} telephony sites")

    tool_log("info", "Loading phones...")
    phones = client.paged_get("/api/v2/telephony/providers/edges/phones")
    phones_by_name = index_phones_by_name(phones)
    tool_log("info", f"Indexed {len(phones)} phones ({len(phones_by_name)} unique names)")

    params = {"state": "any"} if args.include_inactive else {"state": "active"}
    users = client.paged_get("/api/v2/users", params=params)
    tool_log("info", f"Loaded {len(users)} users")

    audit_rows: list[AuditRow] = []
    completed = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_user_detail, client, u["id"]): u for u in users if u.get("id")}
        for future in as_completed(futures):
            completed += 1
            if completed == 1 or completed % 100 == 0:
                tool_log("info", f"User detail {completed}/{len(futures)}")
            summary = futures[future]
            try:
                detail = future.result()
            except Exception as exc:
                audit_rows.append(
                    AuditRow(
                        user_id=safe_str(summary.get("id")),
                        name=safe_str(summary.get("name")),
                        email=safe_str(summary.get("email")),
                        title="",
                        other_phone="",
                        area_code="",
                        expected_markets="",
                        expected_cities="",
                        phone_name="",
                        phone_site="",
                        phone_market="",
                        status="USER_DETAIL_ERROR",
                        notes=str(exc),
                    )
                )
                continue
            audit_rows.append(
                evaluate_user(summary, detail, phones_by_name, site_names, code_to_markets, market_to_city)
            )

    status_order = {
        "MISMATCH": 0,
        "HQ_PHONE_SITE": 1,
        "NO_PHONE_FOUND": 2,
        "UNKNOWN_AREA_CODE": 3,
        "OTHER_PHONE_EXTENSION": 4,
        "INVALID_OTHER_PHONE": 5,
        "NO_OTHER_PHONE": 6,
        "MULTIPLE_PHONES": 7,
        "USER_DETAIL_ERROR": 8,
        "OK": 9,
    }
    audit_rows.sort(key=lambda row: (status_order.get(row.status, 50), row.name.casefold()))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else output_path(f"other_phone_site_audit_{stamp}.xlsx")
    counts: dict[str, int] = defaultdict(int)
    for row in audit_rows:
        counts[row.status] += 1

    meta = {
        "Generated At (UTC)": datetime.now(timezone.utc).isoformat(),
        "Markets File": str(args.markets),
        "Markets Loaded": str(len(market_rows)),
        "Users Scanned": str(len(audit_rows)),
        "Phone Name Pattern": "{User Name} Web (case/space normalized)",
        "Match Rule": "Flags when Other Phone area code market(s) do not match first 3 chars of WEB phone site name",
        "Resolution": "HR determines whether to update Other Phone, WEB phone site, or both — Genesys only surfaces the discrepancy",
        "Purpose": "HR corrective action — verify Other Phone market aligns with WEB phone site assignment",
        "Corrective Action Rows": str(sum(counts.get(s, 0) for s in (
            "MISMATCH", "NO_PHONE_FOUND", "MULTIPLE_PHONES", "NO_OTHER_PHONE",
            "OTHER_PHONE_EXTENSION", "INVALID_OTHER_PHONE", "USER_DETAIL_ERROR",
        ))),
        "Mismatches": str(counts.get("MISMATCH", 0)),
        "OK": str(counts.get("OK", 0)),
        "No Phone Found": str(counts.get("NO_PHONE_FOUND", 0)),
        "No Other Phone": str(counts.get("NO_OTHER_PHONE", 0)),
        "HQ Phone Site": str(counts.get("HQ_PHONE_SITE", 0)),
        "Unknown Area Code": str(counts.get("UNKNOWN_AREA_CODE", 0)),
    }
    write_workbook(out, audit_rows, meta)
    meta_path = out.parent / f"{out.stem}_meta.json"
    meta_path.write_text(json.dumps({"report_path": str(out), **meta}, indent=2), encoding="utf-8")

    tool_log("ok", f"Wrote {out}")
    tool_log("info", f"MISMATCH: {counts.get('MISMATCH', 0)} | OK: {counts.get('OK', 0)} | NO_PHONE: {counts.get('NO_PHONE_FOUND', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
