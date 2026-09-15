#!/usr/bin/env python3
"""
Genesys Cloud cleanup attachment audit

Purpose
-------
Given an authoritative CSV of locations/sites that should remain, find Genesys Cloud
locations and sites that are *not* present in that CSV and export the attachments that
still reference them so cleanup can be planned safely.

Outputs
-------
Creates an .xlsx workbook with these sheets:
- Keep_Sets
- Extra_Locations
- Extra_Sites
- Extra_Location_to_Site
- Extra_Site_Phones
- Extra_Site_NumberPlans
- Extra_Site_OutboundRoutes
- API_Warnings

Credentials
-----------
Reads OAuth client credentials from environment variables:
- GENESYS_CLIENT_ID
- GENESYS_CLIENT_SECRET

Defaults to region us-west-2 / api base https://api.usw2.pure.cloud
You can override with --api-base.

Notes
-----
- This script is intentionally read-only.
- It uses defensive pagination and retries on HTTP 429 / 5xx.
- It tries to be tolerant of schema drift by using .get() lookups everywhere.
"""

from __future__ import annotations

import argparse

from pathlib import Path

def load_client_env(explicit_path: str | None = None) -> dict:
    """
    Load Genesys OAuth settings from a simple KEY=VALUE client.env file.
    Search order:
      1. --env-file path, if provided
      2. client.env next to this script
      3. client.env in current working directory
      4. process environment variables as fallback
    """
    candidates = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    candidates.append(Path(__file__).with_name("client.env"))
    candidates.append(Path.cwd() / "client.env")

    env_map = {}
    chosen = None

    for candidate in candidates:
        if candidate.exists():
            chosen = candidate
            for raw_line in candidate.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env_map[key.strip()] = value.strip().strip('"').strip("'")
            break

    # Fall back to process env for anything not present in file
    import os
    for key in [
        "GENESYS_CLIENT_ID",
        "GENESYS_CLIENT_SECRET",
        "GENESYS_REGION",
        "GENESYS_API_BASE",
        "GENESYS_LOGIN_BASE",
    ]:
        if key not in env_map and os.getenv(key):
            env_map[key] = os.getenv(key)

    env_map["_loaded_from"] = str(chosen) if chosen else "(process environment only)"
    return env_map

import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd
import requests

DEFAULT_API_BASE = "https://api.usw2.pure.cloud"
DEFAULT_LOGIN_BASE = "https://login.usw2.pure.cloud"


@dataclass
class WarningRow:
    category: str
    object_type: str
    object_name: str
    object_id: str
    detail: str


