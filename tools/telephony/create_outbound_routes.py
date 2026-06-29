#!/usr/bin/env python3
"""
Create Genesys Cloud outbound routes for existing sites from a CSV file.

Safe defaults
-------------
- DRY RUN by default
- No destructive actions
- Does NOT modify existing routes
- Does NOT delete anything
- Reads auth settings from ./client.env automatically
- Includes retry/backoff handling for 429 / 5xx / timeouts

Expected CSV columns
--------------------
- site_name

Optional CSV columns
--------------------
- route_name       default: Default Outbound Route
- trunk_name       default: Pure-IP
- description      default: Auto Added

Default route payload
---------------------
{
  "name": "Default Outbound Route",
  "description": "Auto Added",
  "classificationTypes": ["National", "Network"],
  "enabled": true,
  "distribution": "SEQUENTIAL",
  "externalTrunkBases": [
    { "name": "Pure-IP" }
  ]
}

Example
-------
Dry run:
  python3 genesys_create_outbound_routes.py ./locations_cleaned_using_DID_e164.csv

Apply:
  python3 genesys_create_outbound_routes.py ./locations_cleaned_using_DID_e164.csv --apply
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


DEFAULT_REGION = "usw2.pure.cloud"
DEFAULT_ROUTE_NAME = "Default Outbound Route"
DEFAULT_TRUNK_NAME = "Pure-IP"
DEFAULT_DESCRIPTION = "Auto Added"
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
    parser = argparse.ArgumentParser(description="Create Genesys Cloud outbound routes from CSV.")
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
        "--default-route-name",
        default=DEFAULT_ROUTE_NAME,
        help=f'Fallback route name when CSV route_name is blank (default: "{DEFAULT_ROUTE_NAME}")'
    )
    parser.add_argument(
        "--default-trunk-name",
        default=DEFAULT_TRUNK_NAME,
        help=f'Fallback trunk name when CSV trunk_name is blank (default: "{DEFAULT_TRUNK_NAME}")'
    )
    parser.add_argument(
        "--default-description",
        default=DEFAULT_DESCRIPTION,
        help=f'Fallback description when CSV description is blank (default: "{DEFAULT_DESCRIPTION}")'
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually create routes. Default is dry run."
    )
    parser.add_argument(
        "--output-json",
        default="outbound_route_results.json",
        help="Write detailed results here"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT})"
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=8,
        help="Max retries for 429/5xx/transient request failures (default: 8)"
    )
    parser.add_argument(
        "--base-delay",
        type=float,
        default=2.0,
        help="Base backoff delay in seconds (default: 2.0)"
    )
    parser.add_argument(
        "--max-delay",
        type=float,
        default=30.0,
        help="Max backoff delay in seconds (default: 30.0)"
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


def compute_retry_delay(
    resp: Optional[requests.Response],
    attempt: int,
    base_delay: float,
    max_delay: float,
) -> float:
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


def safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"text": resp.text}


def read_csv_rows(csv_file: str) -> List[Dict[str, str]]:
    with open(csv_file, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            cleaned = {str(k).strip(): (str(v).strip() if v is not None else "") for k, v in row.items()}
            if any(cleaned.values()):
                rows.append(cleaned)
        return rows


def get_site_by_name(sess: requests.Session, cfg: Config, args: argparse.Namespace, site_name: str) -> Optional[Dict[str, Any]]:
    url = f"{cfg.api_base}/api/v2/telephony/providers/edges/sites"
    resp = request_with_retry(
        sess,
        "GET",
        url,
        params={"name": site_name, "pageSize": 100, "pageNumber": 1},
        timeout=cfg.timeout,
        max_retries=args.max_retries,
        base_delay=args.base_delay,
        max_delay=args.max_delay,
    )
    resp.raise_for_status()

    for entity in resp.json().get("entities", []):
        if entity.get("name") == site_name:
            return entity
    return None


def get_outbound_routes(sess: requests.Session, cfg: Config, args: argparse.Namespace, site_id: str) -> List[Dict[str, Any]]:
    url = f"{cfg.api_base}/api/v2/telephony/providers/edges/sites/{site_id}/outboundroutes"

    page = 1
    routes: List[Dict[str, Any]] = []
    while True:
        resp = request_with_retry(
            sess,
            "GET",
            url,
            params={"pageSize": 100, "pageNumber": page},
            timeout=cfg.timeout,
            max_retries=args.max_retries,
            base_delay=args.base_delay,
            max_delay=args.max_delay,
        )
        resp.raise_for_status()
        body = resp.json()
        batch = body.get("entities", [])
        routes.extend(batch)

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

    return routes


def route_already_exists(existing_routes: List[Dict[str, Any]], route_name: str) -> Optional[Dict[str, Any]]:
    for route in existing_routes:
        if route.get("name") == route_name:
            return route
    return None


def build_payload(row: Dict[str, str], args: argparse.Namespace) -> Dict[str, Any]:
    site_name = row.get("site_name", "").strip()
    if not site_name:
        raise ValueError("Missing site_name")

    route_name = row.get("route_name", "").strip() or args.default_route_name
    trunk_name = row.get("trunk_name", "").strip() or args.default_trunk_name
    description = row.get("description", "").strip() or args.default_description

    return {
        "name": route_name,
        "description": description,
        "classificationTypes": ["National", "Network"],
        "enabled": True,
        "distribution": "SEQUENTIAL",
        "externalTrunkBases": [
            {
                "name": trunk_name
            }
        ]
    }


def create_outbound_route(
    sess: requests.Session,
    cfg: Config,
    args: argparse.Namespace,
    site_id: str,
    payload: Dict[str, Any],
) -> requests.Response:
    url = f"{cfg.api_base}/api/v2/telephony/providers/edges/sites/{site_id}/outboundroutes"
    return request_with_retry(
        sess,
        "POST",
        url,
        data=json.dumps(payload),
        timeout=cfg.timeout,
        max_retries=args.max_retries,
        base_delay=args.base_delay,
        max_delay=args.max_delay,
    )


def main() -> int:
    args = parse_args()
    cfg = build_config(args)
    token = get_token(cfg)
    sess = build_session(token)

    rows = read_csv_rows(args.csv_file)
    results: List[Dict[str, Any]] = []

    print(f"Loaded {len(rows)} rows from {args.csv_file}")
    print(f"Using env file: {Path(args.env_file).resolve()}")
    print(f"Region: {cfg.region}")
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"Default route name: {args.default_route_name}")
    print(f"Default trunk name: {args.default_trunk_name}")
    print(f"Retry policy: max_retries={args.max_retries}, base_delay={args.base_delay}, max_delay={args.max_delay}")
    print()

    for i, row in enumerate(rows, start=1):
        site_name = row.get("site_name", "").strip()
        result: Dict[str, Any] = {"row": i, "site_name": site_name}

        try:
            if not site_name:
                raise ValueError("Missing site_name")

            payload = build_payload(row, args)
            route_name = payload["name"]
            result["payload"] = payload

            site = get_site_by_name(sess, cfg, args, site_name)
            if not site:
                result["status"] = "site_not_found"
                print(f"[{i}/{len(rows)}] {site_name}: site not found")
                results.append(result)
                continue

            site_id = site.get("id")
            result["site_id"] = site_id

            existing_routes = get_outbound_routes(sess, cfg, args, site_id)
            existing = route_already_exists(existing_routes, route_name)
            if existing:
                result["status"] = "route_already_exists"
                result["route_id"] = existing.get("id")
                print(f"[{i}/{len(rows)}] {site_name}: route already exists -> {route_name}")
                results.append(result)
                continue

            if not args.apply:
                result["status"] = "dry_run_ready"
                print(f"[{i}/{len(rows)}] {site_name}: ready -> {route_name}")
                print(f"    payload: {json.dumps(payload, ensure_ascii=False)}")
                results.append(result)
                continue

            resp = create_outbound_route(sess, cfg, args, site_id, payload)
            result["http_status"] = resp.status_code
            result["response"] = safe_json(resp)

            if 200 <= resp.status_code < 300:
                result["status"] = "created"
                created_id = result["response"].get("id")
                if created_id:
                    result["route_id"] = created_id
                print(f"[{i}/{len(rows)}] {site_name}: route created -> {route_name}")
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
