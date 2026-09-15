#!/usr/bin/env python3
"""
Align active Medical Assistant telephony profile to org peer spec:

  - WORK Genesys extension on user profile
  - Primary Voice = WORK extension (not Teams OTHER)
  - OTHER / Teams phone preserved when present

Assigns extensions from site-inferred pool (15100-15999 vs 8300-8500).
Read-only by default; pass --apply to PATCH users.
"""
from __future__ import annotations

import argparse
import copy
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import (
    add_apply_arg,
    add_env_arg,
    create_client,
    output_path,
    print_mode_banner,
    setup_path,
    tool_log,
    write_csv,
)
from core.genesys_client import GenesysError, safe_str

setup_path()

DEFAULT_TITLE_NEEDLE = "medical assistant"
POOL_LOW_DEFAULT = (8300, 8500)
POOL_HIGH = (15100, 15999)
PHONE_PAGE_SIZE = 50
PHONE_FETCH_RETRIES = 4

OUTPUT_FIELDS = [
    "user_id",
    "name",
    "email",
    "title",
    "site",
    "work_extension_before",
    "work_extension_after",
    "primary_before",
    "primary_after",
    "actions",
    "status",
    "notes",
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
        phones.extend(payload.get("entities") or [])
        if page >= int(payload.get("pageCount") or 1):
            break
        page += 1
    return phones


def index_phones(phones: list[dict]) -> tuple[dict[str, str], dict[str, list[dict]]]:
    site_by_user: dict[str, str] = {}
    by_name: dict[str, list[dict]] = defaultdict(list)
    for phone in phones:
        name = safe_str(phone.get("name"))
        if name:
            by_name[normalize_phone_key(name)].append(phone)
        site = safe_str((phone.get("site") or {}).get("name"))
        for key in ("webRtcUser", "owner", "assignedUser"):
            obj = phone.get(key) or {}
            if isinstance(obj, dict):
                uid = safe_str(obj.get("id"))
                if uid and site:
                    site_by_user[uid] = site
    return site_by_user, by_name


def work_extension(user: dict) -> str:
    for addr in user.get("addresses") or []:
        if not isinstance(addr, dict):
            continue
        if safe_str(addr.get("mediaType")).upper() != "PHONE":
            continue
        if safe_str(addr.get("type")).upper() != "WORK":
            continue
        digits = "".join(ch for ch in safe_str(addr.get("extension")) if ch.isdigit())
        if digits:
            return digits
    return ""


def primary_phone_summary(user: dict) -> str:
    for item in user.get("primaryContactInfo") or []:
        if not isinstance(item, dict):
            continue
        if safe_str(item.get("mediaType")).upper() != "PHONE":
            continue
        if safe_str(item.get("type")).upper() != "PRIMARY":
            continue
        ext = safe_str(item.get("extension")).strip()
        if ext:
            return f"WORK {ext}"
        return f"OTHER {safe_str(item.get('address') or item.get('display'))}"
    return "none"


def primary_is_work_extension(user: dict, ext: str) -> bool:
    if not ext:
        return False
    for item in user.get("primaryContactInfo") or []:
        if not isinstance(item, dict):
            continue
        if safe_str(item.get("mediaType")).upper() != "PHONE":
            continue
        if safe_str(item.get("type")).upper() != "PRIMARY":
            continue
        return safe_str(item.get("extension")).strip() == ext
    return False


def collect_used_extensions(client) -> set[int]:
    """Build used-extension set from user list payloads (no per-user GET)."""
    used: set[int] = set()
    for user in client.paged_get("/api/v2/users", params={"state": "any"}):
        ext = work_extension(user)
        if ext:
            used.add(int(ext))
    return used


def load_ma_details(
    client,
    *,
    title_needle: str,
    user_filter: str | None = None,
) -> list[dict]:
    """Load full user records for active MAs (optionally filtered by name)."""
    details: list[dict] = []
    for user in client.paged_get("/api/v2/users", params={"state": "active"}):
        name = safe_str(user.get("name"))
        title = safe_str(user.get("title"))
        if user_filter and user_filter.casefold() not in name.casefold():
            continue
        if not title_matches(title, title_needle):
            continue
        details.append(client.request("GET", f"/api/v2/users/{user['id']}"))
    return details


def extension_in_pool(ext: str, pool: tuple[int, int]) -> bool:
    if not ext:
        return False
    value = int(ext)
    low, high = pool
    return low <= value <= high


def pool_for_site(site_name: str) -> tuple[int, int]:
    site = (site_name or "").upper()
    if site.startswith("PHX") or site.startswith("MES") or "MES" in site[:5]:
        return POOL_HIGH
    return POOL_LOW_DEFAULT


def resolve_phone_site(
    client,
    user_name: str,
    by_name: dict[str, list[dict]],
) -> str:
    phones = by_name.get(normalize_phone_key(f"{user_name.strip()} Web"), [])
    if not phones:
        return ""
    phone = phones[0]
    site = safe_str((phone.get("site") or {}).get("name"))
    if site:
        return site
    phone_id = safe_str(phone.get("id"))
    if not phone_id:
        return ""
    try:
        detail = client.request("GET", f"/api/v2/telephony/providers/edges/phones/{phone_id}")
    except GenesysError:
        return ""
    return safe_str((detail.get("site") or {}).get("name"))


def user_site(
    client,
    user_id: str,
    user_name: str,
    site_by_user: dict[str, str],
    by_name: dict[str, list[dict]],
) -> str:
    if user_id in site_by_user:
        return site_by_user[user_id]
    return resolve_phone_site(client, user_name, by_name)


def next_extension(used: set[int], pool: tuple[int, int]) -> str | None:
    low, high = pool
    for candidate in range(low, high + 1):
        if candidate not in used:
            return str(candidate)
    return None


def build_patch(user: dict, extension: str) -> dict:
    addresses = copy.deepcopy(user.get("addresses") or [])
    work = None
    for addr in addresses:
        if not isinstance(addr, dict):
            continue
        if safe_str(addr.get("mediaType")).upper() == "PHONE" and safe_str(addr.get("type")).upper() == "WORK":
            work = addr
            break
    if work is None:
        work = {"mediaType": "PHONE", "type": "WORK", "address": "", "countryCode": "US"}
        addresses.append(work)
    work["extension"] = extension
    work["display"] = extension
    work.pop("voicePrimary", None)
    work.pop("primary", None)

    primary_contact_info: list[dict] = []
    for item in user.get("primaryContactInfo") or []:
        if not isinstance(item, dict):
            continue
        if safe_str(item.get("mediaType")).upper() == "PHONE" and safe_str(item.get("type")).upper() == "PRIMARY":
            continue
        primary_contact_info.append(copy.deepcopy(item))
    primary_contact_info.append(
        {
            "display": extension,
            "mediaType": "PHONE",
            "type": "PRIMARY",
            "extension": extension,
        }
    )

    body: dict = {
        "addresses": addresses,
        "primaryContactInfo": primary_contact_info,
    }
    if user.get("version") is not None:
        body["version"] = user.get("version")
    return body


def evaluate_user(
    user_summary: dict,
    *,
    client,
    title_needle: str,
    site_by_user: dict[str, str],
    by_name: dict[str, list[dict]],
    used_extensions: set[int],
) -> dict[str, str] | None:
    title = safe_str(user_summary.get("title"))
    if not title_matches(title, title_needle):
        return None
    if safe_str(user_summary.get("state")).lower() != "active":
        return None

    user_id = safe_str(user_summary.get("id"))
    name = safe_str(user_summary.get("name"))
    email = safe_str(user_summary.get("email"))
    site = user_site(client, user_id, name, site_by_user, by_name)

    row = {
        "user_id": user_id,
        "name": name,
        "email": email,
        "title": title,
        "site": site,
        "work_extension_before": "",
        "work_extension_after": "",
        "primary_before": "",
        "primary_after": "",
        "actions": "",
        "status": "ok",
        "notes": "",
    }

    detail = user_summary
    ext = work_extension(detail)
    row["work_extension_before"] = ext
    row["primary_before"] = primary_phone_summary(detail)

    needs_ext = not ext
    needs_primary = not primary_is_work_extension(detail, ext) if ext else True

    if not needs_ext and not needs_primary:
        return None

    actions: list[str] = []
    target_ext = ext
    if needs_ext:
        pool = pool_for_site(site)
        target_ext = next_extension(used_extensions, pool) or ""
        if not target_ext:
            # Fall back to alternate pool when primary site pool is full.
            alt_pool = POOL_HIGH if pool == POOL_LOW_DEFAULT else POOL_LOW_DEFAULT
            target_ext = next_extension(used_extensions, alt_pool) or ""
        if not target_ext:
            row["status"] = "error"
            row["notes"] = f"No free extensions in pools {POOL_LOW_DEFAULT} / {POOL_HIGH}"
            row["actions"] = "assign_extension"
            return row
        used_extensions.add(int(target_ext))
        actions.append(f"assign x{target_ext}")

    if needs_primary and target_ext:
        actions.append("set_primary_work")

    row["work_extension_after"] = target_ext or ext
    row["primary_after"] = f"WORK {target_ext or ext}"
    row["actions"] = "; ".join(actions)
    row["_patch_extension"] = target_ext or ext
    return row


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assign WORK extensions and align Primary Voice for Medical Assistants."
    )
    add_apply_arg(parser)
    add_env_arg(parser)
    parser.add_argument(
        "--title-needle",
        default=DEFAULT_TITLE_NEEDLE,
        help=f'Title substring (default: "{DEFAULT_TITLE_NEEDLE}").',
    )
    parser.add_argument("--user", help="Limit to one user name substring (case-insensitive).")
    parser.add_argument("--output", help="CSV report path")
    args = parser.parse_args()

    print_mode_banner(
        "Medical Assistant telephony align (WORK ext + Primary Voice)",
        apply=args.apply,
        title_filter=args.title_needle,
    )

    client = create_client(env_file=args.env_file)
    tool_log("info", "Loading phones...")
    phones = load_phones(client)
    site_by_user, by_name = index_phones(phones)
    tool_log("info", f"Loaded {len(phones)} phones")

    tool_log("info", "Collecting used extensions...")
    used_extensions = collect_used_extensions(client)
    tool_log("info", f"{len(used_extensions)} extensions in use")

    tool_log("info", "Loading Medical Assistant profiles...")
    ma_details = load_ma_details(
        client,
        title_needle=args.title_needle,
        user_filter=args.user,
    )
    tool_log("info", f"Loaded {len(ma_details)} active MA profiles")

    pending: list[dict[str, str]] = []
    for index, detail in enumerate(ma_details, start=1):
        if index == 1 or index % 25 == 0 or index == len(ma_details):
            tool_log("info", f"Evaluating {index}/{len(ma_details)}...")
        row = evaluate_user(
            detail,
            client=client,
            title_needle=args.title_needle,
            site_by_user=site_by_user,
            by_name=by_name,
            used_extensions=used_extensions,
        )
        if row:
            pending.append(row)

    tool_log("info", f"Users needing changes: {len(pending)}")

    counts = {"updated": 0, "dry_run": 0, "errors": 0, "skipped": 0}
    for index, row in enumerate(pending, start=1):
        if row.get("status") == "error":
            counts["errors"] += 1
            tool_log("error", f"{row['name']}: {row['notes']}")
            continue

        ext = safe_str(row.pop("_patch_extension", ""))
        if not ext:
            counts["skipped"] += 1
            continue

        detail = client.request("GET", f"/api/v2/users/{row['user_id']}")
        body = build_patch(detail, ext)

        if index == 1 or index % 25 == 0 or index == len(pending):
            tool_log("info", f"Applying {index}/{len(pending)}: {row['name']} ({row['actions']})")

        if args.apply:
            try:
                client.request("PATCH", f"/api/v2/users/{row['user_id']}", json_body=body)
                counts["updated"] += 1
                tool_log("ok", f"{row['name']} -> x{ext}, primary WORK")
            except GenesysError as exc:
                row["status"] = "error"
                row["notes"] = str(exc)[:300]
                counts["errors"] += 1
                tool_log("error", f"{row['name']}: {exc}")
        else:
            counts["dry_run"] += 1
            tool_log("dry", f"Would update {row['name']} -> x{ext}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.output) if args.output else output_path(f"ma_telephony_align_{stamp}.csv")
    write_csv(out_path, pending, OUTPUT_FIELDS)

    tool_log(
        "ok",
        f"Done — updated {counts['updated']}, dry-run {counts['dry_run']}, errors {counts['errors']}; report {out_path}",
    )
    return 1 if counts["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
