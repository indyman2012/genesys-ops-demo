#!/usr/bin/env python3
"""
Genesys Cloud role / permission / CX1-vs-CX2 review for one user.

User target:
    aaron.goble@archwellhealth.com

What it does
------------
1. Authenticates with client.env
2. Resolves the user by email
3. Tries to read role/permission data from:
      GET /api/v2/users/{userId}/roles
   and falls back to:
      GET /api/v2/authorization/subjects/{userId}
4. If needed, expands role details via:
      GET /api/v2/authorization/roles/{roleId}
5. Produces:
   - role names
   - effective permission list (best effort)
   - a heuristic recommendation for CX1 vs CX2

Important limitation
--------------------
Genesys does not expose a simple API flag that says "this user requires CX2".
This script applies a practical ruleset:
- permissions/features that look digital / omnichannel / engagement-heavy
  are flagged as likely needing CX2
- everything else is treated as likely CX1-compatible

That means the output is an OPERATIONS AID, not an official commercial ruling.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

DEFAULT_REGION = "usw2.pure.cloud"
TARGET_EMAIL = "aaron.goble@archwellhealth.com"
OUTPUT_FILE = "aaron_goble_license_review.json"

# Heuristic keywords that strongly suggest CX2 / omnichannel / digital use cases.
CX2_KEYWORDS = [
    "digital",
    "message",
    "messaging",
    "webmessaging",
    "web messaging",
    "email",
    "chat",
    "sms",
    "social",
    "open messaging",
    "instagram",
    "facebook",
    "twitter",
    "x ",
    "whatsapp",
    "line",
    "apple",
    "journey",
    "predictive engagement",
    "predictiveengagement",
    "campaign",  # keep in report, but this alone may not always require CX2
]

# A narrower subset we consider stronger evidence than generic words.
STRONG_CX2_PATTERNS = [
    r"\bdigital\b",
    r"\bemail\b",
    r"\bchat\b",
    r"\bsms\b",
    r"\bmessaging\b",
    r"\bweb ?messaging\b",
    r"\bsocial\b",
    r"\bopen ?messaging\b",
    r"\bwhatsapp\b",
    r"\bfacebook\b",
    r"\binstagram\b",
    r"\bjourney\b",
    r"\bpredictive ?engagement\b",
]


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


def extract_candidate_emails(user_obj: Dict[str, Any]) -> List[str]:
    candidates: List[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str):
            email = value.strip().lower()
            if "@" in email:
                candidates.append(email)

    add(user_obj.get("email"))
    add(user_obj.get("username"))

    pci = user_obj.get("primaryContactInfo")
    if isinstance(pci, list):
        for item in pci:
            if isinstance(item, dict):
                add(item.get("address"))
    elif isinstance(pci, dict):
        add(pci.get("email"))
        add(pci.get("addressEmail"))

    for key in ["addresses", "routingAddresses"]:
        arr = user_obj.get(key)
        if isinstance(arr, list):
            for item in arr:
                if isinstance(item, dict):
                    add(item.get("address"))
                    add(item.get("value"))
                    add(item.get("display"))

    unique = []
    seen = set()
    for item in candidates:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def get_user_by_email(sess: requests.Session, api_base: str, email: str) -> Dict[str, Any]:
    page = 1
    while True:
        resp = retry_request(
            sess,
            "GET",
            f"{api_base}/api/v2/users",
            params={"pageSize": 100, "pageNumber": page},
        )
        resp.raise_for_status()
        body = resp.json()
        entities = body.get("entities", [])

        for user in entities:
            if email.lower() in extract_candidate_emails(user):
                return user

        if page >= body.get("pageCount", 1):
            break
        if not entities:
            break
        page += 1

    raise SystemExit(f"No user found for {email}")


def normalize_permissions_from_blob(blob: Any) -> Set[str]:
    """
    Best-effort extraction because Genesys role/subject payloads can vary.
    """
    perms: Set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).lower()
                if lowered in {"permission", "permissions"}:
                    if isinstance(value, str):
                        perms.add(value)
                    elif isinstance(value, list):
                        for item in value:
                            if isinstance(item, str):
                                perms.add(item)
                            elif isinstance(item, dict):
                                for subk in ("permission", "name", "id"):
                                    subv = item.get(subk)
                                    if isinstance(subv, str):
                                        perms.add(subv)
                    elif isinstance(value, dict):
                        for subk in ("permission", "name", "id"):
                            subv = value.get(subk)
                            if isinstance(subv, str):
                                perms.add(subv)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(blob)
    return perms


def get_user_roles(sess: requests.Session, api_base: str, user_id: str) -> Dict[str, Any]:
    resp = retry_request(sess, "GET", f"{api_base}/api/v2/users/{user_id}/roles")
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    return resp.json()


def get_subject_roles(sess: requests.Session, api_base: str, user_id: str) -> Dict[str, Any]:
    resp = retry_request(sess, "GET", f"{api_base}/api/v2/authorization/subjects/{user_id}")
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    return resp.json()


def get_role_detail(sess: requests.Session, api_base: str, role_id: str) -> Dict[str, Any]:
    resp = retry_request(sess, "GET", f"{api_base}/api/v2/authorization/roles/{role_id}")
    resp.raise_for_status()
    return resp.json()


def extract_roles_from_blob(blob: Any) -> List[Dict[str, str]]:
    roles: List[Dict[str, str]] = []

    def maybe_add(role_obj: Dict[str, Any]) -> None:
        role_id = str(role_obj.get("id", "") or "")
        name = str(role_obj.get("name", "") or role_obj.get("defaultRoleId", "") or "")
        if role_id or name:
            roles.append({"id": role_id, "name": name})

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if any(k in node for k in ("defaultRoleId", "roleType")) or ("id" in node and "name" in node):
                maybe_add(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(blob)

    dedup: List[Dict[str, str]] = []
    seen = set()
    for role in roles:
        key = (role["id"], role["name"])
        if key not in seen:
            seen.add(key)
            dedup.append(role)
    return dedup


def classify_permissions_for_cx(permissions: List[str], role_names: List[str]) -> Dict[str, Any]:
    combined = permissions + role_names
    lower_items = [item.lower() for item in combined if isinstance(item, str)]

    strong_hits: List[str] = []
    soft_hits: List[str] = []

    for item in lower_items:
        for pattern in STRONG_CX2_PATTERNS:
            if re.search(pattern, item):
                strong_hits.append(item)
                break

    for item in lower_items:
        if item in strong_hits:
            continue
        if any(keyword in item for keyword in CX2_KEYWORDS):
            soft_hits.append(item)

    strong_hits = sorted(set(strong_hits))
    soft_hits = sorted(set(soft_hits))

    if strong_hits:
        recommendation = "LIKELY_CX2"
        rationale = "Strong digital/omnichannel-style permissions or roles were detected."
    elif soft_hits:
        recommendation = "REVIEW_FOR_CX2"
        rationale = "Some permissions/roles look like they may need CX2 or an add-on, but evidence is weaker."
    else:
        recommendation = "LIKELY_CX1"
        rationale = "No obvious digital/omnichannel indicators were found in the fetched roles/permissions."

    return {
        "recommendation": recommendation,
        "rationale": rationale,
        "strong_cx2_indicators": strong_hits,
        "soft_cx2_indicators": soft_hits,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default=TARGET_EMAIL, help=f"Target user email (default: {TARGET_EMAIL})")
    parser.add_argument("--output", default=OUTPUT_FILE, help=f"Output JSON file (default: {OUTPUT_FILE})")
    args = parser.parse_args()

    load_env_file(Path("client.env").resolve())
    region = get_region()
    token = get_token(region, get_client_id(), get_client_secret())
    sess = build_session(token)
    api_base = f"https://api.{region}"

    print(f"Resolving user: {args.email}")
    user = get_user_by_email(sess, api_base, args.email)
    user_id = str(user["id"])
    print(f"User ID: {user_id}")

    print("Fetching role/permission data...")
    users_roles_blob = get_user_roles(sess, api_base, user_id)
    subject_roles_blob = get_subject_roles(sess, api_base, user_id)

    roles = extract_roles_from_blob(users_roles_blob) or extract_roles_from_blob(subject_roles_blob)

    permission_set = set()
    permission_set.update(normalize_permissions_from_blob(users_roles_blob))
    permission_set.update(normalize_permissions_from_blob(subject_roles_blob))

    # If role/subject responses didn't expose permissions directly, expand the roles.
    expanded_roles: List[Dict[str, Any]] = []
    if roles:
        for role in roles:
            role_id = role.get("id", "")
            if not role_id:
                continue
            try:
                detail = get_role_detail(sess, api_base, role_id)
                expanded_roles.append(detail)
                permission_set.update(normalize_permissions_from_blob(detail))
            except Exception as exc:
                expanded_roles.append({
                    "id": role_id,
                    "name": role.get("name", ""),
                    "_error": str(exc),
                })

    permissions = sorted(permission_set)
    role_names = sorted({r.get("name", "") for r in roles if r.get("name")})

    cx_assessment = classify_permissions_for_cx(permissions, role_names)

    output = {
        "email": args.email,
        "region": region,
        "user": {
            "id": user_id,
            "name": user.get("name"),
            "email": user.get("email"),
            "username": user.get("username"),
            "department": user.get("department"),
            "title": user.get("title"),
            "state": user.get("state"),
        },
        "roles": roles,
        "role_names": role_names,
        "effective_permissions_best_effort": permissions,
        "cx_license_assessment": cx_assessment,
        "raw_users_roles_response": users_roles_blob,
        "raw_authorization_subject_response": subject_roles_blob,
        "expanded_role_details": expanded_roles,
        "notes": [
            "This script uses a best-effort Genesys API expansion to collect roles and permissions.",
            "The CX1 vs CX2 recommendation is heuristic. Genesys ties role cost to the most expensive permission in the role, but does not expose a single direct API field that says CX1 or CX2 is required for a user.",
        ],
    }

    Path(args.output).write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"User: {output['user']['name']} ({output['user']['email']})")
    print(f"Roles found: {len(roles)}")
    print(f"Permissions found (best effort): {len(permissions)}")
    print(f"Recommendation: {cx_assessment['recommendation']}")
    print(f"Saved to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
