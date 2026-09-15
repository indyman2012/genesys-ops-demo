#!/usr/bin/env python3
"""
One-off: set Primary Voice to Other Phone for users whose job title contains
"Medical Assistant" and whose Primary Voice is currently Work Phone.

This org stores phones on GET /api/v2/users/{userId} (addresses + primaryContactInfo),
not on the profile contactInfo fields. Updates use PATCH /api/v2/users/{userId}.

Target end state (example: tabitha.taylor@archwellhealth.com):
  - addresses keeps WORK extension + OTHER Teams number
  - primaryContactInfo PHONE PRIMARY matches the OTHER phone number

Dry run by default. Pass --apply to make changes.
"""

from __future__ import annotations

import argparse
import copy
import sys
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
from core.genesys_client import safe_str

OTHER_PHONE_PATH = "contactInfo.phone_other"

REPORT_FIELDS = [
    "user_id",
    "name",
    "email",
    "title",
    "current_primary_phone",
    "work_phone",
    "other_phone",
    "action",
    "notes",
]


def _phone_addresses(addresses: list) -> list[dict]:
    return [
        item for item in (addresses or [])
        if isinstance(item, dict) and safe_str(item.get("mediaType")).upper() == "PHONE"
    ]


def _primary_phone_contact(primary_contact_info: list) -> dict | None:
    for item in primary_contact_info or []:
        if not isinstance(item, dict):
            continue
        if safe_str(item.get("mediaType")).upper() == "PHONE" and safe_str(item.get("type")).upper() == "PRIMARY":
            return item
    return None


def _address_by_type(addresses: list, address_type: str) -> dict | None:
    target = address_type.upper()
    for item in _phone_addresses(addresses):
        if safe_str(item.get("type")).upper() == target:
            return item
    return None


def _contact_key(entry: dict) -> tuple[str, str]:
    extension = safe_str(entry.get("extension")).strip()
    if extension:
        return ("extension", extension)
    address = safe_str(entry.get("address")).strip()
    if address:
        return ("address", address)
    display = safe_str(entry.get("display")).strip()
    if display:
        return ("display", display)
    return ("", "")


def _contacts_match(left: dict | None, right: dict | None) -> bool:
    if not left or not right:
        return False
    key = _contact_key(left)
    return key[0] != "" and key == _contact_key(right)


def _format_phone(entry: dict | None) -> str:
    if not entry:
        return ""
    extension = safe_str(entry.get("extension")).strip()
    if extension:
        return f"ext {extension}"
    return safe_str(entry.get("display") or entry.get("address"))


def _build_primary_contact_patch(user: dict, other_phone: dict) -> dict:
    primary_contact_info: list[dict] = []
    for item in user.get("primaryContactInfo") or []:
        if not isinstance(item, dict):
            continue
        if safe_str(item.get("mediaType")).upper() == "PHONE" and safe_str(item.get("type")).upper() == "PRIMARY":
            continue
        primary_contact_info.append(copy.deepcopy(item))

    phone_primary: dict[str, str] = {
        "address": safe_str(other_phone.get("address")),
        "display": safe_str(other_phone.get("display") or other_phone.get("address")),
        "mediaType": "PHONE",
        "type": "PRIMARY",
    }
    extension = safe_str(other_phone.get("extension")).strip()
    if extension:
        phone_primary["extension"] = extension
    country_code = safe_str(other_phone.get("countryCode")).strip()
    if country_code:
        phone_primary["countryCode"] = country_code

    primary_contact_info.append(phone_primary)
    return {
        "version": user.get("version"),
        "primaryContactInfo": primary_contact_info,
    }


def _profile_other_phone_update(profile: dict) -> dict | None:
    entries = (profile.get("contactInfo") or {}).get("phone_other") or []
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        return None
    field_id = safe_str(entries[0].get("_id"))
    if not field_id:
        return None
    return {
        "primaryContactInfo": {
            "voice": [{
                "value": {
                    "fieldId": field_id,
                    "fieldPath": OTHER_PHONE_PATH,
                },
            }],
        },
    }


