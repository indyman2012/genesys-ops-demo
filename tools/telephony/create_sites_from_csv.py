#!/usr/bin/env python3
"""
Create missing Genesys Cloud sites from a CSV file.

Safe defaults:
- DRY RUN by default
- No destructive actions
- Does NOT modify existing sites
- Reads auth settings from ./client.env automatically

Expected CSV columns (from your uploaded file):
- location_name
- state
- phone_number
- site_name

Default create payload:
{
  "name": "<site_name>",
  "description": "Auto Added",
  "location": {
    "id": "<resolved location id>",
    "name": "<location_name>"
  },
  "timeZone": "<derived from state>",
  "mediaModel": "Cloud"
}

Optional:
- Use --include-caller-id to also include:
  - callerName
  - callerAddress
  This is OFF by default so the create pass stays as conservative as possible.

Examples:
  python3 genesys_create_sites_from_csv.py ./locations_cleaned_using_DID_e164.csv
  python3 genesys_create_sites_from_csv.py ./locations_cleaned_using_DID_e164.csv --apply
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.csv_utils import csv_field, normalize_csv_row


DEFAULT_REGION = "usw2.pure.cloud"
DEFAULT_DESCRIPTION = "Auto Added"
DEFAULT_MEDIA_MODEL = "Cloud"
DEFAULT_CALLER_NAME = "Archwell Health"
DEFAULT_TIMEOUT = 30

# State-to-timezone mapping for the states present in your uploaded CSV.
# This keeps the create pass deterministic and avoids needing a separate timezone file.
STATE_TIMEZONE_MAP = {
    "AL": "America/Chicago",
    "AR": "America/Chicago",
    "AZ": "America/Phoenix",
    "CO": "America/Denver",
    "FL": "America/New_York",   # all FL sites in the uploaded CSV are in Eastern time
    "KS": "America/Chicago",
    "MO": "America/Chicago",
    "NC": "America/New_York",
    "NE": "America/Chicago",
    "NV": "America/Los_Angeles",
    "OH": "America/New_York",
    "OK": "America/Chicago",
    "PA": "America/New_York",
    "TN": "America/Chicago",    # Nashville area
    "WI": "America/Chicago",
}


@dataclass
class Config:
    region: str
    client_id: str
    client_secret: str
    timeout: int = DEFAULT_TIMEOUT

    @property
    def login_base(self) -> str:
        return f"https://login.{self.region}"

    @property
    def api_base(self) -> str:
        return f"https://api.{self.region}"


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        raise SystemExit(f"client.env not found: {env_path}")

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export "):].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create missing Genesys Cloud sites from CSV.")
    parser.add_argument("csv_file", help="CSV file to process")
    parser.add_argument(
        "--env-file",
        default="client.env",
        help="Path to client.env (default: ./client.env)"
    )
    parser.add_argument(
        "--region",
        default=None,
        help="Genesys region domain. Defaults to GENESYS_CLOUD_REGION from env/client.env, otherwise usw2.pure.cloud"
    )
    parser.add_argument(
        "--description",
        default=DEFAULT_DESCRIPTION,
        help=f'Description to apply to new sites (default: "{DEFAULT_DESCRIPTION}")'
    )
    parser.add_argument(
        "--media-model",
        default=DEFAULT_MEDIA_MODEL,
        help=f'Media model for new sites (default: "{DEFAULT_MEDIA_MODEL}")'
    )
    parser.add_argument(
        "--include-caller-id",
        action="store_true",
        help="Include callerName and callerAddress in the create payload"
    )
    parser.add_argument(
        "--caller-name",
        default=DEFAULT_CALLER_NAME,
        help=f'Caller name used with --include-caller-id (default: "{DEFAULT_CALLER_NAME}")'
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually create missing sites. Default is dry run."
    )
    parser.add_argument(
        "--output-json",
        default="site_creation_results.json",
        help="Write detailed results here"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT})"
    )
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> Config:
    env_path = Path(args.env_file).resolve()
    load_env_file(env_path)

    region = (
        args.region
        or os.getenv("GENESYS_CLOUD_REGION", "").strip()
        or DEFAULT_REGION
    )

    client_id = (
        os.getenv("GENESYS_CLIENT_ID", "").strip()
        or os.getenv("CLIENT_ID", "").strip()
    )
    client_secret = (
        os.getenv("GENESYS_CLIENT_SECRET", "").strip()
        or os.getenv("CLIENT_SECRET", "").strip()
    )

    if not client_id:
        raise SystemExit("Missing required client id in env/client.env (GENESYS_CLIENT_ID or CLIENT_ID)")
    if not client_secret:
        raise SystemExit("Missing required client secret in env/client.env (GENESYS_CLIENT_SECRET or CLIENT_SECRET)")

    return Config(
        region=region,
        client_id=client_id,
        client_secret=client_secret,
        timeout=args.timeout,
    )


def get_token(cfg: Config) -> str:
    raw = f"{cfg.client_id}:{cfg.client_secret}".encode("utf-8")
    auth = base64.b64encode(raw).decode("ascii")
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"grant_type": "client_credentials"}

    resp = requests.post(
        f"{cfg.login_base}/oauth/token",
        headers=headers,
        data=data,
        timeout=cfg.timeout,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def build_session(token: str) -> requests.Session:
    sess = requests.Session()
    sess.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    return sess


def read_csv_rows(csv_file: str) -> List[Dict[str, str]]:
    with open(csv_file, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            cleaned = {str(k).strip(): (str(v).strip() if v is not None else "") for k, v in row.items()}
            if any(cleaned.values()):
                rows.append(normalize_csv_row(cleaned))
        return rows


def normalize_e164(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        raise ValueError("phone_number is blank or invalid")
    return f"+{digits}"


def safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"text": resp.text}


def paged_get_entities(
    sess: requests.Session,
    url: str,
    timeout: int,
    page_size: int = 100,
) -> List[Dict[str, Any]]:
    page = 1
    entities: List[Dict[str, Any]] = []

    while True:
        resp = sess.get(url, params={"pageSize": page_size, "pageNumber": page}, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        batch = body.get("entities", [])
        entities.extend(batch)

        page_count = body.get("pageCount")
        if page_count is not None:
            if page >= page_count:
                break
        else:
            if not batch:
                break

        if not batch:
            break
        page += 1

    return entities


def get_all_locations(sess: requests.Session, cfg: Config) -> Dict[str, Dict[str, Any]]:
    url = f"{cfg.api_base}/api/v2/locations"
    entities = paged_get_entities(sess, url, cfg.timeout, page_size=100)
    return {str(entity.get("name", "")).strip(): entity for entity in entities if entity.get("name")}


def get_all_sites(sess: requests.Session, cfg: Config) -> Dict[str, Dict[str, Any]]:
    url = f"{cfg.api_base}/api/v2/telephony/providers/edges/sites"
    entities = paged_get_entities(sess, url, cfg.timeout, page_size=100)
    return {str(entity.get("name", "")).strip(): entity for entity in entities if entity.get("name")}


def derive_timezone(row: Dict[str, str]) -> str:
    state = row.get("state", "").strip().upper()
    tz = STATE_TIMEZONE_MAP.get(state)
    if not tz:
        raise ValueError(f"No timezone mapping defined for state: {state or '<blank>'}")
    return tz


def build_payload(
    row: Dict[str, str],
    location: Dict[str, Any],
    args: argparse.Namespace,
) -> Dict[str, Any]:
    site_name = csv_field(row, "site_name")
    location_name = csv_field(row, "location_name")

    if not site_name:
        raise ValueError("Missing site_name")
    if not location_name:
        raise ValueError(f"{site_name}: missing location_name")

    payload: Dict[str, Any] = {
        "name": site_name,
        "description": args.description,
        "location": {
            "id": location["id"],
            "name": location_name,
        },
        "timeZone": derive_timezone(row),
        "mediaModel": args.media_model,
    }

    if args.include_caller_id:
        payload["callerName"] = args.caller_name
        payload["callerAddress"] = normalize_e164(row.get("phone_number", ""))

    return payload


def create_site(
    sess: requests.Session,
    cfg: Config,
    payload: Dict[str, Any],
) -> requests.Response:
    url = f"{cfg.api_base}/api/v2/telephony/providers/edges/sites"
    return sess.post(url, data=json.dumps(payload), timeout=cfg.timeout)


def main() -> int:
    args = parse_args()
    cfg = build_config(args)
    token = get_token(cfg)
    sess = build_session(token)

    rows = read_csv_rows(args.csv_file)
    locations_by_name = get_all_locations(sess, cfg)
    sites_by_name = get_all_sites(sess, cfg)

    results: List[Dict[str, Any]] = []

    print(f"Loaded {len(rows)} rows from {args.csv_file}")
    print(f"Using env file: {Path(args.env_file).resolve()}")
    print(f"Region: {cfg.region}")
    print(f"Known locations loaded: {len(locations_by_name)}")
    print(f"Known sites loaded: {len(sites_by_name)}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"Include caller ID on create: {'yes' if args.include_caller_id else 'no'}")
    print()

    for i, row in enumerate(rows, start=1):
        site_name = csv_field(row, "site_name")
        location_name = csv_field(row, "location_name")
        result: Dict[str, Any] = {"row": i, "site_name": site_name, "location_name": location_name}

        try:
            if not site_name:
                raise ValueError("Missing site_name")
            if not location_name:
                raise ValueError(f"{site_name}: missing location_name")

            existing_site = sites_by_name.get(site_name)
            if existing_site:
                result["status"] = "site_already_exists"
                result["site_id"] = existing_site.get("id")
                print(f"[{i}/{len(rows)}] {site_name}: already exists -> {existing_site.get('id')}")
                results.append(result)
                continue

            location = locations_by_name.get(location_name)
            if not location:
                result["status"] = "location_not_found"
                print(f"[{i}/{len(rows)}] {site_name}: location not found -> {location_name}")
                results.append(result)
                continue

            payload = build_payload(row, location, args)
            result["payload"] = payload
            result["location_id"] = location.get("id")

            if not args.apply:
                result["status"] = "dry_run_ready"
                print(f"[{i}/{len(rows)}] {site_name}: ready")
                print(f"    payload: {json.dumps(payload, ensure_ascii=False)}")
                results.append(result)
                continue

            resp = create_site(sess, cfg, payload)
            result["http_status"] = resp.status_code
            result["response"] = safe_json(resp)

            if 200 <= resp.status_code < 300:
                result["status"] = "created"
                created_id = result["response"].get("id")
                if created_id:
                    result["site_id"] = created_id
                    sites_by_name[site_name] = {"id": created_id, "name": site_name}
                print(f"[{i}/{len(rows)}] {site_name}: created")
            else:
                result["status"] = "error"
                print(f"[{i}/{len(rows)}] {site_name}: ERROR {resp.status_code}")
                print(json.dumps(result["response"], indent=2, ensure_ascii=False))

            results.append(result)

        except Exception as exc:
            result["status"] = "exception"
            result["error"] = str(exc)
            print(f"[{i}/{len(rows)}] {site_name or '<blank>'}: EXCEPTION {exc}")
            results.append(result)

    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print()
    print(f"Wrote detailed results to {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
