#!/usr/bin/env python3
# genesys_reassign_phones_to_headquarters.py
#
# Reads audit_sites_to_delete.csv, finds any phones associated to those sites,
# and reassigns them to the "Headquarters" site.
#
# Default behavior: DRY RUN
# Use --apply to make changes.
#
# Input CSV expected:
#   audit_sites_to_delete.csv
#
# Output:
#   reassign_phones_to_headquarters_results.csv
#   reassign_phones_to_headquarters_results.json

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv

load_dotenv("client.env")

CLIENT_ID = os.getenv("GENESYS_CLIENT_ID")
CLIENT_SECRET = os.getenv("GENESYS_CLIENT_SECRET")
REGION = os.getenv("GENESYS_CLOUD_REGION", "usw2.pure.cloud")

TOKEN_URL = f"https://login.{REGION}/oauth/token"
API_BASE = f"https://api.{REGION}"

DEFAULT_INPUT_CSV = "audit_sites_to_delete.csv"
DEFAULT_TARGET_SITE = "Headquarters"
DEFAULT_OUTPUT_CSV = "reassign_phones_to_headquarters_results.csv"
DEFAULT_OUTPUT_JSON = "reassign_phones_to_headquarters_results.json"


def log(level: str, msg: str) -> None:
    print(f"[{level.upper()}] {msg}")


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
            log("WARN", f"{method} {url} hit {r.status_code}; retry {attempt + 1} in {delay:.2f}s")
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


def read_candidate_sites(path: str) -> Tuple[List[Dict[str, str]], set[str]]:
    rows: List[Dict[str, str]] = []
    site_ids: set[str] = set()

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"site_name", "id"}
        headers = set(reader.fieldnames or [])
        if not required.issubset(headers):
            raise ValueError(f"CSV must contain headers {sorted(required)}. Found: {sorted(headers)}")

        for row in reader:
            site_id = (row.get("id") or "").strip()
            site_name = (row.get("site_name") or "").strip()
            if not site_id:
                continue
            rows.append({"site_name": site_name, "id": site_id})
            site_ids.add(site_id)

    return rows, site_ids


def resolve_target_site(sites: List[Dict[str, Any]], target_name: str) -> Dict[str, str]:
    matches = [s for s in sites if str(s.get("name", "")).strip().lower() == target_name.strip().lower()]
    if not matches:
        raise RuntimeError(f'Target site "{target_name}" not found')
    if len(matches) > 1:
        raise RuntimeError(f'Multiple sites named "{target_name}" found; aborting')
    site = matches[0]
    return {"id": str(site["id"]), "name": str(site["name"])}


def get_phone_detail(session: requests.Session, phone_id: str) -> Dict[str, Any]:
    r = request_with_retry(session, "GET", f"{API_BASE}/api/v2/telephony/providers/edges/phones/{phone_id}")
    r.raise_for_status()
    return r.json()


def build_phone_update_payload(phone: Dict[str, Any], target_site: Dict[str, str]) -> Dict[str, Any]:
    payload = json.loads(json.dumps(phone))
    payload["site"] = {"id": target_site["id"], "name": target_site["name"]}
    return payload


def update_phone_site(session: requests.Session, phone_id: str, payload: Dict[str, Any]) -> requests.Response:
    return request_with_retry(
        session,
        "PUT",
        f"{API_BASE}/api/v2/telephony/providers/edges/phones/{phone_id}",
        json=payload,
    )


def write_results_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    fieldnames = [
        "phone_name",
        "phone_id",
        "source_site_name",
        "source_site_id",
        "target_site_name",
        "target_site_id",
        "status",
        "http_status",
        "error_text",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reassign phones from candidate delete sites to Headquarters.")
    parser.add_argument("--csv", default=DEFAULT_INPUT_CSV, help=f"Input audit CSV (default: {DEFAULT_INPUT_CSV})")
    parser.add_argument("--target-site", default=DEFAULT_TARGET_SITE, help=f'Target site name (default: "{DEFAULT_TARGET_SITE}")')
    parser.add_argument("--apply", action="store_true", help="Actually apply phone site changes")
    parser.add_argument("--output-csv", default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON)
    args = parser.parse_args()

    token = get_token()
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })

    candidate_rows, candidate_site_ids = read_candidate_sites(args.csv)
    log("INFO", f"Loaded {len(candidate_rows)} candidate site rows from {args.csv}")

    all_sites = get_all(session, f"{API_BASE}/api/v2/telephony/providers/edges/sites")
    target_site = resolve_target_site(all_sites, args.target_site)
    log("INFO", f'Target site resolved: {target_site["name"]} ({target_site["id"]})')

    all_phones = get_all(session, f"{API_BASE}/api/v2/telephony/providers/edges/phones")
    log("INFO", f"Loaded {len(all_phones)} phones")

    phones_to_move: List[Dict[str, Any]] = []
    for phone in all_phones:
        site = phone.get("site") or {}
        site_id = str(site.get("id", "") or "")
        if site_id and site_id in candidate_site_ids:
            phones_to_move.append(phone)

    log("INFO", f"Found {len(phones_to_move)} phone(s) attached to candidate delete sites")

    results: List[Dict[str, Any]] = []
    for phone in phones_to_move:
        phone_id = str(phone.get("id", "") or "")
        phone_name = str(phone.get("name", "") or phone_id)
        source_site = phone.get("site") or {}
        source_site_id = str(source_site.get("id", "") or "")
        source_site_name = str(source_site.get("name", "") or "")

        row = {
            "phone_name": phone_name,
            "phone_id": phone_id,
            "source_site_name": source_site_name,
            "source_site_id": source_site_id,
            "target_site_name": target_site["name"],
            "target_site_id": target_site["id"],
            "status": "",
            "http_status": "",
            "error_text": "",
        }

        if source_site_id == target_site["id"]:
            row["status"] = "already_on_target"
            log("INFO", f"{phone_name} already on {target_site['name']}")
            results.append(row)
            continue

        detail = get_phone_detail(session, phone_id)
        payload = build_phone_update_payload(detail, target_site)

        if not args.apply:
            row["status"] = "dry_run_ready"
            log("INFO", f"DRY RUN: would move {phone_name} from {source_site_name} to {target_site['name']}")
            results.append(row)
            continue

        r = update_phone_site(session, phone_id, payload)
        row["http_status"] = r.status_code

        if 200 <= r.status_code < 300:
            row["status"] = "updated"
            log("SUCCESS", f"Moved {phone_name} from {source_site_name} to {target_site['name']}")
        else:
            row["status"] = "failed"
            row["error_text"] = (r.text or "").strip()
            log("ERROR", f"Failed moving {phone_name}: HTTP {r.status_code}")
            if row["error_text"]:
                print(row["error_text"])

        results.append(row)

    write_results_csv(args.output_csv, results)
    Path(args.output_json).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    updated = sum(1 for r in results if r["status"] == "updated")
    dry = sum(1 for r in results if r["status"] == "dry_run_ready")
    failed = sum(1 for r in results if r["status"] == "failed")
    log("SUCCESS", f"Done. updated={updated}, dry_run_ready={dry}, failed={failed}")
    log("INFO", f"Wrote {args.output_csv}")
    log("INFO", f"Wrote {args.output_json}")


if __name__ == "__main__":
    main()
