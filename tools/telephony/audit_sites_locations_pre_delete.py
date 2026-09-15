#!/usr/bin/env python3
# genesys_audit_sites_locations_pre_delete_v3.py
#
# Expands the pre-delete audit to check common object connections so you can
# prepare a safe cleanup list.
#
# Input CSV columns expected:
#   site_name
#   location_name
#
# What this checks
# ----------------
# Candidate Locations (not in CSV):
#   - whether any Sites still reference the Location
#
# Candidate Sites (not in CSV):
#   - whether any Phones still reference the Site
#   - whether any Outbound Routes still exist on the Site
#
# Output files:
#   audit_sites_to_delete.csv
#   audit_locations_to_delete.csv
#
# Notes
# -----
# This is still READ-ONLY.
# It is intentionally defensive:
# - if a lookup endpoint fails, the script records the error instead of crashing
# - output includes status flags so you can quickly filter "safe" candidates
#
# This is a preparation/audit script, not a delete script.

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

load_dotenv("client.env")

CLIENT_ID = os.getenv("GENESYS_CLIENT_ID")
CLIENT_SECRET = os.getenv("GENESYS_CLIENT_SECRET")
REGION = os.getenv("GENESYS_CLOUD_REGION", "usw2.pure.cloud")

SITE_COL = "site_name"
LOCATION_COL = "location_name"

TOKEN_URL = f"https://login.{REGION}/oauth/token"
API_BASE = f"https://api.{REGION}"


def log(level: str, message: str) -> None:
    print(f"[{level.upper()}] {message}")