def _title_matches(title: str, needle: str) -> bool:
    return needle.casefold() in safe_str(title).casefold()


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(
        description=(
            "Change Primary Voice from Work Phone to Other Phone for users whose "
            "job title contains a search phrase (default: Medical Assistant)."
        ),
    )
    parser.add_argument(
        "--title-contains",
        default="Medical Assistant",
        help='Case-insensitive title match (default: "Medical Assistant").',
    )
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include inactive users (default: active users only).",
    )
    add_apply_arg(parser)
    add_env_arg(parser)
    args = parser.parse_args()

    active_only = not args.include_inactive
    print_mode_banner(
        "Medical Assistant Primary Voice -> Other Phone",
        apply=args.apply,
        title_filter=args.title_contains,
    )

    client = create_client(log=tool_log, env_file=args.env_file)
    users = client.paged_get("/api/v2/users")
    matched = [
        user for user in users
        if _title_matches(user.get("title", ""), args.title_contains)
        and (not active_only or safe_str(user.get("state")).lower() == "active")
    ]
    tool_log("info", f"Found {len(matched)} users matching title filter")

    rows: list[dict[str, str]] = []
    counts = {"updated": 0, "skipped": 0, "errors": 0, "dry_run": 0}

    for index, user in enumerate(matched, start=1):
        user_id = safe_str(user.get("id"))
        name = safe_str(user.get("name"))
        email = safe_str(user.get("email"))
        title = safe_str(user.get("title"))

        if index == 1 or index % 25 == 0 or index == len(matched):
            tool_log("info", f"Processing {index}/{len(matched)}")

        row = {
            "user_id": user_id,
            "name": name,
            "email": email,
            "title": title,
            "current_primary_phone": "",
            "work_phone": "",
            "other_phone": "",
            "action": "",
            "notes": "",
        }

        try:
            detail = client.request("GET", f"/api/v2/users/{user_id}")
            profile = client.request("GET", f"/api/v2/users/{user_id}/profile")
        except Exception as exc:
            row["action"] = "ERROR"
            row["notes"] = f"user/profile GET failed: {exc}"
            counts["errors"] += 1
            rows.append(row)
            continue

        addresses = detail.get("addresses") or []
        primary = _primary_phone_contact(detail.get("primaryContactInfo") or [])
        work_phone = _address_by_type(addresses, "WORK")
        other_phone = _address_by_type(addresses, "OTHER")

        row["current_primary_phone"] = _format_phone(primary)
        row["work_phone"] = _format_phone(work_phone)
        row["other_phone"] = _format_phone(other_phone)

        if not other_phone:
            row["action"] = "SKIP_NO_OTHER_PHONE"
            row["notes"] = "No Other Phone address on user record"
            counts["skipped"] += 1
            rows.append(row)
            continue

        if _contacts_match(primary, other_phone):
            row["action"] = "SKIP_ALREADY_OTHER_PHONE"
            counts["skipped"] += 1
            rows.append(row)
            continue

        if not work_phone or not _contacts_match(primary, work_phone):
            row["action"] = "SKIP_NOT_WORK_PHONE"
            row["notes"] = "Primary phone is not the Work Phone entry"
            counts["skipped"] += 1
            rows.append(row)
            continue

        profile_body = _profile_other_phone_update(profile)
        patch_body = _build_primary_contact_patch(detail, other_phone)

        if args.apply:
            try:
                if profile_body:
                    client.request("PUT", f"/api/v2/users/{user_id}/profile", json_body=profile_body)
                else:
                    client.request("PATCH", f"/api/v2/users/{user_id}", json_body=patch_body)
                row["action"] = "UPDATED"
                counts["updated"] += 1
                tool_log("ok", f"Updated {name} ({email})")
            except Exception as exc:
                row["action"] = "ERROR"
                row["notes"] = f"update failed: {exc}"
                counts["errors"] += 1
        else:
            row["action"] = "DRY_RUN_WOULD_UPDATE"
            row["notes"] = (
                "Would set primary phone to Other Phone via profile PUT"
                if profile_body
                else "Would set primary phone to Other Phone via user PATCH"
            )
            counts["dry_run"] += 1

        rows.append(row)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_path(f"ma_primary_phone_other_{stamp}.csv")
    write_csv(report_path, rows, REPORT_FIELDS)

    tool_log("info", f"Report: {report_path}")
    tool_log("info", f"Matched: {len(matched)}")
    if args.apply:
        tool_log("ok", f"Updated: {counts['updated']}")
    else:
        tool_log("dry", f"Would update: {counts['dry_run']}")
    tool_log("info", f"Skipped: {counts['skipped']}")
    if counts["errors"]:
        tool_log("error", f"Errors: {counts['errors']}")

    return 1 if counts["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
