#!/usr/bin/env python3
# genesys_role_assign_v6_7.py
# Version: 6.7
# - Supports "All Divisions" add/remove via divisionId="*"
# - Supports multiple values per flag, e.g. --division "A" "B" and --remdiv "X" "Y"
# - When removing "All Divisions", automatically adds Home division for each current role
#   so role access is retained instead of stripping effective role assignments

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import csv
import json
import os
from typing import Dict, Iterable, List, Sequence, Set, Tuple, Union

import requests
import PureCloudPlatformClientV2

from core.cli import setup_path
from core.config import get_api_host, get_client_credentials, load_client_env

setup_path()
load_client_env()

CLIENT_ID, CLIENT_SECRET = get_client_credentials()
BASE_URL = get_api_host()

api_client = PureCloudPlatformClientV2.api_client.ApiClient()
api_client.host = BASE_URL

token = api_client.get_client_credentials_token(CLIENT_ID, CLIENT_SECRET)
ACCESS_TOKEN = token.access_token

users_api = PureCloudPlatformClientV2.UsersApi(api_client)

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

ALL_DIVISIONS_SENTINELS = {
    "*",
    "all divisions",
    "all division",
    "all_divisions",
    "all-divisions",
}

DEFAULT_FALLBACK_DIVISION_NAME = "Home"


def genesys_request(method: str, path: str, **kwargs):
    url = f"{BASE_URL}{path}"
    resp = requests.request(method, url, headers=HEADERS, timeout=60, **kwargs)

    if not resp.ok:
        print(f"[ERROR] {method} {path} -> {resp.status_code}")
        print(resp.text)
        resp.raise_for_status()

    if resp.text.strip():
        return resp.json()
    return None


def chunked(seq: Sequence[dict], size: int) -> Iterable[Sequence[dict]]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def flatten_arg_values(values: Sequence[Union[Sequence[str], str]]) -> List[str]:
    flattened: List[str] = []
    for value in values:
        if isinstance(value, str):
            clean = value.strip()
            if clean:
                flattened.append(clean)
            continue
        for item in value:
            clean = str(item).strip()
            if clean:
                flattened.append(clean)
    return flattened


def is_all_divisions_value(value: str) -> bool:
    return value.strip().lower() in ALL_DIVISIONS_SENTINELS


def get_user(email: str):
    body = {
        "query": [{
            "type": "TERM",
            "fields": ["email"],
            "value": email
        }]
    }
    res = users_api.post_users_search(body)
    return res.results[0] if res.results else None


def get_divisions() -> Dict[str, str]:
    divisions = {}
    page_number = 1

    while True:
        data = genesys_request(
            "GET",
            f"/api/v2/authorization/divisions?pageSize=100&pageNumber={page_number}"
        )
        entities = data.get("entities", [])
        for division in entities:
            divisions[division["name"]] = division["id"]

        page_count = data.get("pageCount", 1)
        if page_number >= page_count or not entities:
            break
        page_number += 1

    return divisions


def get_subject_grants(user_id: str) -> List[dict]:
    data = genesys_request("GET", f"/api/v2/authorization/subjects/{user_id}")
    return data.get("grants", [])


def extract_role_ids(grants: List[dict]) -> Set[str]:
    role_ids: Set[str] = set()
    for grant in grants:
        role_obj = grant.get("role") or {}
        role_id = role_obj.get("id") or grant.get("roleId")
        if role_id:
            role_ids.add(role_id)
    return role_ids