class GenesysClient:
    def __init__(self, client_id: str, client_secret: str, api_base: str, login_base: str, timeout: int = 60):
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_base = api_base.rstrip("/")
        self.login_base = login_base.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.token: Optional[str] = None

    def authenticate(self) -> None:
        resp = self.session.post(
            f"{self.login_base}/oauth/token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self.token = data["access_token"]
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.api_base}{path}"
        retries = 6
        for attempt in range(1, retries + 1):
            resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
            if resp.status_code == 401 and attempt == 1:
                self.authenticate()
                continue
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                sleep_for = float(retry_after) if retry_after else min(2 ** attempt, 30)
                time.sleep(sleep_for)
                continue
            if 500 <= resp.status_code < 600 and attempt < retries:
                time.sleep(min(2 ** attempt, 30))
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp

    def get_paginated_entities(
        self,
        path: str,
        *,
        entity_key: Optional[str] = None,
        page_size: int = 100,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        params = dict(extra_params or {})
        params.setdefault("pageSize", page_size)
        page_number = 1
        results: List[Dict[str, Any]] = []

        while True:
            params["pageNumber"] = page_number
            resp = self.request("GET", path, params=params)
            payload = resp.json()

            if isinstance(payload, list):
                results.extend(item for item in payload if isinstance(item, dict))
                break

            if entity_key:
                page_items = payload.get(entity_key, [])
            else:
                page_items = (
                    payload.get("entities")
                    or payload.get("items")
                    or payload.get("results")
                    or []
                )

            if not isinstance(page_items, list):
                raise RuntimeError(f"Unexpected payload shape for {path}: missing list entity collection")

            results.extend(page_items)

            page_count = payload.get("pageCount") if isinstance(payload, dict) else None
            total = payload.get("total") if isinstance(payload, dict) else None
            if page_count is not None:
                if page_number >= int(page_count):
                    break
            else:
                if not page_items:
                    break
                if total is not None and len(results) >= int(total):
                    break
                if len(page_items) < int(params["pageSize"]):
                    break

            page_number += 1

        return results


def clean_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_key(value: Any) -> str:
    return clean_str(value).casefold()


def pick_first_present(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    normalized = {c.casefold(): c for c in df.columns}
    for candidate in candidates:
        real = normalized.get(candidate.casefold())
        if real:
            return real
    return None


def build_keep_sets(df: pd.DataFrame, location_col: Optional[str], site_col: Optional[str]) -> Tuple[Set[str], Set[str]]:
    keep_locations: Set[str] = set()
    keep_sites: Set[str] = set()

    if location_col:
        keep_locations = {normalize_key(v) for v in df[location_col].dropna().tolist() if clean_str(v)}
    if site_col:
        keep_sites = {normalize_key(v) for v in df[site_col].dropna().tolist() if clean_str(v)}

    # If only one of the two exists and your environment uses identical location/site names,
    # mirror the keep-set so either artifact can still be matched safely.
    if keep_locations and not keep_sites:
        keep_sites = set(keep_locations)
    if keep_sites and not keep_locations:
        keep_locations = set(keep_sites)

    return keep_locations, keep_sites


def flatten_address(address: Dict[str, Any]) -> str:
    if not isinstance(address, dict):
        return ""
    parts = [
        clean_str(address.get("street1")),
        clean_str(address.get("street2")),
        clean_str(address.get("city")),
        clean_str(address.get("state")),
        clean_str(address.get("zipCode") or address.get("zipcode")),
        clean_str(address.get("country")),
    ]
    return ", ".join([p for p in parts if p])


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit extra Genesys Cloud sites/locations and their attachments.")
    parser.add_argument("--csv", required=True, help="Authoritative main CSV path.")
    parser.add_argument("--out", default="genesys_cleanup_attachment_audit.xlsx", help="Output xlsx path.")
    parser.add_argument("--location-column", default="Location", help="CSV column containing location names/codes.")
    parser.add_argument("--site-column", default="Site", help="CSV column containing site names/codes.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="Genesys Cloud API base URL.")
    parser.add_argument("--login-base", default=DEFAULT_LOGIN_BASE, help="Genesys Cloud login base URL.")
    parser.add_argument("--page-size", type=int, default=100, help="Page size for paginated API fetches.")
    args = parser.parse_args()

    client_id = os.getenv("GENESYS_CLIENT_ID")
    client_secret = os.getenv("GENESYS_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("Missing GENESYS_CLIENT_ID and/or GENESYS_CLIENT_SECRET environment variables.", file=sys.stderr)
        return 2

    main_df = pd.read_csv(args.csv, dtype=str).fillna("")
    location_col = pick_first_present(main_df, [args.location_column, "Location", "LocationName", "location_name"])
    site_col = pick_first_present(main_df, [args.site_column, "Site", "SiteName", "site_name"])

    if not location_col and not site_col:
        print(
            "Could not find either a location or site column in the CSV. "
            "Use --location-column and/or --site-column.",
            file=sys.stderr,
        )
        return 2

    keep_locations, keep_sites = build_keep_sets(main_df, location_col, site_col)

    gc = GenesysClient(client_id, client_secret, api_base=args.api_base, login_base=args.login_base)
    gc.authenticate()

    warnings: List[WarningRow] = []

    locations = gc.get_paginated_entities("/api/v2/locations", page_size=args.page_size)
    sites = gc.get_paginated_entities("/api/v2/telephony/providers/edges/sites", page_size=args.page_size)
    phones = gc.get_paginated_entities("/api/v2/telephony/providers/edges/phones", page_size=args.page_size)

    extra_locations = [loc for loc in locations if normalize_key(loc.get("name")) not in keep_locations]
    extra_sites = [site for site in sites if normalize_key(site.get("name")) not in keep_sites]

    extra_location_ids = {clean_str(loc.get("id")) for loc in extra_locations}
    extra_site_ids = {clean_str(site.get("id")) for site in extra_sites}

    # Map related sites to locations.
    extra_location_to_site_rows: List[Dict[str, Any]] = []
    for site in sites:
        loc = site.get("location") or {}
        loc_id = clean_str(loc.get("id"))
        if loc_id in extra_location_ids or clean_str(site.get("id")) in extra_site_ids:
            extra_location_to_site_rows.append(
                {
                    "LocationName": clean_str(loc.get("name")),
                    "LocationId": loc_id,
                    "SiteName": clean_str(site.get("name")),
                    "SiteId": clean_str(site.get("id")),
                    "SiteState": clean_str(site.get("state")),
                    "MediaModel": clean_str(site.get("mediaModel")),
                    "Managed": site.get("managed"),
                }
            )

    # Phones attached to extra sites.
    extra_site_phone_rows: List[Dict[str, Any]] = []
    for phone in phones:
        site = phone.get("site") or {}
        site_id = clean_str(site.get("id"))
        if site_id in extra_site_ids:
            line_base = None
            lines = phone.get("lines") or []
            if isinstance(lines, list) and lines:
                line_base = lines[0]
            owner = phone.get("owner") or phone.get("assignedUser") or {}
            extra_site_phone_rows.append(
                {
                    "SiteName": clean_str(site.get("name")),
                    "SiteId": site_id,
                    "PhoneName": clean_str(phone.get("name")),
                    "PhoneId": clean_str(phone.get("id")),
                    "PhoneState": clean_str(phone.get("state")),
                    "PhoneType": clean_str(phone.get("phoneMetaBase") or phone.get("type")),
                    "WebRtcUser": clean_str((phone.get("webRtcUser") or {}).get("id")),
                    "OwnerName": clean_str(owner.get("name")),
                    "OwnerId": clean_str(owner.get("id")),
                    "PrimaryLineId": clean_str((line_base or {}).get("id")),
                    "PrimaryLineName": clean_str((line_base or {}).get("name")),
                }
            )

    # Child collections per extra site.
    extra_site_numberplan_rows: List[Dict[str, Any]] = []
    extra_site_outbound_route_rows: List[Dict[str, Any]] = []

    for site in extra_sites:
        site_id = clean_str(site.get("id"))
        site_name = clean_str(site.get("name"))

        try:
            number_plans = gc.get_paginated_entities(
                f"/api/v2/telephony/providers/edges/sites/{site_id}/numberplans",
                page_size=args.page_size,
            )
            for np in number_plans:
                extra_site_numberplan_rows.append(
                    {
                        "SiteName": site_name,
                        "SiteId": site_id,
                        "NumberPlanId": clean_str(np.get("id")),
                        "NumberPlanName": clean_str(np.get("name")),
                        "Classification": clean_str(np.get("classification")),
                        "MatchType": clean_str(np.get("matchType")),
                        "NormalizedFormat": clean_str(np.get("normalizedFormat")),
                    }
                )
        except requests.HTTPError as exc:
            warnings.append(
                WarningRow(
                    category="child_fetch_failed",
                    object_type="site",
                    object_name=site_name,
                    object_id=site_id,
                    detail=f"numberplans fetch failed: {exc}",
                )
            )

        try:
            outbound_routes = gc.get_paginated_entities(
                f"/api/v2/telephony/providers/edges/sites/{site_id}/outboundroutes",
                page_size=args.page_size,
            )
            for route in outbound_routes:
                trunk_bases = route.get("externalTrunkBases") or []
                extra_site_outbound_route_rows.append(
                    {
                        "SiteName": site_name,
                        "SiteId": site_id,
                        "RouteId": clean_str(route.get("id")),
                        "RouteName": clean_str(route.get("name")),
                        "Enabled": route.get("enabled"),
                        "Distribution": clean_str(route.get("distribution")),
                        "ClassificationTypes": ", ".join(route.get("classificationTypes") or []),
                        "ExternalTrunkBases": ", ".join(
                            clean_str(tb.get("name")) for tb in trunk_bases if isinstance(tb, dict)
                        ),
                    }
                )
        except requests.HTTPError as exc:
            warnings.append(
                WarningRow(
                    category="child_fetch_failed",
                    object_type="site",
                    object_name=site_name,
                    object_id=site_id,
                    detail=f"outboundroutes fetch failed: {exc}",
                )
            )

    extra_location_rows: List[Dict[str, Any]] = []
    for loc in extra_locations:
        emergency = loc.get("emergencyNumber") or {}
        extra_location_rows.append(
            {
                "LocationName": clean_str(loc.get("name")),
                "LocationId": clean_str(loc.get("id")),
                "Notes": clean_str(loc.get("notes")),
                "Address": flatten_address(loc.get("address") or {}),
                "EmergencyNumber": clean_str(emergency.get("number") or emergency.get("e164") or emergency.get("phoneNumber")),
                "EmergencyType": clean_str(emergency.get("type")),
                "Version": loc.get("version"),
            }
        )

    extra_site_rows: List[Dict[str, Any]] = []
    for site in extra_sites:
        loc = site.get("location") or {}
        contact_user = site.get("contactUser") or {}
        extra_site_rows.append(
            {
                "SiteName": clean_str(site.get("name")),
                "SiteId": clean_str(site.get("id")),
                "LocationName": clean_str(loc.get("name")),
                "LocationId": clean_str(loc.get("id")),
                "State": clean_str(site.get("state")),
                "MediaModel": clean_str(site.get("mediaModel")),
                "Managed": site.get("managed"),
                "CallerName": clean_str(site.get("callerName")),
                "ContactUserName": clean_str(contact_user.get("name")),
                "ContactUserId": clean_str(contact_user.get("id")),
            }
        )

    keep_rows = []
    max_keep_len = max(len(keep_locations), len(keep_sites), 1)
    loc_list = sorted(keep_locations)
    site_list = sorted(keep_sites)
    for i in range(max_keep_len):
        keep_rows.append(
            {
                "KeepLocation(normalized)": loc_list[i] if i < len(loc_list) else "",
                "KeepSite(normalized)": site_list[i] if i < len(site_list) else "",
            }
        )

    warning_rows = [w.__dict__ for w in warnings]

    with pd.ExcelWriter(args.out, engine="openpyxl") as writer:
        pd.DataFrame(keep_rows).to_excel(writer, sheet_name="Keep_Sets", index=False)
        pd.DataFrame(extra_location_rows).sort_values(by=["LocationName", "LocationId"], na_position="last").to_excel(
            writer, sheet_name="Extra_Locations", index=False
        )
        pd.DataFrame(extra_site_rows).sort_values(by=["SiteName", "SiteId"], na_position="last").to_excel(
            writer, sheet_name="Extra_Sites", index=False
        )
        pd.DataFrame(extra_location_to_site_rows).sort_values(
            by=["LocationName", "SiteName"], na_position="last"
        ).to_excel(writer, sheet_name="Extra_Location_to_Site", index=False)
        pd.DataFrame(extra_site_phone_rows).sort_values(
            by=["SiteName", "PhoneName"], na_position="last"
        ).to_excel(writer, sheet_name="Extra_Site_Phones", index=False)
        pd.DataFrame(extra_site_numberplan_rows).sort_values(
            by=["SiteName", "NumberPlanName"], na_position="last"
        ).to_excel(writer, sheet_name="Extra_Site_NumberPlans", index=False)
        pd.DataFrame(extra_site_outbound_route_rows).sort_values(
            by=["SiteName", "RouteName"], na_position="last"
        ).to_excel(writer, sheet_name="Extra_Site_OutboundRoutes", index=False)
        pd.DataFrame(warning_rows).to_excel(writer, sheet_name="API_Warnings", index=False)

        # Basic autosize.
        for ws in writer.book.worksheets:
            for column_cells in ws.columns:
                length = max((len(clean_str(cell.value)) for cell in column_cells), default=0)
                ws.column_dimensions[column_cells[0].column_letter].width = min(max(length + 2, 12), 60)
            ws.freeze_panes = "A2"

    print(f"Wrote audit workbook: {args.out}")
    print(f"Extra locations found: {len(extra_location_rows)}")
    print(f"Extra sites found: {len(extra_site_rows)}")
    print(f"Extra-site phones found: {len(extra_site_phone_rows)}")
    print(f"Extra site/location relationships found: {len(extra_location_to_site_rows)}")
    if warnings:
        print(f"Warnings captured: {len(warnings)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
