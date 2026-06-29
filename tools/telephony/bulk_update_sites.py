#!/usr/bin/env python3
"""
Bulk update Genesys Cloud sites from CSV.

For each site in the CSV:
- finds the site by site_name
- GETs the full site object
- sets callerName = Archwell Health
- sets callerAddress from phone_number (normalized to E.164)
- sets edgeAutoUpdateConfig to:
    timeZone: America/New_York
    rrule:    FREQ=WEEKLY;BYDAY=SU
    start:    2026-01-04T01:00:00.000
    end:      2026-01-04T05:00:00.000
- PUTs the full site object back

Safe defaults:
- DRY RUN by default
- skips sites not found
- retries 429 / 5xx / timeout / connection errors
- reads ./client.env automatically

Expected CSV columns:
- site_name
- phone_number
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
DEFAULT_CALLER_NAME = "Archwell Health"
DEFAULT_TIMEOUT = 30
AUTO_UPDATE = {
    "timeZone": "America/New_York",
    "rrule": "FREQ=WEEKLY;BYDAY=SU",
    "start": "2026-01-04T01:00:00.000",
    "end": "2026-01-04T05:00:00.000",
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
    parser = argparse.ArgumentParser(description="Bulk update Genesys Cloud sites from CSV.")
    parser.add_argument("csv_file", help="CSV file to process")
    parser.add_argument("--env-file", default="client.env", help="Path to client.env (default: ./client.env)")
    parser.add_argument("--region", default=None, help="Genesys region domain")
    parser.add_argument("--caller-name", default=DEFAULT_CALLER_NAME, help=f'Caller name (default: "{DEFAULT_CALLER_NAME}")')
    parser.add_argument("--apply", action="store_true", help="Actually update sites. Default is dry run.")
    parser.add_argument("--output-json", default="site_bulk_update_results.json", help="Write detailed results here")
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


def normalize_e164(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        raise ValueError("phone_number is blank or invalid")
    return f"+{digits}"


def get_site_by_name(sess: requests.Session, cfg: Config, args: argparse.Namespace, site_name: str) -> Optional[Dict[str, Any]]:
    url = f"{cfg.api_base}/api/v2/telephony/providers/edges/sites"
    resp = request_with_retry(
        sess, "GET", url,
        params={"name": site_name, "pageSize": 100, "pageNumber": 1},
        timeout=cfg.timeout, max_retries=args.max_retries, base_delay=args.base_delay, max_delay=args.max_delay,
    )
    resp.raise_for_status()
    for entity in resp.json().get("entities", []):
        if entity.get("name") == site_name:
            return entity
    return None


def get_site_detail(sess: requests.Session, cfg: Config, args: argparse.Namespace, site_id: str) -> Dict[str, Any]:
    url = f"{cfg.api_base}/api/v2/telephony/providers/edges/sites/{site_id}"
    resp = request_with_retry(
        sess, "GET", url,
        timeout=cfg.timeout, max_retries=args.max_retries, base_delay=args.base_delay, max_delay=args.max_delay,
    )
    resp.raise_for_status()
    return resp.json()


def clean_put_payload(site: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.loads(json.dumps(site))
    for key in [
        "id",
        "selfUri",
        "dateCreated",
        "dateModified",
        "createdBy",
        "modifiedBy",
        "createdByApp",
        "modifiedByApp",
        "version",
    ]:
        payload.pop(key, None)
    return payload


def put_site(sess: requests.Session, cfg: Config, args: argparse.Namespace, site_id: str, payload: Dict[str, Any]) -> requests.Response:
    url = f"{cfg.api_base}/api/v2/telephony/providers/edges/sites/{site_id}"
    return request_with_retry(
        sess, "PUT", url,
        data=json.dumps(payload),
        timeout=cfg.timeout, max_retries=args.max_retries, base_delay=args.base_delay, max_delay=args.max_delay,
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
    print(f"Caller name target: {args.caller_name}")
    print(f"Auto update target: {json.dumps(AUTO_UPDATE)}")
    print(f"Retry policy: max_retries={args.max_retries}, base_delay={args.base_delay}, max_delay={args.max_delay}")
    print()

    for i, row in enumerate(rows, start=1):
        site_name = row.get("site_name", "").strip()
        result: Dict[str, Any] = {"row": i, "site_name": site_name}

        try:
            if not site_name:
                raise ValueError("Missing site_name")

            target_caller_address = normalize_e164(row.get("phone_number", ""))

            site_summary = get_site_by_name(sess, cfg, args, site_name)
            if not site_summary:
                result["status"] = "site_not_found"
                print(f"[{i}/{len(rows)}] {site_name}: site not found")
                results.append(result)
                continue

            site_id = site_summary.get("id")
            result["site_id"] = site_id

            site_detail = get_site_detail(sess, cfg, args, site_id)
            current = {
                "callerName": site_detail.get("callerName"),
                "callerAddress": site_detail.get("callerAddress"),
                "edgeAutoUpdateConfig": site_detail.get("edgeAutoUpdateConfig"),
            }
            target = {
                "callerName": args.caller_name,
                "callerAddress": target_caller_address,
                "edgeAutoUpdateConfig": AUTO_UPDATE,
            }

            result["current"] = current
            result["target"] = target

            already_matches = (
                current["callerName"] == target["callerName"]
                and current["callerAddress"] == target["callerAddress"]
                and current["edgeAutoUpdateConfig"] == target["edgeAutoUpdateConfig"]
            )

            if already_matches:
                result["status"] = "no_change_needed"
                print(f"[{i}/{len(rows)}] {site_name}: already correct")
                results.append(result)
                continue

            put_payload = clean_put_payload(site_detail)
            put_payload["callerName"] = args.caller_name
            put_payload["callerAddress"] = target_caller_address
            put_payload["edgeAutoUpdateConfig"] = dict(AUTO_UPDATE)

            if not args.apply:
                result["status"] = "dry_run_ready"
                print(f"[{i}/{len(rows)}] {site_name}: ready")
                print(f"    current: {json.dumps(current, ensure_ascii=False)}")
                print(f"    target : {json.dumps(target, ensure_ascii=False)}")
                results.append(result)
                continue

            resp = put_site(sess, cfg, args, site_id, put_payload)
            result["http_status"] = resp.status_code
            result["response"] = safe_json(resp)

            if 200 <= resp.status_code < 300:
                result["status"] = "updated"
                print(f"[{i}/{len(rows)}] {site_name}: updated")
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