def get_token() -> str:
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("Missing GENESYS_CLIENT_ID or GENESYS_CLIENT_SECRET in client.env")

    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {auth}"},
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def request_with_retry(session: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
    last = None
    for attempt in range(8):
        r = session.request(method, url, timeout=60, **kwargs)
        last = r
        if r.status_code == 429 or r.status_code >= 500:
            delay = min(2 ** attempt, 30) + random.random()
            log("WARN", f"Retry {attempt + 1}: sleeping {delay:.2f}s for {method} {url}")
            time.sleep(delay)
            continue
        return r
    return last


def get_all(session: requests.Session, url: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    page = 1
    while True:
        r = request_with_retry(session, "GET", url, params={"pageSize": 100, "pageNumber": page})
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("entities", []))
        if page >= data.get("pageCount", 1):
            break
        page += 1
    return results


def safe_get_all(session: requests.Session, url: str, label: str) -> tuple[List[Dict[str, Any]], str]:
    try:
        items = get_all(session, url)
        log("INFO", f"Loaded {len(items)} {label}")
        return items, ""
    except Exception as exc:
        err = f"{label} lookup failed: {exc}"
        log("ERROR", err)
        return [], err


def safe_get(session: requests.Session, url: str, label: str) -> tuple[Dict[str, Any] | None, str]:
    try:
        r = request_with_retry(session, "GET", url)
        r.raise_for_status()
        return r.json(), ""
    except Exception as exc:
        err = f"{label} lookup failed: {exc}"
        log("ERROR", err)
        return None, err


def resolve_header(fieldnames: list[str] | None, *candidates: str) -> str | None:
    headers = [h.strip() for h in (fieldnames or []) if h]
    lower_map = {h.casefold(): h for h in headers}
    for candidate in candidates:
        if candidate in headers:
            return candidate
        hit = lower_map.get(candidate.casefold())
        if hit:
            return hit
    return None


def load_keep_lists(csv_file: str) -> tuple[set[str], set[str]]:
    keep_sites: set[str] = set()
    keep_locations: set[str] = set()

    with open(csv_file, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers_found = [h.strip() for h in (reader.fieldnames or [])]
        site_col = resolve_header(reader.fieldnames, SITE_COL, "SiteName", "Site")
        location_col = resolve_header(reader.fieldnames, LOCATION_COL, "LocationName", "Location")

        if not site_col or not location_col:
            raise ValueError(
                f"CSV must contain site and location columns "
                f"({SITE_COL}/{LOCATION_COL} or SiteName/LocationName). Found: {headers_found}"
            )

        for row in reader:
            site = (row.get(site_col) or "").strip()
            location = (row.get(location_col) or "").strip()
            if site:
                keep_sites.add(site)
            if location:
                keep_locations.add(location)

    return keep_sites, keep_locations


def build_location_rows(candidate_locations: List[Dict[str, Any]], all_sites: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for location in candidate_locations:
        location_id = str(location.get("id", "") or "")
        location_name = str(location.get("name", "") or "")

        attached_sites = []
        for site in all_sites:
            site_location = site.get("location") or {}
            if str(site_location.get("id", "") or "") == location_id:
                attached_sites.append(str(site.get("name", "") or ""))

        rows.append({
            "location_name": location_name,
            "id": location_id,
            "attached_sites_count": len(attached_sites),
            "attached_sites": "; ".join(sorted(attached_sites)),
            "has_dependencies": len(attached_sites) > 0,
            "status": "has_site_dependencies" if attached_sites else "ready_for_delete_review",
        })

    return rows


def build_site_rows(candidate_sites: List[Dict[str, Any]], all_phones: List[Dict[str, Any]], session: requests.Session) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for site in candidate_sites:
        site_id = str(site.get("id", "") or "")
        site_name = str(site.get("name", "") or "")

        attached_phones = []
        for phone in all_phones:
            phone_site = phone.get("site") or {}
            if str(phone_site.get("id", "") or "") == site_id:
                attached_phones.append(str(phone.get("name", "") or phone.get("id", "") or ""))

        outbound_routes_url = f"{API_BASE}/api/v2/telephony/providers/edges/sites/{site_id}/outboundroutes"
        outbound_routes, route_err = safe_get_all(session, outbound_routes_url, f"outbound routes for site {site_name}")
        route_names = [str(r.get("name", "") or r.get("id", "") or "") for r in outbound_routes]

        has_dependencies = bool(attached_phones or outbound_routes)

        rows.append({
            "site_name": site_name,
            "id": site_id,
            "attached_phones_count": len(attached_phones),
            "attached_phones": "; ".join(sorted(attached_phones)),
            "outbound_routes_count": len(outbound_routes),
            "outbound_routes": "; ".join(sorted(route_names)),
            "lookup_error": route_err,
            "has_dependencies": has_dependencies or bool(route_err),
            "status": (
                "has_dependencies"
                if has_dependencies
                else ("lookup_error" if route_err else "ready_for_delete_review")
            ),
        })

    return rows


def write_csv(path: str, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(csv_file: str) -> None:
    token = get_token()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    keep_sites, keep_locations = load_keep_lists(csv_file)
    log("INFO", f"Loaded {len(keep_sites)} sites and {len(keep_locations)} locations from CSV")

    all_sites, sites_err = safe_get_all(session, f"{API_BASE}/api/v2/telephony/providers/edges/sites", "sites")
    all_locations, locations_err = safe_get_all(session, f"{API_BASE}/api/v2/locations", "locations")
    all_phones, phones_err = safe_get_all(session, f"{API_BASE}/api/v2/telephony/providers/edges/phones", "phones")

    if sites_err or locations_err:
        raise RuntimeError("Unable to continue because required site/location lookups failed")

    candidate_sites = [s for s in all_sites if str(s.get("name", "") or "") not in keep_sites]
    candidate_locations = [l for l in all_locations if str(l.get("name", "") or "") not in keep_locations]

    log("INFO", f"Candidate sites not in CSV: {len(candidate_sites)}")
    log("INFO", f"Candidate locations not in CSV: {len(candidate_locations)}")

    location_rows = build_location_rows(candidate_locations, all_sites)
    site_rows = build_site_rows(candidate_sites, all_phones, session)

    if phones_err:
        for row in site_rows:
            if row["status"] == "ready_for_delete_review":
                row["status"] = "lookup_error"
            row["lookup_error"] = "; ".join(x for x in [row.get("lookup_error", ""), phones_err] if x)
            row["has_dependencies"] = True

    write_csv(
        "audit_locations_to_delete.csv",
        location_rows,
        [
            "location_name",
            "id",
            "attached_sites_count",
            "attached_sites",
            "has_dependencies",
            "status",
        ],
    )

    write_csv(
        "audit_sites_to_delete.csv",
        site_rows,
        [
            "site_name",
            "id",
            "attached_phones_count",
            "attached_phones",
            "outbound_routes_count",
            "outbound_routes",
            "lookup_error",
            "has_dependencies",
            "status",
        ],
    )

    Path("audit_locations_to_delete.json").write_text(
        json.dumps(location_rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    Path("audit_sites_to_delete.json").write_text(
        json.dumps(site_rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    ready_locations = sum(1 for r in location_rows if r["status"] == "ready_for_delete_review")
    ready_sites = sum(1 for r in site_rows if r["status"] == "ready_for_delete_review")

    log("SUCCESS", f"Locations exported: {len(location_rows)} total, {ready_locations} with no detected dependencies")
    log("SUCCESS", f"Sites exported: {len(site_rows)} total, {ready_sites} with no detected dependencies")
    log("INFO", "Wrote audit_locations_to_delete.csv / .json")
    log("INFO", "Wrote audit_sites_to_delete.csv / .json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="Authoritative keep-list CSV with site_name and location_name columns")
    args = parser.parse_args()
    main(args.csv)
