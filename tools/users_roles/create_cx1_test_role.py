#!/usr/bin/env python3
"""
Create a Genesys Cloud custom role named "CX1 Test" (or another name you supply)
using Aaron Goble's effective permission policies as a starting point, minus the
permissions/domains flagged as likely CX2-oriented.

Default behavior
----------------
DRY RUN ONLY. Nothing is created unless you pass --apply.

Inputs
------
- client.env in the same directory
- aaron_goble_license_review.json in the same directory, or pass --review-json

What it does
------------
1. Reads the Aaron review JSON
2. Builds a filtered permission policy list by removing policies tied to the
   flagged CX2 indicators found in the JSON (for Aaron, this is mainly chat and journey)
3. Optionally creates a custom role
4. Optionally updates that role with the filtered permission policies

Notes
-----
- This is intentionally conservative: it removes only domains/entities indicated by the
  review file's strong/soft CX2 indicators.
- Genesys Cloud roles determine licensing by the highest-cost permission in the role.
- This script is meant to help you build a lab/test role for licensing validation.
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

DEFAULT_REGION = "usw2.pure.cloud"


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
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_region() -> str:
    return os.getenv("GENESYS_CLOUD_REGION", "").strip() or DEFAULT_REGION


def get_client_id() -> str:
    value = os.getenv("GENESYS_CLIENT_ID", "").strip() or os.getenv("CLIENT_ID", "").strip()
    if not value:
        raise SystemExit("Missing client id in client.env (GENESYS_CLIENT_ID or CLIENT_ID)")
    return value


def get_client_secret() -> str:
    value = os.getenv("GENESYS_CLIENT_SECRET", "").strip() or os.getenv("CLIENT_SECRET", "").strip()
    if not value:
        raise SystemExit("Missing client secret in client.env (GENESYS_CLIENT_SECRET or CLIENT_SECRET)")
    return value


def get_token(region: str, client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    auth = base64.b64encode(raw).decode("ascii")
    resp = requests.post(
        f"https://login.{region}/oauth/token",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials"},
        timeout=30,
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


def retry_request(sess: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
    last_resp: Optional[requests.Response] = None
    for attempt in range(8):
        resp = sess.request(method, url, timeout=30, **kwargs)
        last_resp = resp
        if resp.status_code == 429 or resp.status_code >= 500:
            delay = min(2 ** attempt, 30) + random.random()
            print(f"Retry {attempt + 1}: {delay:.2f}s")
            time.sleep(delay)
            continue
        return resp
    assert last_resp is not None
    return last_resp


def derive_denied_domains_and_entities(review_data: Dict[str, Any]) -> Tuple[Set[str], Set[Tuple[str, str]], List[str]]:
    """
    Build removal rules from the flagged indicators in the review JSON.
    Example indicators:
      chat:chat:access
      journey:event:view
    """
    indicators = []
    assess = review_data.get("cx_license_assessment") or {}
    indicators.extend(assess.get("strong_cx2_indicators") or [])
    indicators.extend(assess.get("soft_cx2_indicators") or [])

    denied_domains: Set[str] = set()
    denied_domain_entity: Set[Tuple[str, str]] = set()
    notes: List[str] = []

    for item in indicators:
        if not isinstance(item, str):
            continue
        parts = item.split(":")
        if len(parts) >= 2:
            domain = parts[0].strip()
            entity = parts[1].strip()
            if domain:
                denied_domains.add(domain)
            if domain and entity:
                denied_domain_entity.add((domain, entity))

    # If no indicators were parsed, use the broad domains suggested by Aaron's current output.
    if not denied_domains:
        denied_domains.update({"chat", "journey"})

    notes.append(f"Denied domains derived from review file: {sorted(denied_domains)}")
    notes.append(
        "Policies matching denied domains/entities will be excluded from the custom test role."
    )
    return denied_domains, denied_domain_entity, notes


def filter_permission_policies(
    policies: List[Dict[str, Any]],
    denied_domains: Set[str],
    denied_domain_entity: Set[Tuple[str, str]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []

    for policy in policies:
        domain = str(policy.get("domain", "") or "").strip()
        entity = str(policy.get("entityName", "") or "").strip()
        if domain in denied_domains or (domain, entity) in denied_domain_entity:
            removed.append(policy)
        else:
            kept.append(policy)

    return kept, removed


def create_role(sess: requests.Session, api_base: str, name: str, description: str) -> Dict[str, Any]:
    payload = {
        "name": name,
        "description": description,
    }
    resp = retry_request(sess, "POST", f"{api_base}/api/v2/authorization/roles", json=payload)
    resp.raise_for_status()
    return resp.json()


def get_role(sess: requests.Session, api_base: str, role_id: str) -> Dict[str, Any]:
    resp = retry_request(sess, "GET", f"{api_base}/api/v2/authorization/roles/{role_id}")
    resp.raise_for_status()
    return resp.json()


def update_role(sess: requests.Session, api_base: str, role_id: str, name: str, description: str, permission_policies: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Best-effort full role update. Genesys SDK documentation exposes PUT /api/v2/authorization/roles/{roleId}
    for organization role updates, so we send the custom role definition back with the filtered policies.
    """
    payload = {
        "name": name,
        "description": description,
        "permissionPolicies": permission_policies,
    }
    resp = retry_request(sess, "PUT", f"{api_base}/api/v2/authorization/roles/{role_id}", json=payload)
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-json", default="aaron_goble_license_review.json", help="Path to Aaron review JSON")
    parser.add_argument("--role-name", default="CX1 Test", help='Role name to create/update (default: "CX1 Test")')
    parser.add_argument("--description", default="Voice-only test role derived from Aaron Goble minus flagged CX2 permissions", help="Role description")
    parser.add_argument("--apply", action="store_true", help="Actually create/update the role. Default is dry run.")
    parser.add_argument("--output", default="cx1_test_role_preview.json", help="Output preview/result JSON")
    args = parser.parse_args()

    review_path = Path(args.review_json).resolve()
    if not review_path.exists():
        raise SystemExit(f"Review JSON not found: {review_path}")

    review_data = json.loads(review_path.read_text(encoding="utf-8"))

    raw_roles_blob = review_data.get("raw_users_roles_response") or {}
    policies = raw_roles_blob.get("permissionPolicies") or []
    if not policies:
        raise SystemExit("No permissionPolicies found in the review JSON. Cannot build test role.")

    denied_domains, denied_domain_entity, notes = derive_denied_domains_and_entities(review_data)
    kept_policies, removed_policies = filter_permission_policies(policies, denied_domains, denied_domain_entity)

    preview = {
        "source_user": review_data.get("user") or {},
        "role_name": args.role_name,
        "description": args.description,
        "denied_domains": sorted(denied_domains),
        "denied_domain_entity_pairs": sorted([list(x) for x in denied_domain_entity]),
        "source_permission_policy_count": len(policies),
        "kept_permission_policy_count": len(kept_policies),
        "removed_permission_policy_count": len(removed_policies),
        "removed_permission_policies": removed_policies,
        "kept_permission_policies": kept_policies,
        "notes": notes,
        "mode": "APPLY" if args.apply else "DRY_RUN",
    }

    if not args.apply:
        Path(args.output).write_text(json.dumps(preview, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Dry run only. Preview saved to {args.output}")
        print(f"Kept policies   : {len(kept_policies)}")
        print(f"Removed policies: {len(removed_policies)}")
        print(f"Denied domains  : {sorted(denied_domains)}")
        return 0

    load_env_file(Path("client.env").resolve())
    region = get_region()
    token = get_token(region, get_client_id(), get_client_secret())
    sess = build_session(token)
    api_base = f"https://api.{region}"

    print(f"Creating role: {args.role_name}")
    created = create_role(sess, api_base, args.role_name, args.description)
    role_id = str(created.get("id", "") or "")
    if not role_id:
        raise SystemExit("Role creation did not return an id.")

    print(f"Created role id: {role_id}")
    print("Updating role with filtered permission policies...")
    updated = update_role(sess, api_base, role_id, args.role_name, args.description, kept_policies)

    result = copy.deepcopy(preview)
    result["created_role"] = created
    result["updated_role"] = updated
    result["created_role_id"] = role_id

    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Done. Result saved to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
