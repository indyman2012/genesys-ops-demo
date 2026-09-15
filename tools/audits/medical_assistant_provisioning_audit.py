#!/usr/bin/env python3
"""
Active Medical Assistant users missing Communicate - User and/or a Genesys phone.

Title match: case-insensitive substring "medical assistant" (covers Center Float,
Bilingual, Medix/HBH variants, contractor "(c)" titles, etc.).

Phone assigned = any of:
  - Edge phone linked via webRtcUser / owner / assignedUser
  - Associated station (userId or webRtcUserId)
  - Edge phone named "{Name} Web"

Read-only. Outputs CSV under outputs/.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, setup_path, tool_log, write_csv
from core.genesys_client import GenesysError, safe_str

setup_path()

DEFAULT_TITLE_NEEDLE = "medical assistant"
COMMUNICATE_USER_ROLE = "communicate - user"
PHONE_PAGE_SIZE = 50
PHONE_FETCH_RETRIES = 4

OUTPUT_FIELDS = [
    "user_id",
    "name",
    "email",
    "title",
    "has_communicate_user_role",
    "has_phone",
    "phone_or_station",
    "roles",
    "issues",
]


def normalize_phone_key(name: str) -> str:
    parts = " ".join((name or "").split())
    folded = parts.casefold()
    if folded.endswith(" web"):
        return folded
    if folded.endswith("web") and len(folded) > 3:
        return f"{folded[:-3].rstrip()} web"
    return folded


def title_matches(title: str, needle: str) -> bool:
    return needle.casefold() in (title or "").casefold()


def load_phones(client) -> list[dict]:
    page = 1
    phones: list[dict] = []
    while True:
        for attempt in range(PHONE_FETCH_RETRIES):
            try:
                payload = client.request(
                    "GET",
                    "/api/v2/telephony/providers/edges/phones",
                    params={"pageSize": PHONE_PAGE_SIZE, "pageNumber": page},
                )
                break
            except GenesysError as exc:
                if "408" in str(exc) and attempt + 1 < PHONE_FETCH_RETRIES:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
        entities = payload.get("entities") or []
        phones.extend(entities)
        page_count = int(payload.get("pageCount") or 1)
        if page >= page_count:
            break
        page += 1
    return phones


def index_phones(phones: list[dict]) -> tuple[set[str], dict[str, list[dict]]]:
    by_user: set[str] = set()
    by_name: dict[str, list[dict]] = defaultdict(list)
    for phone in phones:
        name = safe_str(phone.get("name"))
        if name:
            by_name[normalize_phone_key(name)].append(phone)
        for key in ("webRtcUser", "owner", "assignedUser"):
            obj = phone.get(key) or {}
            if isinstance(obj, dict):
                uid = safe_str(obj.get("id"))
                if uid:
                    by_user.add(uid)
    return by_user, by_name


def index_associated_stations(client) -> tuple[set[str], dict[str, str]]:
    by_user: set[str] = set()
    names: dict[str, str] = {}
    for station in client.paged_get("/api/v2/stations", params={"pageSize": 100}):
        if (station.get("status") or "").upper() != "ASSOCIATED":
            continue
        uid = safe_str(station.get("userId") or station.get("webRtcUserId"))
        if not uid:
            continue
        by_user.add(uid)
        names[uid] = safe_str(station.get("name"))
    return by_user, names


def user_roles(client, user_id: str) -> list[str]:
    resp = client.request("GET", f"/api/v2/users/{user_id}/roles")
    roles = resp.get("roles") if isinstance(resp.get("roles"), list) else []
    names: list[str] = []
    for role in roles:
        if not isinstance(role, dict):
            continue
        name = safe_str(role.get("name")).strip()
        if name:
            names.append(name)
    return sorted(set(names))


def has_communicate_user_role(role_names: list[str]) -> bool:
    return any(name.casefold() == COMMUNICATE_USER_ROLE for name in role_names)


def phone_assignment(
    user_id: str,
    user_name: str,
    *,
    phone_users: set[str],
    phone_names: dict[str, list[dict]],
    station_users: set[str],
    station_names: dict[str, str],
) -> tuple[bool, str]:
    if user_id in station_users:
        return True, station_names.get(user_id, "")
    named = phone_names.get(normalize_phone_key(f"{user_name.strip()} Web"), [])
    if named:
        return True, safe_str(named[0].get("name"))
    if user_id in phone_users:
        return True, "(edge phone owner/webrtc)"
    return False, ""


def build_gap_rows(
    client,
    *,
    title_needle: str,
    include_inactive: bool,
    phone_users: set[str],
    phone_names: dict[str, list[dict]],
    station_users: set[str],
    station_names: dict[str, str],
) -> tuple[list[dict[str, str]], int]:
    rows: list[dict[str, str]] = []
    ma_count = 0
    params = {} if include_inactive else {"state": "active"}
    for user in client.paged_get("/api/v2/users", params=params):
        title = safe_str(user.get("title"))
        if not title_matches(title, title_needle):
            continue
        ma_count += 1
        user_id = safe_str(user.get("id"))
        name = safe_str(user.get("name"))
        email = safe_str(user.get("email"))
        roles = user_roles(client, user_id)
        has_role = has_communicate_user_role(roles)
        has_phone, phone_label = phone_assignment(
            user_id,
            name,
            phone_users=phone_users,
            phone_names=phone_names,
            station_users=station_users,
            station_names=station_names,
        )
        if has_role and has_phone:
            continue
        issues: list[str] = []
        if not has_role:
            issues.append("Missing Communicate - User role")
        if not has_phone:
            issues.append("No phone assigned")
        rows.append(
            {
                "user_id": user_id,
                "name": name,
                "email": email,
                "title": title,
                "has_communicate_user_role": "yes" if has_role else "no",
                "has_phone": "yes" if has_phone else "no",
                "phone_or_station": phone_label,
                "roles": " | ".join(roles),
                "issues": "; ".join(issues),
            }
        )
    rows.sort(key=lambda row: (row["issues"], row["name"].casefold()))
    return rows, ma_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Medical Assistant users missing Communicate - User role and/or phone."
    )
    add_env_arg(parser)
    parser.add_argument(
        "--title-needle",
        default=DEFAULT_TITLE_NEEDLE,
        help=f'Case-insensitive title substring (default: "{DEFAULT_TITLE_NEEDLE}").',
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include inactive/deleted users.",
    )
    parser.add_argument("--output", help="Output CSV path (default: outputs/medical_assistant_provisioning_gaps_<ts>.csv)")
    args = parser.parse_args()

    client = create_client(env_file=args.env_file)

    tool_log("info", "Loading edge phones...")
    phones = load_phones(client)
    phone_users, phone_names = index_phones(phones)
    tool_log("info", f"Indexed {len(phones)} phones")

    tool_log("info", "Loading associated stations...")
    station_users, station_names = index_associated_stations(client)
    tool_log("info", f"Found {len(station_users)} associated stations")

    rows, ma_count = build_gap_rows(
        client,
        title_needle=args.title_needle,
        include_inactive=args.include_inactive,
        phone_users=phone_users,
        phone_names=phone_names,
        station_users=station_users,
        station_names=station_names,
    )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output else output_path(f"medical_assistant_provisioning_gaps_{stamp}.csv")
    write_csv(out_path, rows, OUTPUT_FIELDS)

    missing_role = sum(1 for row in rows if row["has_communicate_user_role"] == "no")
    missing_phone = sum(1 for row in rows if row["has_phone"] == "no")
    tool_log("ok", f"Active MAs scanned: {ma_count}")
    tool_log("ok", f"Gaps found: {len(rows)} ({missing_role} missing role, {missing_phone} missing phone)")
    tool_log("ok", f"Wrote {out_path}")
    for row in rows:
        tool_log("info", f"  {row['name']} — {row['issues']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
