#!/usr/bin/env python3
"""Read-only org cleanup audit: queues, sites, locations, routes, flows, and related orphans."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log
from core.genesys_client import safe_str

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
except ImportError as exc:  # pragma: no cover
    raise SystemExit("openpyxl is required: pip install openpyxl") from exc

HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
ACTION_FILL = PatternFill("solid", fgColor="F4CCCC")
BOLD = Font(bold=True)
SKIP_SITE_NAMES = frozenset({"PureCloud Voice - AWS"})


@dataclass
class Row:
    category: str
    status: str
    name: str
    object_id: str
    division: str
    detail: str
    dependencies: str
    recommended_action: str


def division_name(obj: dict) -> str:
    division = obj.get("division") or {}
    return safe_str(division.get("name"))


def location_ref(obj: dict) -> tuple[str, str]:
    location = obj.get("location") or {}
    return safe_str(location.get("id")), safe_str(location.get("name"))


def write_sheet(wb: Workbook, title: str, headers: list[str], rows: list[list]) -> None:
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.font = BOLD
        cell.fill = HEADER_FILL
    for row in rows:
        ws.append(row)
    for column_cells in ws.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 12), 56)
    ws.freeze_panes = "A2"


def audit_rows_to_lists(rows: list[Row]) -> list[list]:
    return [
        [
            row.category,
            row.status,
            row.name,
            row.object_id,
            row.division,
            row.detail,
            row.dependencies,
            row.recommended_action,
        ]
        for row in rows
    ]


def fetch_outbound_routes(client, site_id: str) -> tuple[list[dict], str]:
    try:
        routes = client.paged_get(f"/api/v2/telephony/providers/edges/sites/{site_id}/outboundroutes")
        return routes, ""
    except Exception as exc:
        return [], str(exc)


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Org cleanup audit (read-only)")
    parser.add_argument("--output", default=None, help="Output .xlsx path")
    add_env_arg(parser)
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    headers = [
        "Category",
        "Status",
        "Name",
        "ID",
        "Division",
        "Detail",
        "Dependencies",
        "Recommended Action",
    ]
    findings: list[Row] = []
    warnings: list[list[str]] = []

    tool_log("info", "Loading org inventory...")
    queues = client.paged_get("/api/v2/routing/queues")
    sites = client.paged_get("/api/v2/telephony/providers/edges/sites")
    locations = client.paged_get("/api/v2/locations")
    phones = client.paged_get("/api/v2/telephony/providers/edges/phones")
    flows = client.paged_get("/api/v2/flows")
    groups = client.paged_get("/api/v2/groups")
    wrapups = client.paged_get("/api/v2/routing/wrapupcodes")

    phones_by_site: dict[str, list[str]] = defaultdict(list)
    for phone in phones:
        site = phone.get("site") or {}
        site_id = safe_str(site.get("id"))
        if site_id:
            phones_by_site[site_id].append(safe_str(phone.get("name")))

    sites_by_location: dict[str, list[str]] = defaultdict(list)
    for site in sites:
        loc_id, loc_name = location_ref(site)
        label = safe_str(site.get("name"))
        if loc_id:
            sites_by_location[loc_id].append(label)
        elif label not in SKIP_SITE_NAMES:
            findings.append(
                Row(
                    category="Sites",
                    status="NO_LOCATION",
                    name=label,
                    object_id=safe_str(site.get("id")),
                    division="",
                    detail="Telephony site has no location assigned",
                    dependencies=f"Phones attached: {len(phones_by_site.get(safe_str(site.get('id')), []))}",
                    recommended_action="Link site to a location or confirm core/system site can remain as-is",
                )
            )

    location_names = {safe_str(loc.get("id")): safe_str(loc.get("name")) for loc in locations}
    for loc in locations:
        loc_id = safe_str(loc.get("id"))
        loc_name = safe_str(loc.get("name"))
        attached = sites_by_location.get(loc_id, [])
        if not attached:
            findings.append(
                Row(
                    category="Locations",
                    status="NO_SITES",
                    name=loc_name,
                    object_id=loc_id,
                    division="",
                    detail="No telephony sites reference this location",
                    dependencies="",
                    recommended_action="Review for deletion if location was retired with its centers",
                )
            )

    tool_log("info", "Checking queues...")
    referenced_flow_ids: set[str] = set()
    for queue in queues:
        qid = safe_str(queue.get("id"))
        qname = safe_str(queue.get("name"))
        div = division_name(queue)
        member_count = int(queue.get("memberCount") or 0)
        joined_count = int(queue.get("joinedMemberCount") or 0)
        user_count = int(queue.get("userMemberCount") or 0)

        flow = queue.get("queueFlow") or {}
        flow_id = safe_str(flow.get("id"))
        flow_name = safe_str(flow.get("name"))
        if flow_id:
            referenced_flow_ids.add(flow_id)
        else:
            findings.append(
                Row(
                    category="Queues",
                    status="NO_INQUEUE_FLOW",
                    name=qname,
                    object_id=qid,
                    division=div,
                    detail="Queue has no in-queue flow assigned",
                    dependencies=f"memberCount={member_count}, joined={joined_count}",
                    recommended_action="Assign an in-queue flow or retire the queue",
                )
            )

        if member_count == 0:
            findings.append(
                Row(
                    category="Queues",
                    status="ZERO_MEMBERS",
                    name=qname,
                    object_id=qid,
                    division=div,
                    detail="Queue has zero members (users or groups)",
                    dependencies=f"in-queue flow: {flow_name or 'none'}",
                    recommended_action="Confirm queue is retired; remove or repurpose if obsolete",
                )
            )
        elif joined_count == 0 and user_count == 0:
            findings.append(
                Row(
                    category="Queues",
                    status="NO_JOINED_OR_USER_MEMBERS",
                    name=qname,
                    object_id=qid,
                    division=div,
                    detail="Queue has group-only or inactive memberships; no joined/user members",
                    dependencies=f"memberCount={member_count}",
                    recommended_action="Review membership — queue may be configured but unused",
                )
            )

    tool_log("info", "Checking flows...")
    for flow in flows:
        fid = safe_str(flow.get("id"))
        fname = safe_str(flow.get("name"))
        ftype = safe_str(flow.get("type"))
        div = division_name(flow)
        is_system = bool(flow.get("system"))
        is_deleted = bool(flow.get("deleted"))
        is_active = bool(flow.get("active", True))

        if is_deleted:
            continue
        if is_system:
            continue
        if fid not in referenced_flow_ids:
            findings.append(
                Row(
                    category="Flows",
                    status="UNREFERENCED_BY_QUEUE",
                    name=fname,
                    object_id=fid,
                    division=div,
                    detail=f"Architect flow type={ftype} is not referenced by any queue queueFlow",
                    dependencies="",
                    recommended_action="Review in Architect; archive/delete if obsolete",
                )
            )
        if not is_active:
            findings.append(
                Row(
                    category="Flows",
                    status="INACTIVE",
                    name=fname,
                    object_id=fid,
                    division=div,
                    detail=f"Flow type={ftype} is marked inactive",
                    dependencies="",
                    recommended_action="Confirm whether inactive flow can be removed",
                )
            )

    tool_log("info", "Checking outbound routes and site phone attachments...")
    for site in sites:
        site_id = safe_str(site.get("id"))
        site_name = safe_str(site.get("name"))
        if site_name in SKIP_SITE_NAMES:
            continue
        loc_id, loc_name = location_ref(site)
        phone_names = phones_by_site.get(site_id, [])
        routes, route_err = fetch_outbound_routes(client, site_id)
        if route_err:
            warnings.append(["OutboundRoutes", site_name, site_id, route_err])

        if not phone_names and routes:
            for route in routes:
                findings.append(
                    Row(
                        category="Outbound Routes",
                        status="SITE_HAS_NO_PHONES",
                        name=safe_str(route.get("name")) or f"route@{site_name}",
                        object_id=safe_str(route.get("id")),
                        division="",
                        detail=f"Outbound route on site '{site_name}' which has zero phones",
                        dependencies=f"location={loc_name or 'none'}",
                        recommended_action="Review route — likely unused if site has no phones",
                    )
                )

        if not phone_names and not routes and loc_id:
            findings.append(
                Row(
                    category="Sites",
                    status="NO_PHONES_NO_ROUTES",
                    name=site_name,
                    object_id=site_id,
                    division="",
                    detail="Site has location but no phones and no outbound routes",
                    dependencies=f"location={loc_name}",
                    recommended_action="Candidate for site cleanup if center is closed",
                )
            )

    tool_log("info", "Checking groups...")
    for group in groups:
        gid = safe_str(group.get("id"))
        gname = safe_str(group.get("name"))
        member_count = int(group.get("memberCount") or 0)
        if member_count == 0:
            findings.append(
                Row(
                    category="Groups",
                    status="ZERO_MEMBERS",
                    name=gname,
                    object_id=gid,
                    division=division_name(group),
                    detail="Group has zero members",
                    dependencies="",
                    recommended_action="Review whether empty group can be deleted",
                )
            )

    tool_log("info", "Checking wrap-up code usage...")
    wrapup_ids = {safe_str(w.get("id")): safe_str(w.get("name")) for w in wrapups}
    wrapup_usage: dict[str, set[str]] = defaultdict(set)
    for index, queue in enumerate(queues, 1):
        if index == 1 or index % 50 == 0 or index == len(queues):
            tool_log("info", f"Wrap-up scan {index}/{len(queues)} queues")
        qid = safe_str(queue.get("id"))
        qname = safe_str(queue.get("name"))
        try:
            assigned = client.paged_get(f"/api/v2/routing/queues/{qid}/wrapupcodes")
        except Exception as exc:
            warnings.append(["WrapupCodes", qname, qid, str(exc)])
            continue
        for code in assigned:
            cid = safe_str(code.get("id"))
            if cid:
                wrapup_usage[cid].add(qname)

    for cid, cname in wrapup_ids.items():
        if cid not in wrapup_usage:
            findings.append(
                Row(
                    category="Wrap-up Codes",
                    status="UNASSIGNED",
                    name=cname,
                    object_id=cid,
                    division="",
                    detail="Wrap-up code is not assigned to any queue",
                    dependencies="",
                    recommended_action="Review for deletion if no longer used in reporting",
                )
            )

    findings.sort(key=lambda row: (row.category, row.status, row.name.casefold()))

    category_counts: dict[str, int] = defaultdict(int)
    status_counts: dict[str, int] = defaultdict(int)
    for row in findings:
        category_counts[row.category] += 1
        status_counts[f"{row.category}:{row.status}"] += 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = Path(args.output) if args.output else output_path(f"org_cleanup_audit_{stamp}.xlsx")

    wb = Workbook()
    ws_about = wb.active
    ws_about.title = "About"
    meta = {
        "Generated At (UTC)": datetime.now(timezone.utc).isoformat(),
        "Purpose": "Informational org cleanup candidates — no changes made",
        "Queues Scanned": str(len(queues)),
        "Sites Scanned": str(len(sites)),
        "Locations Scanned": str(len(locations)),
        "Phones Scanned": str(len(phones)),
        "Flows Scanned": str(len(flows)),
        "Groups Scanned": str(len(groups)),
        "Wrap-up Codes Scanned": str(len(wrapups)),
        "Total Findings": str(len(findings)),
        "API Warnings": str(len(warnings)),
    }
    ws_about.append(["Field", "Value"])
    for key, value in meta.items():
        ws_about.append([key, value])

    ws_summary = wb.create_sheet("Summary")
    ws_summary.append(["Org Cleanup Audit Summary"])
    ws_summary["A1"].font = BOLD
    ws_summary.append([])
    ws_summary.append(["Inventory", "Count"])
    for label, count in [
        ("Queues", len(queues)),
        ("Sites", len(sites)),
        ("Locations", len(locations)),
        ("Phones", len(phones)),
        ("Architect Flows", len(flows)),
        ("Groups", len(groups)),
        ("Wrap-up Codes", len(wrapups)),
    ]:
        ws_summary.append([label, count])
    ws_summary.append([])
    ws_summary.append(["Findings by Category", "Count"])
    for category in sorted(category_counts):
        ws_summary.append([category, category_counts[category]])
    ws_summary.append([])
    ws_summary.append(["Notable Statuses", "Count"])
    for key in sorted(status_counts):
        ws_summary.append([key, status_counts[key]])

    write_sheet(wb, "All Findings", headers, audit_rows_to_lists(findings))

    priority_statuses = {
        "ZERO_MEMBERS",
        "NO_LOCATION",
        "NO_SITES",
        "UNREFERENCED_BY_QUEUE",
        "SITE_HAS_NO_PHONES",
        "NO_PHONES_NO_ROUTES",
        "UNASSIGNED",
    }
    priority = [row for row in findings if row.status in priority_statuses]
    write_sheet(wb, "Priority Review", headers, audit_rows_to_lists(priority))

    category_sheets = {
        "Queues": "Queues",
        "Sites": "Sites_Locations",
        "Locations": "Sites_Locations",
        "Outbound Routes": "Outbound_Routes",
        "Flows": "Flows",
        "Groups": "Groups",
        "Wrap-up Codes": "Wrapup_Codes",
    }
    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in findings:
        sheet = category_sheets.get(row.category, "Other")
        grouped[sheet].append(row)
    for sheet_name, rows in grouped.items():
        if sheet_name == "Sites_Locations":
            continue
        write_sheet(wb, sheet_name, headers, audit_rows_to_lists(rows))

    site_loc_rows = [row for row in findings if row.category in {"Sites", "Locations"}]
    if site_loc_rows:
        write_sheet(wb, "Sites_Locations", headers, audit_rows_to_lists(site_loc_rows))

    if warnings:
        write_sheet(wb, "API_Warnings", ["Category", "Object", "ID", "Error"], warnings)

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    meta_path = out.parent / f"{out.stem}_meta.json"
    meta_path.write_text(json.dumps({"report_path": str(out), **meta}, indent=2), encoding="utf-8")

    tool_log("ok", f"Wrote {out}")
    tool_log("info", f"Findings: {len(findings)} | Priority: {len(priority)} | Warnings: {len(warnings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