def extract_role_division_pairs(grants: List[dict]) -> Set[Tuple[str, str]]:
    pairs: Set[Tuple[str, str]] = set()

    for grant in grants:
        role_obj = grant.get("role") or {}
        role_id = role_obj.get("id") or grant.get("roleId")
        if not role_id:
            continue

        division_candidates = []

        if isinstance(grant.get("division"), dict):
            division_candidates.append(grant["division"])

        if isinstance(grant.get("divisions"), list):
            division_candidates.extend([d for d in grant["divisions"] if isinstance(d, dict)])

        if isinstance(grant.get("targets"), list):
            for target in grant["targets"]:
                if isinstance(target, dict):
                    if target.get("type") == "DIVISION" and target.get("id"):
                        pairs.add((role_id, target["id"]))
                    elif isinstance(target.get("division"), dict) and target["division"].get("id"):
                        pairs.add((role_id, target["division"]["id"]))

        for d in division_candidates:
            div_id = d.get("id") or d.get("divisionId")
            if div_id:
                pairs.add((role_id, div_id))

        if grant.get("divisionId"):
            pairs.add((role_id, grant["divisionId"]))

    return pairs


def normalize_requested_divisions(requested_names: Sequence[str], division_map: Dict[str, str]) -> List[str]:
    division_ids: List[str] = []
    seen: Set[str] = set()

    for name in requested_names:
        clean_name = name.strip()

        if is_all_divisions_value(clean_name):
            if "*" not in seen:
                division_ids.append("*")
                seen.add("*")
            continue

        if clean_name not in division_map:
            raise ValueError(f"Division not found: {clean_name}")

        div_id = division_map[clean_name]
        if div_id not in seen:
            division_ids.append(div_id)
            seen.add(div_id)

    return division_ids


def bulk_add_grants(user_id: str, grants: List[dict], dry_run: bool = False):
    if not grants:
        return

    for batch in chunked(grants, 100):
        payload = {"grants": list(batch)}
        if dry_run:
            print("\n[INFO] DRY RUN bulkadd payload:")
            print(json.dumps(payload, indent=2))
            continue

        genesys_request("POST", f"/api/v2/authorization/subjects/{user_id}/bulkadd", json=payload)
        print(f"[SUCCESS] bulkadd applied: {len(batch)} grant(s)")


def bulk_remove_grants(user_id: str, grants: List[dict], dry_run: bool = False):
    if not grants:
        return

    for batch in chunked(grants, 100):
        payload = {"grants": list(batch)}
        if dry_run:
            print("\n[INFO] DRY RUN bulkremove payload:")
            print(json.dumps(payload, indent=2))
            continue

        genesys_request("POST", f"/api/v2/authorization/subjects/{user_id}/bulkremove", json=payload)
        print(f"[SUCCESS] bulkremove applied: {len(batch)} grant(s)")


