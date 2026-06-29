#!/usr/bin/env python3
"""
Validate reviewed user/site mapping data against Genesys Cloud.

This version expects the CSV to already include a reviewed email column:
- generated_email

Input assumptions
-----------------
The CSV should contain at least:
- site_name (or Primary / first column)
- person_name (or User Assigned / second column)
- generated_email

If site_name / person_name headers are not present, the script will try:
- first column as site_name
- second column as person_name

What it does
------------
- reads the reviewed CSV
- uses generated_email directly (no email generation)
- loads all Genesys Cloud sites
- loads all Genesys Cloud users
- validates:
    - site exists
    - user exists by generated_email
- captures:
    - user_id
    - site_id
    - location_id
    - user's current location
    - best-effort station/phone hints from the user payload

Outputs
-------
- ready_to_apply.csv
- manual_review.csv
- validation_results.json

Safe
----
- read-only validation only
- no write actions in Genesys Cloud
- handles 429 / 5xx / timeout / connection retries
- reads ./client.env automatically
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import random
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


DEFAULT_REGION = "usw2.pure.cloud"
DEFAULT_TIMEOUT = 30


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
    parser = argparse.ArgumentParser(description="Validate reviewed user/site data against Genesys Cloud.")
    parser.add_argument("input_file", help="Reviewed CSV file")
    parser.add_argument("--env-file", default="client.env", help="Path to client.env (default: ./client.env)")
    parser.add_argument("--region", default=None, help="Genesys region domain")
    parser.add_argument("--output-dir", default=".", help="Directory for output files")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT})")
    parser.add_argument("--max-retries", type=int, default=8, help="Max retries for 429/5xx/transient request failures (default: 8)")
    parser.add_argument("--base-delay", type=float, default=2.0, help="Base backoff delay in seconds (default: 2.0)")
    parser.add_argument("--max-delay", type=float, default=30.0, help="Max backoff delay in seconds (default: 30.0)")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> Config:
    load_env_file(Path(args.env_file).resolve())

    region = args.region or os.getenv("GENESYS_CLOUD_REGION", "").strip() or DEFAULT_REGION
    client_id = os.getenv("GENESYS_CLIENT_ID", "").strip() or os.getenv("CLIENT_ID", "").strip()
    client_secret = os.getenv("GENESYS_CLIENT_SECRET", "").strip() or os.getenv("CLIENT_SECRET", "").strip()

    if not client_id:
        raise SystemExit("Missing required client id in env/client.env (GENESYS_CLIENT_ID or CLIENT_ID)")
    if not client_secret:
        raise SystemExit("Missing required client secret in env/client.env (GENESYS_CLIENT_SECRET or CLIENT_SECRET)")

    return Config(region=region, client_id=client_id, client_secret=client_secret, timeout=args.timeout)


def get_token(cfg: Config) -> str:
    raw = f"{cfg.client_id}:{cfg.client_secret}".encode("utf-8")
    auth = base64.b64encode(raw).decode("ascii")
    resp = requests.post(
        f"{cfg.login_base}/oauth/token",
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials"},
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


def compute_retry_delay(resp: Optional[requests.Response], attempt: int, base_delay: float, max_delay: float) -> float:
    if resp is not None:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), max_delay)
            except ValueError:
                pass
    delay = min(base_delay * (2 ** max(0, attempt - 1)), max_delay)
    jitter = random.uniform(0, min(0.5, delay * 0.25))
    return min(delay + jitter, max_delay)


def request_with_retry(
    sess: requests.Session,
    method: str,
    url: str,
    *,
    timeout: int,
    max_retries: int,
    base_delay: float,
    max_delay: float,
    **kwargs: Any,
) -> requests.Response:
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 2):
        resp: Optional[requests.Response] = None
        try:
            resp = sess.request(method, url, timeout=timeout, **kwargs)
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if attempt > max_retries:
                    return resp
                delay = compute_retry_delay(resp, attempt, base_delay, max_delay)
                print(f"    retryable HTTP {resp.status_code}; sleeping {delay:.2f}s before retry {attempt}/{max_retries}")
                time.sleep(delay)
                continue
            return resp
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt > max_retries:
                raise
            delay = compute_retry_delay(resp, attempt, base_delay, max_delay)
            print(f"    transient request error ({type(exc).__name__}); sleeping {delay:.2f}s before retry {attempt}/{max_retries}")
            time.sleep(delay)

    if last_exc:
        raise last_exc
    raise RuntimeError("request_with_retry exhausted unexpectedly")


def read_csv_rows(path: str) -> List[Dict[str, str]]:
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            cleaned = {str(k).strip(): (str(v).strip() if v is not None else "") for k, v in row.items()}
            if any(cleaned.values()):
                rows.append(cleaned)
        return normalize_source_rows(rows)


def normalize_source_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    normalized_rows: List[Dict[str, str]] = []

    for row in rows:
        keys = list(row.keys())

        site_name = (
            row.get("site_name", "")
            or row.get("Primary", "")
            or row.get("primary", "")
            or row.get("Site", "")
            or row.get("Location", "")
            or (row.get(keys[0], "") if len(keys) >= 1 else "")
        ).strip()

        person_name = (
            row.get("person_name", "")
            or row.get("User Asssigned", "")
            or row.get("User Assigned", "")
            or row.get("name", "")
            or row.get("Name", "")
            or (row.get(keys[1], "") if len(keys) >= 2 else "")
        ).strip()

        generated_email = row.get("generated_email", "").strip().lower()

        if site_name or person_name or generated_email:
            normalized_rows.append({
                "site_name": site_name,
                "person_name": person_name,
                "generated_email": generated_email,
            })

    return normalized_rows


def paged_get_entities(
    sess: requests.Session,
    url: str,
    cfg: Config,
    args: argparse.Namespace,
    extra_params: Optional[Dict[str, Any]] = None,
    page_size: int = 100,
) -> List[Dict[str, Any]]:
    page = 1
    entities: List[Dict[str, Any]] = []

    while True:
        params = {"pageSize": page_size, "pageNumber": page}
        if extra_params:
            params.update(extra_params)

        resp = request_with_retry(
            sess, "GET", url,
            params=params,
            timeout=cfg.timeout, max_retries=args.max_retries, base_delay=args.base_delay, max_delay=args.max_delay,
        )
        resp.raise_for_status()
        body = resp.json()
        batch = body.get("entities", [])
        entities.extend(batch)

        page_count = body.get("pageCount")
        if page_count is not None:
            if page >= page_count:
                break
        elif not batch:
            break

        if not batch:
            break
        page += 1

    return entities


def load_sites(sess: requests.Session, cfg: Config, args: argparse.Namespace) -> Dict[str, Dict[str, Any]]:
    url = f"{cfg.api_base}/api/v2/telephony/providers/edges/sites"
    entities = paged_get_entities(sess, url, cfg, args, page_size=100)
    return {str(e.get("name", "")).strip(): e for e in entities if e.get("name")}


def extract_candidate_emails(user_obj: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str):
            email = value.strip().lower()
            if "@" in email:
                candidates.append(email)

    add(user_obj.get("email"))
    add(user_obj.get("username"))

    for key in ["primaryContactInfo", "workContactInfo"]:
        block = user_obj.get(key)
        if isinstance(block, dict):
            add(block.get("email"))
            add(block.get("addressEmail"))

    for key in ["addresses", "routingAddresses"]:
        block = user_obj.get(key)
        if isinstance(block, list):
            for item in block:
                if isinstance(item, dict):
                    add(item.get("address"))
                    add(item.get("value"))
                    add(item.get("display"))

    unique = []
    seen = set()
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def load_users(sess: requests.Session, cfg: Config, args: argparse.Namespace) -> Tuple[Dict[str, List[Dict[str, Any]]], int]:
    url = f"{cfg.api_base}/api/v2/users"
    entities = paged_get_entities(sess, url, cfg, args, page_size=100)
    email_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for user in entities:
        for email in extract_candidate_emails(user):
            email_map[email].append(user)

    return email_map, len(entities)


def get_user_detail(sess: requests.Session, cfg: Config, args: argparse.Namespace, user_id: str) -> Dict[str, Any]:
    url = f"{cfg.api_base}/api/v2/users/{user_id}"
    resp = request_with_retry(
        sess, "GET", url,
        timeout=cfg.timeout, max_retries=args.max_retries, base_delay=args.base_delay, max_delay=args.max_delay,
    )
    resp.raise_for_status()
    return resp.json()


def extract_location(detail: Dict[str, Any]) -> Tuple[str, str]:
    location = detail.get("location")
    if isinstance(location, dict):
        return str(location.get("id", "") or ""), str(location.get("name", "") or "")
    return "", ""


def extract_station_hints(detail: Dict[str, Any]) -> Dict[str, str]:
    hints: Dict[str, str] = {
        "station_id": "",
        "station_name": "",
        "phone_id": "",
        "phone_name": "",
    }

    candidate_keys = ["station", "defaultStation", "primaryStation", "phone", "defaultPhone", "primaryPhone"]
    for key in candidate_keys:
        obj = detail.get(key)
        if isinstance(obj, dict):
            obj_id = str(obj.get("id", "") or "")
            obj_name = str(obj.get("name", "") or "")
            # Preserve separate station vs phone when the key tells us which is which
            if "station" in key.lower():
                hints["station_id"] = hints["station_id"] or obj_id
                hints["station_name"] = hints["station_name"] or obj_name
            elif "phone" in key.lower():
                hints["phone_id"] = hints["phone_id"] or obj_id
                hints["phone_name"] = hints["phone_name"] or obj_name
            else:
                hints["station_id"] = hints["station_id"] or obj_id
                hints["station_name"] = hints["station_name"] or obj_name
                hints["phone_id"] = hints["phone_id"] or obj_id
                hints["phone_name"] = hints["phone_name"] or obj_name

    for key in ["stations", "phones"]:
        arr = detail.get(key)
        if isinstance(arr, list) and arr:
            first = arr[0]
            if isinstance(first, dict):
                if key == "stations":
                    hints["station_id"] = hints["station_id"] or str(first.get("id", "") or "")
                    hints["station_name"] = hints["station_name"] or str(first.get("name", "") or "")
                else:
                    hints["phone_id"] = hints["phone_id"] or str(first.get("id", "") or "")
                    hints["phone_name"] = hints["phone_name"] or str(first.get("name", "") or "")

    # Genesys user payloads sometimes expose station/phone via line appearances
    for key in ["stationLineAppearances", "lineAppearances"]:
        arr = detail.get(key)
        if isinstance(arr, list):
            for item in arr:
                if not isinstance(item, dict):
                    continue
                station = item.get("station")
                if isinstance(station, dict):
                    hints["station_id"] = hints["station_id"] or str(station.get("id", "") or "")
                    hints["station_name"] = hints["station_name"] or str(station.get("name", "") or "")
                phone = item.get("phone")
                if isinstance(phone, dict):
                    hints["phone_id"] = hints["phone_id"] or str(phone.get("id", "") or "")
                    hints["phone_name"] = hints["phone_name"] or str(phone.get("name", "") or "")
                line = item.get("line")
                if isinstance(line, dict) and not hints["phone_name"]:
                    hints["phone_name"] = str(line.get("name", "") or "")

    return hints


def write_csv(path: Path, rows: List[Dict[str, Any]], field_order: List[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=field_order, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    args = parse_args()
    cfg = build_config(args)
    token = get_token(cfg)
    sess = build_session(token)

    source_rows = read_csv_rows(args.input_file)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loaded {len(source_rows)} source rows from {args.input_file}")
    print(f"Using env file: {Path(args.env_file).resolve()}")
    print(f"Region: {cfg.region}")
    print("Loading sites...")
    sites_by_name = load_sites(sess, cfg, args)
    print(f"Loaded {len(sites_by_name)} sites")
    print("Loading users...")
    users_by_email, user_count = load_users(sess, cfg, args)
    print(f"Loaded {user_count} users with {len(users_by_email)} indexed email addresses")
    print()

    detail_cache: Dict[str, Dict[str, Any]] = {}
    ready_rows: List[Dict[str, Any]] = []
    review_rows: List[Dict[str, Any]] = []
    all_results: List[Dict[str, Any]] = []

    success_count = 0
    success_with_phone_count = 0
    success_without_phone_count = 0
    success_with_station_count = 0

    for idx, src in enumerate(source_rows, start=1):
        site_name = src.get("site_name", "").strip()
        # Skip NVLWOO rows entirely
        if site_name.upper() == "NVLWOO":
            continue

        person_name = src.get("person_name", "").strip()
        generated_email = src.get("generated_email", "").strip().lower()

        result: Dict[str, Any] = {
            "row": idx,
            "site_name": site_name,
            "person_name": person_name,
            "generated_email": generated_email,
            "user_id": "",
            "user_name": "",
            "site_id": "",
            "location_id": "",
            "current_location_id": "",
            "current_location_name": "",
            "station_id": "",
            "station_name": "",
            "phone_id": "",
            "phone_name": "",
            "status": "",
            "issue": "",
        }

        try:
            if not site_name:
                result["status"] = "manual_review"
                result["issue"] = "Missing site/location name"
                review_rows.append(result)
                all_results.append(result)
                continue

            if not person_name:
                result["status"] = "manual_review"
                result["issue"] = "Missing person name"
                review_rows.append(result)
                all_results.append(result)
                continue

            if not generated_email:
                result["status"] = "manual_review"
                result["issue"] = "Missing generated_email"
                review_rows.append(result)
                all_results.append(result)
                continue

            site = sites_by_name.get(site_name)
            if not site:
                result["status"] = "manual_review"
                result["issue"] = "Site not found in Genesys"
                review_rows.append(result)
                all_results.append(result)
                continue

            result["site_id"] = str(site.get("id", "") or "")
            location = site.get("location") or {}
            if isinstance(location, dict):
                result["location_id"] = str(location.get("id", "") or "")

            matches = users_by_email.get(generated_email, [])
            if not matches:
                result["status"] = "manual_review"
                result["issue"] = "generated_email did not match a Genesys user"
                review_rows.append(result)
                all_results.append(result)
                continue

            if len(matches) > 1:
                result["status"] = "manual_review"
                result["issue"] = f"generated_email matched multiple Genesys users ({len(matches)})"
                review_rows.append(result)
                all_results.append(result)
                continue

            user = matches[0]
            user_id = str(user.get("id", "") or "")
            result["user_id"] = user_id
            result["user_name"] = str(user.get("name", "") or user.get("username", "") or "")

            if user_id not in detail_cache:
                detail_cache[user_id] = get_user_detail(sess, cfg, args, user_id)
            detail = detail_cache[user_id]

            current_location_id, current_location_name = extract_location(detail)
            result["current_location_id"] = current_location_id
            result["current_location_name"] = current_location_name

            hints = extract_station_hints(detail)
            result.update(hints)

            success_count += 1
            if result["phone_id"]:
                success_with_phone_count += 1
            else:
                success_without_phone_count += 1
            if result["station_id"]:
                success_with_station_count += 1

            if not result["phone_id"] and not result["station_id"]:
                result["status"] = "ready_no_phone_found"
                result["issue"] = "User matched, but no station/phone hint was found in user detail"
            else:
                result["status"] = "ready"
                result["issue"] = ""

            ready_rows.append(result)
            all_results.append(result)

            if idx % 100 == 0:
                print(f"Processed {idx}/{len(source_rows)} rows")

        except Exception as exc:
            result["status"] = "manual_review"
            result["issue"] = str(exc)
            review_rows.append(result)
            all_results.append(result)

    ready_fields = [
        "row", "site_name", "person_name", "generated_email", "user_id", "user_name",
        "site_id", "location_id", "current_location_id", "current_location_name",
        "station_id", "station_name", "phone_id", "phone_name", "status", "issue"
    ]
    review_fields = [
        "row", "site_name", "person_name", "generated_email", "status", "issue"
    ]

    ready_path = output_dir / "ready_to_apply.csv"
    review_path = output_dir / "manual_review.csv"
    json_path = output_dir / "validation_results.json"

    write_csv(ready_path, ready_rows, ready_fields)
    write_csv(review_path, review_rows, review_fields)
    json_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"Ready rows              : {len(ready_rows)}")
    print(f"Manual review rows      : {len(review_rows)}")
    print(f"Matched users checked   : {success_count}")
    print(f"Matched users w/ phone  : {success_with_phone_count}")
    print(f"Matched users w/o phone : {success_without_phone_count}")
    print(f"Matched users w/ station: {success_with_station_count}")
    print(f"Wrote {ready_path}")
    print(f"Wrote {review_path}")
    print(f"Wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
