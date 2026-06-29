#!/usr/bin/env python3
"""
Create Genesys Cloud wrap-up codes from an input file and assign them to all queues
whose names start with a chosen prefix (default: NCM). Also supports removing
all existing queue wrap-up codes before adding the requested set.

Conventions:
- Reads OAuth creds from a colocated client.env file
- Supports CSV, TXT, or JSON input for wrap-up codes
- Safe by default with --dry-run
- Retries on 429 / 5xx
- Writes a CSV summary report

Expected env vars in client.env:
    GENESYS_CLIENT_ID=your_client_id
    GENESYS_CLIENT_SECRET=your_client_secret
Optional:
    GENESYS_REGION=usw2
    GENESYS_BASE_URL=https://api.usw2.pure.cloud

Examples:
    python genesys_wrapupcodes_apply.py --input wrapup_codes.csv
    python genesys_wrapupcodes_apply.py --input wrapup_codes.csv --execute
    python genesys_wrapupcodes_apply.py --input wrapup_codes.txt --queue-prefix NCM_
    python genesys_wrapupcodes_apply.py --input wrapup_codes.csv --remove-all-existing --execute
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# ----------------------------- Console colors ----------------------------- #

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"


def color(text: str, code: str) -> str:
    return f"{code}{text}{C.RESET}"


def log(msg: str, level: str = "INFO") -> None:
    palette = {
        "INFO": C.CYAN,
        "OK": C.GREEN,
        "WARN": C.YELLOW,
        "ERR": C.RED,
        "STEP": C.BLUE,
    }
    print(color(f"[{level}]", palette.get(level, C.RESET)), msg)


# ----------------------------- Env handling ------------------------------ #

def load_env_file(env_path: Path) -> Dict[str, str]:
    if not env_path.exists():
        raise FileNotFoundError(f"client.env not found at: {env_path}")

    values: Dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def base_url_from_region(region: str) -> str:
    mapping = {
        "mypurecloud.com": "https://api.mypurecloud.com",
        "usw2": "https://api.usw2.pure.cloud",
        "use1": "https://api.use1.pure.cloud",
        "euw1": "https://api.euw1.pure.cloud",
        "cac1": "https://api.cac1.pure.cloud",
        "aps1": "https://api.aps1.pure.cloud",
        "apse2": "https://api.apse2.pure.cloud",
        "sae1": "https://api.sae1.pure.cloud",
        "me1": "https://api.me1.pure.cloud",
        "af-south-1": "https://api.af-south-1.pure.cloud",
    }
    return mapping.get(region, f"https://api.{region}.pure.cloud")


def login_url_from_base_url(base_url: str) -> str:
    """
    Genesys Cloud OAuth tokens must be requested from the login host, not the API host.
    """
    normalized = base_url.rstrip("/").strip()
    if not normalized.startswith("http://") and not normalized.startswith("https://"):
        normalized = "https://" + normalized

    if "://api." in normalized:
        return normalized.replace("://api.", "://login.", 1)

    scheme, rest = normalized.split("://", 1)
    if rest.startswith("login."):
        return normalized
    return f"{scheme}://login.{rest}"


# ----------------------------- Input parsing ----------------------------- #

@dataclass
class WrapupCodeInput:
    name: str
    description: str = ""


def parse_codes_file(path: Path) -> List[WrapupCodeInput]:
    suffix = path.suffix.lower()

    if suffix == ".csv":
        return parse_codes_csv(path)
    if suffix == ".txt":
        return parse_codes_txt(path)
    if suffix == ".json":
        return parse_codes_json(path)

    raise ValueError(
        f"Unsupported input type: {path.suffix}. Use .csv, .txt, or .json"
    )


def parse_codes_csv(path: Path) -> List[WrapupCodeInput]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        field_map = {c.lower().strip(): c for c in (reader.fieldnames or [])}

        name_col = None
        for candidate in ("name", "code", "wrapupcode", "wrap_up_code", "wrapup_code"):
            if candidate in field_map:
                name_col = field_map[candidate]
                break

        desc_col = None
        for candidate in ("description", "desc", "notes"):
            if candidate in field_map:
                desc_col = field_map[candidate]
                break

        if not name_col:
            raise ValueError(
                "CSV must contain a code/name column. Accepted headers include: "
                "name, code, wrapupcode, wrap_up_code, wrapup_code"
            )

        rows: List[WrapupCodeInput] = []
        seen = set()
        for row in reader:
            name = (row.get(name_col) or "").strip()
            description = (row.get(desc_col) or "").strip() if desc_col else ""
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            rows.append(WrapupCodeInput(name=name, description=description))
        return rows


def parse_codes_txt(path: Path) -> List[WrapupCodeInput]:
    rows: List[WrapupCodeInput] = []
    seen = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Support either:
        #   Code Name
        #   Code Name | Description
        parts = [p.strip() for p in line.split("|", 1)]
        name = parts[0]
        description = parts[1] if len(parts) > 1 else ""
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(WrapupCodeInput(name=name, description=description))
    return rows


def parse_codes_json(path: Path) -> List[WrapupCodeInput]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("JSON input must be a list of strings or objects")

    rows: List[WrapupCodeInput] = []
    seen = set()
    for item in payload:
        if isinstance(item, str):
            name = item.strip()
            description = ""
        elif isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            description = str(item.get("description", "")).strip()
        else:
            continue

        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(WrapupCodeInput(name=name, description=description))
    return rows


# ----------------------------- API client -------------------------------- #

class GenesysClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self.session = requests.Session()
        self.login_base_url = login_url_from_base_url(self.base_url)
        self.access_token = self._get_access_token()
        self.session.headers.update({
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _get_access_token(self) -> str:
        token_url = f"{self.login_base_url}/oauth/token"
        response = requests.post(
            token_url,
            auth=(self.client_id, self.client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            detail = response.text.strip()
            raise RuntimeError(
                f"OAuth token request failed: POST {token_url} -> {response.status_code}: {detail}"
            )
        data = response.json()
        token = data.get("access_token")
        if not token:
            raise RuntimeError("OAuth token response did not include access_token")
        return token

    def request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None,
                json_body: Optional[Any] = None, expected: Tuple[int, ...] = (200, 201, 202, 204)) -> Any:
        url = f"{self.base_url}{path}"

        for attempt in range(1, 8):
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                json=json_body,
                timeout=self.timeout,
            )

            if response.status_code in expected:
                if response.status_code == 204 or not response.text.strip():
                    return None
                return response.json()

            if response.status_code in (429, 500, 502, 503, 504):
                retry_after = response.headers.get("Retry-After")
                sleep_for = float(retry_after) if retry_after else min(2 ** attempt, 30)
                log(
                    f"{method} {path} -> {response.status_code}; retrying in {sleep_for:.1f}s "
                    f"(attempt {attempt}/7)",
                    "WARN",
                )
                time.sleep(sleep_for)
                continue

            detail = response.text.strip()
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {detail}")

        raise RuntimeError(f"{method} {path} failed after repeated retries")

    def paged_get(self, path: str, *, page_size: int = 100, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        page = 1
        items: List[Dict[str, Any]] = []

        while True:
            merged = dict(params or {})
            merged["pageSize"] = page_size
            merged["pageNumber"] = page
            data = self.request("GET", path, params=merged, expected=(200,))

            entities = data.get("entities", [])
            items.extend(entities)

            page_count = data.get("pageCount")
            total = data.get("total")
            if page_count is not None and page >= page_count:
                break
            if not entities:
                break
            if total is not None and len(items) >= total:
                break
            page += 1

        return items


# ----------------------------- Genesys ops ------------------------------- #

def get_all_wrapup_codes(gc: GenesysClient) -> List[Dict[str, Any]]:
    return gc.paged_get("/api/v2/routing/wrapupcodes", page_size=100)


def create_wrapup_code(gc: GenesysClient, code: WrapupCodeInput) -> Dict[str, Any]:
    body = {
        "name": code.name,
        "description": code.description,
    }
    return gc.request("POST", "/api/v2/routing/wrapupcodes", json_body=body, expected=(200, 201))


def get_all_queues(gc: GenesysClient) -> List[Dict[str, Any]]:
    return gc.paged_get("/api/v2/routing/queues", page_size=100)


def get_queue_wrapup_codes(gc: GenesysClient, queue_id: str) -> List[Dict[str, Any]]:
    return gc.paged_get(f"/api/v2/routing/queues/{queue_id}/wrapupcodes", page_size=100)


def add_wrapup_codes_to_queue(gc: GenesysClient, queue_id: str, code_refs: List[Dict[str, str]]) -> Any:
    """
    Genesys supports adding up to 100 wrap-up codes to a queue in one call.
    This POST is intended to append the provided codes to the queue.
    """
    return gc.request(
        "POST",
        f"/api/v2/routing/queues/{queue_id}/wrapupcodes",
        json_body=code_refs,
        expected=(200, 201, 202, 204),
    )


def remove_wrapup_code_from_queue(gc: GenesysClient, queue_id: str, code_id: str) -> Any:
    return gc.request(
        "DELETE",
        f"/api/v2/routing/queues/{queue_id}/wrapupcodes/{code_id}",
        expected=(200, 202, 204),
    )


# ----------------------------- Main workflow ----------------------------- #

def chunked(items: List[Any], size: int) -> Iterable[List[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def write_report(path: Path, rows: List[Dict[str, Any]]) -> None:
    fieldnames = [
        "queue_name",
        "queue_id",
        "existing_before",
        "removed_existing",
        "missing_to_add",
        "added",
        "status",
        "detail",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create wrap-up codes and assign them to all queues starting with a prefix."
    )
    parser.add_argument("--input", required=True, help="Path to CSV/TXT/JSON file containing wrap-up codes")
    parser.add_argument("--queue-prefix", default="NCM", help="Queue name prefix to match (default: NCM)")
    parser.add_argument(
        "--remove-all-existing",
        action="store_true",
        help="Remove all currently assigned wrap-up codes from each matched queue before adding the requested set.",
    )
    parser.add_argument("--env-file", default="client.env", help="Path to client.env (default: client.env)")
    parser.add_argument("--report", default="wrapup_queue_assignment_report.csv", help="Output CSV report path")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually create codes and assign them. Without this flag, the script runs in dry-run mode.",
    )
    args = parser.parse_args()

    dry_run = not args.execute

    try:
        env = load_env_file(Path(args.env_file))
    except Exception as e:
        log(str(e), "ERR")
        return 2

    client_id = env.get("GENESYS_CLIENT_ID", "").strip()
    client_secret = env.get("GENESYS_CLIENT_SECRET", "").strip()
    region = env.get("GENESYS_REGION", "usw2").strip()
    base_url = env.get("GENESYS_BASE_URL", "").strip() or base_url_from_region(region)

    if not client_id or not client_secret:
        log("GENESYS_CLIENT_ID / GENESYS_CLIENT_SECRET missing in client.env", "ERR")
        return 2

    input_path = Path(args.input)
    if not input_path.exists():
        log(f"Input file not found: {input_path}", "ERR")
        return 2

    try:
        requested_codes = parse_codes_file(input_path)
    except Exception as e:
        log(f"Failed to parse input file: {e}", "ERR")
        return 2

    if not requested_codes:
        log("No wrap-up codes found in input file.", "ERR")
        return 2

    log(f"Mode: {'DRY-RUN' if dry_run else 'EXECUTE'}", "STEP")
    log(f"Base URL: {base_url}", "INFO")
    log(f"Login URL: {login_url_from_base_url(base_url)}", "INFO")
    log(f"Input codes: {len(requested_codes)}", "INFO")
    log(f"Queue prefix: {args.queue_prefix}", "INFO")
    log(f"Remove all existing first: {'YES' if args.remove_all_existing else 'NO'}", "INFO")

    try:
        gc = GenesysClient(base_url=base_url, client_id=client_id, client_secret=client_secret)
    except Exception as e:
        log(f"Failed to authenticate: {e}", "ERR")
        return 2

    # 1) Load existing wrap-up codes
    log("Loading existing wrap-up codes...", "STEP")
    all_codes = get_all_wrapup_codes(gc)
    code_by_name = {str(c.get("name", "")).casefold(): c for c in all_codes if c.get("name")}

    created_codes: List[Dict[str, Any]] = []
    already_existing_codes: List[Dict[str, Any]] = []
    failed_creations: List[str] = []

    # 2) Create missing codes
    for code in requested_codes:
        existing = code_by_name.get(code.name.casefold())
        if existing:
            already_existing_codes.append(existing)
            log(f"Exists already: {code.name}", "INFO")
            continue

        if dry_run:
            log(f"Would create wrap-up code: {code.name}", "WARN")
            fake = {"id": f"DRYRUN::{code.name}", "name": code.name, "description": code.description}
            created_codes.append(fake)
            code_by_name[code.name.casefold()] = fake
        else:
            try:
                created = create_wrapup_code(gc, code)
                created_codes.append(created)
                code_by_name[code.name.casefold()] = created
                log(f"Created wrap-up code: {code.name}", "OK")
            except Exception as e:
                failed_creations.append(code.name)
                log(f"Failed to create wrap-up code [{code.name}]: {e}", "ERR")

    if failed_creations:
        log(f"{len(failed_creations)} wrap-up code(s) failed to create. They will not be assigned.", "WARN")

    usable_codes = [code_by_name[c.name.casefold()] for c in requested_codes if c.name.casefold() in code_by_name]
    if not usable_codes:
        log("No usable wrap-up codes remain after creation phase.", "ERR")
        return 2

    # 3) Load queues and filter on prefix
    log("Loading queues...", "STEP")
    all_queues = get_all_queues(gc)
    matched_queues = [
        q for q in all_queues
        if str(q.get("name", "")).startswith(args.queue_prefix)
    ]

    if not matched_queues:
        log(f"No queues found starting with prefix: {args.queue_prefix}", "WARN")
        return 0

    log(f"Matched queues: {len(matched_queues)}", "INFO")

    report_rows: List[Dict[str, Any]] = []

    # 4) Remove / assign wrap-up codes queue by queue
    for idx, queue in enumerate(matched_queues, start=1):
        queue_id = str(queue.get("id"))
        queue_name = str(queue.get("name"))
        log(f"[{idx}/{len(matched_queues)}] Queue: {queue_name}", "STEP")

        try:
            current = get_queue_wrapup_codes(gc, queue_id)
            current_name_map = {str(c.get("name", "")).casefold(): c for c in current if c.get("name")}
            removed_count = 0
            add_target_codes = usable_codes

            detail_parts = []
            status = "no_changes"
            added_count = 0

            if args.remove_all_existing and current:
                if dry_run:
                    removed_count = len(current)
                    detail_parts.append(f"Would remove {removed_count} existing wrap-up code(s)")
                    status = "would_remove_and_add" if usable_codes else "would_remove"
                    log(f"{queue_name}: would remove {removed_count} existing wrap-up code(s)", "WARN")
                else:
                    for existing_code in current:
                        existing_id = str(existing_code.get("id", "")).strip()
                        if not existing_id:
                            continue
                        remove_wrapup_code_from_queue(gc, queue_id, existing_id)
                        removed_count += 1
                    detail_parts.append(f"Removed {removed_count} existing wrap-up code(s)")
                    status = "removed_and_added" if usable_codes else "removed"
                    log(f"{queue_name}: removed {removed_count} existing wrap-up code(s)", "OK")

                # after clearing the queue, all requested codes become candidates to add
                missing = usable_codes[:]
            else:
                missing = [c for c in usable_codes if str(c.get("name", "")).casefold() not in current_name_map]

            if not missing:
                if not detail_parts:
                    detail_parts.append("All requested wrap-up codes already assigned.")
                    log(f"{queue_name}: nothing to add", "INFO")
            else:
                refs = [{"id": str(c["id"]), "name": str(c["name"])} for c in missing]

                if dry_run:
                    detail_parts.append(f"Would add {len(refs)} wrap-up code(s)")
                    added_count = len(refs)
                    if status == "no_changes":
                        status = "would_add"
                    log(f"{queue_name}: would add {len(refs)} wrap-up code(s)", "WARN")
                else:
                    for batch in chunked(refs, 100):
                        add_wrapup_codes_to_queue(gc, queue_id, batch)
                    detail_parts.append(f"Added {len(refs)} wrap-up code(s)")
                    added_count = len(refs)
                    if status == "no_changes":
                        status = "added"
                    elif status == "removed":
                        status = "removed_and_added"
                    log(f"{queue_name}: added {len(refs)} wrap-up code(s)", "OK")

            report_rows.append({
                "queue_name": queue_name,
                "queue_id": queue_id,
                "existing_before": len(current),
                "removed_existing": removed_count,
                "missing_to_add": len(missing),
                "added": added_count,
                "status": status,
                "detail": "; ".join(detail_parts) if detail_parts else "",
            })
        except Exception as e:
            log(f"{queue_name}: failed -> {e}", "ERR")
            report_rows.append({
                "queue_name": queue_name,
                "queue_id": queue_id,
                "existing_before": "",
                "removed_existing": "",
                "missing_to_add": "",
                "added": 0,
                "status": "error",
                "detail": str(e),
            })

    report_path = Path(args.report)
    write_report(report_path, report_rows)
    log(f"Report written: {report_path}", "OK")

    changed_count = len([
        r for r in report_rows
        if r["status"] in ("added", "would_add", "removed", "would_remove", "removed_and_added", "would_remove_and_add")
    ])
    log(
        f"Done. Queues processed={len(report_rows)}; queues with requested changes={changed_count}; "
        f"codes requested={len(requested_codes)}; created={len(created_codes)}; existing={len(already_existing_codes)}",
        "OK",
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