def process_user(
    email: str,
    division_names: Sequence[str],
    remove_division_names: Sequence[str],
    mode: str = "add",
    dry_run: bool = False,
    debug_grants: bool = False,
):
    print(f"\n[INFO] Processing {email}")

    user = get_user(email)
    if not user:
        print("[ERROR] User not found")
        return

    division_map = get_divisions()
    requested_division_ids = normalize_requested_divisions(division_names, division_map)
    requested_remove_division_ids = normalize_requested_divisions(remove_division_names, division_map)

    grants = get_subject_grants(user.id)
    if debug_grants:
        print("\n[INFO] DEBUG raw grants:")
        print(json.dumps(grants, indent=2))

    if not grants:
        print("[WARN] No grants found on user")
        return

    existing_pairs = extract_role_division_pairs(grants)
    role_ids = sorted(extract_role_ids(grants))

    if not role_ids:
        print("[WARN] No roles found on user grants")
        return

    print(f"[INFO] Found {len(role_ids)} roles and {len(existing_pairs)} existing role/division grant pair(s)")

    requested_pairs = {(role_id, div_id) for role_id in role_ids for div_id in requested_division_ids}

    to_add_set: Set[Tuple[str, str]] = set()
    to_remove_set: Set[Tuple[str, str]] = set()

    if requested_pairs:
        to_add_set.update(requested_pairs - existing_pairs)

        if mode == "sync":
            to_remove_set.update(existing_pairs - requested_pairs)

    if requested_remove_division_ids:
        remove_division_id_set = set(requested_remove_division_ids)
        to_remove_set.update(
            (role_id, div_id)
            for role_id, div_id in existing_pairs
            if div_id in remove_division_id_set
        )

        # Special handling: removing wildcard All Divisions should leave Home behind
        # so the role itself is retained on a concrete division instead of disappearing.
        if "*" in remove_division_id_set:
            home_division_id = division_map.get(DEFAULT_FALLBACK_DIVISION_NAME)
            if not home_division_id:
                raise ValueError(
                    f'Fallback division "{DEFAULT_FALLBACK_DIVISION_NAME}" not found; cannot safely remove All Divisions'
                )

            for role_id in role_ids:
                if (role_id, home_division_id) not in existing_pairs:
                    to_add_set.add((role_id, home_division_id))

    # Never remove something we're also explicitly adding
    to_remove_set -= to_add_set

    to_add = sorted(
        [{"roleId": role_id, "divisionId": div_id} for role_id, div_id in to_add_set],
        key=lambda g: (g["roleId"], g["divisionId"])
    )

    to_remove = sorted(
        [{"roleId": role_id, "divisionId": div_id} for role_id, div_id in to_remove_set],
        key=lambda g: (g["roleId"], g["divisionId"])
    )

    print(f"[INFO] Planned adds: {len(to_add)}")
    print(f"[INFO] Planned removes: {len(to_remove)}")

    if to_add:
        bulk_add_grants(user.id, to_add, dry_run=dry_run)

    if to_remove:
        bulk_remove_grants(user.id, to_remove, dry_run=dry_run)

    if not to_add and not to_remove:
        print("[INFO] No changes needed")


def main():
    parser = argparse.ArgumentParser(
        description="Assign and/or remove divisions across all existing roles on a Genesys Cloud user."
    )

    parser.add_argument("--user", help="User email address")
    parser.add_argument(
        "--division",
        nargs="+",
        action="append",
        default=[],
        help='Division name(s) to add. Supports multiple values per flag, e.g. --division "Div A" "Div B". Use "All Divisions" or "*" for all divisions.'
    )
    parser.add_argument(
        "--remdiv",
        nargs="+",
        action="append",
        default=[],
        help='Division name(s) to remove from all current roles. Supports multiple values per flag, e.g. --remdiv "Div A" "Div B". Use "All Divisions" or "*" to remove the wildcard grant and replace it with Home.'
    )
    parser.add_argument("--csv", help="Pipe-delimited file with user_email and divisions/remdiv columns")
    parser.add_argument(
        "--mode",
        choices=["add", "sync"],
        default="add",
        help="When used with --division: add = only add missing role/division grants; sync = add missing and remove other visible role/division pairs"
    )
    parser.add_argument("--dry-run", action="store_true", help="Show the bulkadd/bulkremove payloads without making changes")
    parser.add_argument("--debug-grants", action="store_true", help="Print the raw /authorization/subjects grants payload for troubleshooting")

    args = parser.parse_args()

    flat_divisions = flatten_arg_values(args.division)
    flat_remdivs = flatten_arg_values(args.remdiv)

    if args.user and (flat_divisions or flat_remdivs):
        process_user(
            args.user,
            flat_divisions,
            flat_remdivs,
            mode=args.mode,
            dry_run=args.dry_run,
            debug_grants=args.debug_grants,
        )
        return

    if args.csv:
        with open(args.csv, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter="|")
            for row in reader:
                divisions = [d.strip() for d in row.get("divisions", "").split(",") if d.strip()]
                remdivs = [d.strip() for d in row.get("remdiv", "").split(",") if d.strip()]
                process_user(
                    row["user_email"],
                    divisions,
                    remdivs,
                    mode=args.mode,
                    dry_run=args.dry_run,
                    debug_grants=args.debug_grants,
                )
        return

    print("Provide --user + (--division and/or --remdiv) OR --csv")


if __name__ == "__main__":
    main()
