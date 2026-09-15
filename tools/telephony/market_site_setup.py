#!/usr/bin/env python3
"""
Generalized market site setup: location + 6-char site + verified outbound route.

Creates (or skips if present):
  - Location with ELIN emergency number
  - Telephony site linked to location
  - Default Outbound Route with trunk assigned and enabled=true

Post-apply verification FAILS the run if the route is not operational (CLEMKT lesson).

Optional HQ phone moves via --queue-name (joined=true members) or --phone-moves-csv.

Examples:
  # Dry run (default)
  python tools/telephony/market_site_setup.py \\
    --site-code CLEMKT --location-name Cleveland --caller-id +12167699935 \\
    --copy-from-location CLEJEF --queue-name "Sales - CLE - Cleveland"

  # Apply
  python tools/telephony/market_site_setup.py \\
    --site-code CLEMKT --location-name Cleveland --caller-id +12167699935 \\
    --copy-from-location CLEJEF --apply

  # Export live site config as template reference
  python tools/telephony/market_site_setup.py --dump-template CLEMKT
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import (
    add_apply_arg,
    add_env_arg,
    create_client,
    output_path,
    read_csv,
    setup_path,
    tool_log,
    write_csv,
)
from core.genesys_client import safe_str
from core.outbound_route_health import (
    DEFAULT_ROUTE_NAME,
    DEFAULT_TRUNK_NAME,
    build_default_route_payload,
    verify_default_route,
)

DEFAULT_CALLER_NAME = "Archwell Health"
DEFAULT_HQ_SITE = "Headquarters"
DEFAULT_MEDIA_MODEL = "Cloud"


def normalize_e164(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        raise ValueError("caller-id is required (E.164, e.g. +12165551234)")
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if value.strip().startswith("+") and len(digits) >= 10:
        return f"+{digits}"
    raise ValueError(f"Invalid caller-id format: {value!r}")


def format_elin_display(e164: str) -> str:
    digits = re.sub(r"\D", "", e164)
    if len(digits) == 11 and digits.startswith("1"):
        return f"+1 {digits[1:4]}-{digits[4:7]}-{digits[7:]}"
    return e164


def find_by_name(items: list[dict], name: str, *, key: str = "name") -> dict | None:
    target = name.strip().lower()
    return next((item for item in items if safe_str(item.get(key)).lower() == target), None)


def build_phone_update_payload(phone: dict, target_site: dict) -> dict:
    payload = json.loads(json.dumps(phone))
    payload["site"] = {"id": target_site["id"], "name": target_site["name"]}
    return payload


def build_hq_phone_user_map(client, hq_phones: list[dict]) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    by_name = {(p.get("name") or "").strip().lower(): p for p in hq_phones}
    web_phones = [p for p in hq_phones if (p.get("name") or "").strip().lower().endswith(" web")]
    tool_log("info", f"  Resolving webRtcUser on {len(web_phones)} HQ Web phones...")
    for i, phone in enumerate(web_phones, 1):
        if i == 1 or i % 10 == 0 or i == len(web_phones):
            tool_log("info", f"  HQ Web phone lookup {i}/{len(web_phones)}")
        detail = client.request("GET", f"/api/v2/telephony/providers/edges/phones/{phone['id']}")
        uid = (detail.get("webRtcUser") or {}).get("id")
        if uid:
            mapping[uid] = phone
    mapping["_by_name"] = by_name  # type: ignore[assignment]
    return mapping


def find_web_phone(user_id: str, user_name: str, phone_map: dict[str, dict]) -> dict | None:
    if user_id in phone_map and user_id != "_by_name":
        return phone_map[user_id]
    by_name: dict[str, dict] = phone_map.get("_by_name") or {}
    expected = f"{user_name} Web".lower()
    return by_name.get(expected)


def resolve_site_defaults(client, copy_from_site: str | None) -> dict:
    defaults = {
        "timeZone": "America/New_York",
        "mediaModel": DEFAULT_MEDIA_MODEL,
        "callerName": DEFAULT_CALLER_NAME,
        "description_prefix": "Auto Added",
    }
    if not copy_from_site:
        return defaults

    sites = client.paged_get("/api/v2/telephony/providers/edges/sites")
    peer = find_by_name(sites, copy_from_site)
    if not peer:
        raise RuntimeError(f"copy-from-site not found: {copy_from_site}")

    detail = client.request("GET", f"/api/v2/telephony/providers/edges/sites/{peer['id']}")
    defaults["timeZone"] = detail.get("timeZone") or defaults["timeZone"]
    defaults["mediaModel"] = detail.get("mediaModel") or defaults["mediaModel"]
    defaults["callerName"] = detail.get("callerName") or defaults["callerName"]
    return defaults


def build_location_payload(
    *,
    location_name: str,
    caller_id: str,
    template_detail: dict | None,
    address: dict | None,
) -> dict:
    if template_detail:
        payload = {
            "name": location_name,
            "address": json.loads(json.dumps(template_detail.get("address") or {})),
        }
        template_emergency = template_detail.get("emergencyNumber") or {}
        emergency_type = template_emergency.get("type") or "elin"
    else:
        if not address:
            raise RuntimeError("Provide --copy-from-location or address fields (--address-line1, --city, --state, --zip)")
        payload = {"name": location_name, "address": address}
        emergency_type = "elin"

    payload["emergencyNumber"] = {
        "e164": caller_id,
        "number": format_elin_display(caller_id),
        "type": emergency_type,
    }
    return payload


def ensure_location(
    client,
    *,
    location_name: str,
    caller_id: str,
    copy_from_location: str | None,
    address: dict | None,
    apply: bool,
) -> tuple[str, str, list[str]]:
    lines: list[str] = []
    locs = client.paged_get("/api/v2/locations")
    existing = find_by_name(locs, location_name)
    if existing:
        lines.append(f"Location {location_name} already exists: {existing['id']}")
        return existing["id"], "exists", lines

    template_detail = None
    if copy_from_location:
        template = find_by_name(locs, copy_from_location)
        if not template:
            raise RuntimeError(f"Template location not found: {copy_from_location}")
        template_detail = client.request("GET", f"/api/v2/locations/{template['id']}")
        lines.append(f"Using address template from location {copy_from_location}")

    payload = build_location_payload(
        location_name=location_name,
        caller_id=caller_id,
        template_detail=template_detail,
        address=address,
    )
    lines.append(f"Create location payload: {json.dumps(payload, indent=2)}")

    if not apply:
        return "", "would_create", lines

    created = client.request("POST", "/api/v2/locations", json_body=payload, expected_statuses=[200, 201])
    loc_id = created.get("id") or ""
    lines.append(f"Created location {location_name}: {loc_id}")
    return loc_id, "created", lines


def ensure_site(
    client,
    *,
    site_code: str,
    location_name: str,
    location_id: str,
    caller_id: str,
    site_defaults: dict,
    site_description: str | None,
    apply: bool,
) -> tuple[str, str, list[str]]:
    lines: list[str] = []
    sites = client.paged_get("/api/v2/telephony/providers/edges/sites")
    existing = find_by_name(sites, site_code)
    if existing:
        lines.append(f"Site {site_code} already exists: {existing['id']}")
        return existing["id"], "exists", lines

    if not location_id:
        locs = client.paged_get("/api/v2/locations")
        loc = find_by_name(locs, location_name)
        if not loc:
            raise RuntimeError(f"Location {location_name} not found")
        location_id = loc["id"]

    description = site_description or f"{site_defaults['description_prefix']} - {location_name} Market"
    payload = {
        "name": site_code,
        "description": description,
        "location": {"id": location_id, "name": location_name},
        "timeZone": site_defaults["timeZone"],
        "mediaModel": site_defaults["mediaModel"],
        "callerName": site_defaults["callerName"],
        "callerAddress": caller_id,
    }
    lines.append(f"Create site payload: {json.dumps(payload, indent=2)}")

    if not apply:
        return "", "would_create", lines

    created = client.request(
        "POST",
        "/api/v2/telephony/providers/edges/sites",
        json_body=payload,
        expected_statuses=[200, 201],
    )
    site_id = created.get("id") or ""
    lines.append(f"Created site {site_code}: {site_id}")
    return site_id, "created", lines


def ensure_outbound_route(
    client,
    site_id: str,
    *,
    trunk_name: str,
    route_name: str,
    apply: bool,
) -> tuple[str, list[str]]:
    lines: list[str] = []
    routes = client.paged_get(f"/api/v2/telephony/providers/edges/sites/{site_id}/outboundroutes")
    existing = next((r for r in routes if (r.get("name") or "") == route_name), None)

    payload = build_default_route_payload(trunk_name=trunk_name, route_name=route_name)

    if existing:
        route_id = existing.get("id") or ""
        ok, note, _ = verify_default_route(client, site_id, route_name=route_name)
        if ok:
            lines.append(f"{route_name} already exists and is operational")
            return "exists_ok", lines

        lines.append(f"{route_name} exists but NOT operational: {note}")
        if not apply:
            lines.append(f"Would update route to: {json.dumps(payload)}")
            return "would_fix", lines

        if route_id:
            detail = client.request(
                "GET",
                f"/api/v2/telephony/providers/edges/sites/{site_id}/outboundroutes/{route_id}",
            )
            update_payload = json.loads(json.dumps(detail))
            for key in ("id", "selfUri", "dateCreated", "dateModified", "createdBy", "modifiedBy"):
                update_payload.pop(key, None)
            update_payload["enabled"] = True
            update_payload["externalTrunkBases"] = payload["externalTrunkBases"]
            update_payload.setdefault("classificationTypes", payload["classificationTypes"])
            update_payload.setdefault("distribution", payload["distribution"])
            client.request(
                "PUT",
                f"/api/v2/telephony/providers/edges/sites/{site_id}/outboundroutes/{route_id}",
                json_body=update_payload,
                expected_statuses=[200, 202],
            )
            lines.append(f"Updated {route_name} (enabled + trunk)")
            return "fixed", lines

        lines.append("Route summary found but no route id — cannot fix")
        return "exists_broken", lines

    lines.append(f"Create outbound route: {json.dumps(payload)}")
    if not apply:
        return "would_create", lines

    try:
        client.request(
            "POST",
            f"/api/v2/telephony/providers/edges/sites/{site_id}/outboundroutes",
            json_body=payload,
            expected_statuses=[200, 201],
        )
        lines.append(f"Created {route_name}")
        return "created", lines
    except Exception as exc:
        if "already assigned" in str(exc).lower() or "duplicate" in str(exc).lower():
            lines.append(f"{route_name} already exists (auto-provisioned)")
            return "exists", lines
        raise


def move_phones_from_queue(
    client,
    *,
    site_code: str,
    site_id: str,
    queue_name: str,
    hq_site_name: str,
    apply: bool,
) -> list[dict]:
    sites = client.paged_get("/api/v2/telephony/providers/edges/sites")
    hq = find_by_name(sites, hq_site_name)
    target = next((s for s in sites if s.get("id") == site_id), None) or find_by_name(sites, site_code)
    if not hq:
        raise RuntimeError(f'HQ site "{hq_site_name}" not found')
    if not target and apply:
        raise RuntimeError(f"Target site {site_code} not found")

    queues = client.paged_get("/api/v2/routing/queues")
    queue = find_by_name(queues, queue_name)
    if not queue:
        raise RuntimeError(f"Queue not found: {queue_name}")

    members = client.paged_get(f"/api/v2/routing/queues/{queue['id']}/members")
    joined = [m for m in members if m.get("joined") is True]
    tool_log("info", f"{len(joined)} joined=true members in {queue_name}")

    all_phones = client.paged_get("/api/v2/telephony/providers/edges/phones", params={"pageSize": 100})
    hq_phones = [p for p in all_phones if (p.get("site") or {}).get("id") == hq["id"]]
    phone_map = build_hq_phone_user_map(client, hq_phones)

    rows: list[dict] = []
    for member in joined:
        user = member.get("user") or {}
        uid = user.get("id") or ""
        uname = user.get("name") or uid
        email = ""
        try:
            udetail = client.request("GET", f"/api/v2/users/{uid}")
            email = udetail.get("email") or ""
            uname = udetail.get("name") or uname
        except Exception as exc:
            tool_log("warn", f"User lookup failed for {uid}: {exc}")

        phone = find_web_phone(uid, uname, phone_map)
        row = {
            "source": "queue",
            "user_name": uname,
            "user_email": email,
            "user_id": uid,
            "phone_name": "",
            "phone_id": "",
            "old_site": hq_site_name,
            "new_site": site_code,
            "action": "",
            "notes": "",
        }

        if not phone:
            row["action"] = "SKIP_NO_HQ_WEB_PHONE"
            row["notes"] = f"No Web phone at {hq_site_name}"
            rows.append(row)
            continue

        row["phone_name"] = phone.get("name") or ""
        row["phone_id"] = phone.get("id") or ""

        if apply:
            if not target:
                row["action"] = "FAILED"
                row["notes"] = f"Target site {site_code} not found"
                rows.append(row)
                continue
            detail = client.request("GET", f"/api/v2/telephony/providers/edges/phones/{phone['id']}")
            current_site = (detail.get("site") or {}).get("name") or ""
            if current_site.upper() == site_code.upper():
                row["action"] = "SKIP_ALREADY_ON_TARGET"
                rows.append(row)
                continue
            payload = build_phone_update_payload(detail, target)
            try:
                client.request(
                    "PUT",
                    f"/api/v2/telephony/providers/edges/phones/{phone['id']}",
                    json_body=payload,
                    expected_statuses=[200, 202],
                )
                row["action"] = "UPDATED"
            except Exception as exc:
                row["action"] = "FAILED"
                row["notes"] = str(exc)
        else:
            row["action"] = "WOULD_MOVE"

        rows.append(row)

    return rows


def move_phones_from_csv(
    client,
    *,
    csv_path: Path,
    site_code: str,
    site_id: str,
    hq_site_name: str,
    apply: bool,
) -> list[dict]:
    rows_in = read_csv(csv_path)
    if not rows_in:
        raise RuntimeError(f"Phone moves CSV is empty: {csv_path}")

    sites = client.paged_get("/api/v2/telephony/providers/edges/sites")
    hq = find_by_name(sites, hq_site_name)
    target = next((s for s in sites if s.get("id") == site_id), None) or find_by_name(sites, site_code)
    site_by_code = {(s.get("name") or "").upper(): s for s in sites}

    rows: list[dict] = []
    for row_in in rows_in:
        phone_id = safe_str(row_in.get("phone_id"))
        user_email = safe_str(row_in.get("user_email"))
        target_code = safe_str(row_in.get("target_site_code") or row_in.get("site_code") or site_code).upper()
        target_site = site_by_code.get(target_code) or target

        result = {
            "source": "csv",
            "user_name": safe_str(row_in.get("user_name")),
            "user_email": user_email,
            "user_id": safe_str(row_in.get("user_id")),
            "phone_name": safe_str(row_in.get("phone_name")),
            "phone_id": phone_id,
            "old_site": safe_str(row_in.get("old_site") or hq_site_name),
            "new_site": target_code or site_code,
            "action": "",
            "notes": safe_str(row_in.get("notes")),
        }

        if not phone_id and user_email:
            users = client.paged_get("/api/v2/users", params={"searchTerm": user_email})
            user = next((u for u in users if safe_str(u.get("email")).lower() == user_email.lower()), None)
            if not user:
                result["action"] = "SKIP_USER_NOT_FOUND"
                rows.append(result)
                continue
            result["user_id"] = user.get("id") or ""
            result["user_name"] = user.get("name") or result["user_name"]
            all_phones = client.paged_get("/api/v2/telephony/providers/edges/phones", params={"pageSize": 100})
            for phone in all_phones:
                if phone_id:
                    break
                detail = client.request("GET", f"/api/v2/telephony/providers/edges/phones/{phone['id']}")
                web_user = (detail.get("webRtcUser") or {}).get("id")
                if web_user == result["user_id"]:
                    phone_id = phone["id"]
                    result["phone_id"] = phone_id
                    result["phone_name"] = phone.get("name") or result["phone_name"]
                    break

        if not phone_id:
            result["action"] = "SKIP_NO_PHONE"
            result["notes"] = result["notes"] or "phone_id or resolvable user_email required"
            rows.append(result)
            continue

        if not target_site:
            result["action"] = "FAILED"
            result["notes"] = result["notes"] or f"Target site not found: {target_code}"
            rows.append(result)
            continue

        detail = client.request("GET", f"/api/v2/telephony/providers/edges/phones/{phone_id}")
        current_site_name = (detail.get("site") or {}).get("name") or ""
        result["phone_name"] = detail.get("name") or result["phone_name"]
        result["old_site"] = current_site_name or result["old_site"]

        if current_site_name.upper() == (target_site.get("name") or "").upper():
            result["action"] = "SKIP_ALREADY_ON_TARGET"
            rows.append(result)
            continue

        if not apply:
            result["action"] = "WOULD_MOVE"
            rows.append(result)
            continue

        payload = build_phone_update_payload(detail, target_site)
        try:
            client.request(
                "PUT",
                f"/api/v2/telephony/providers/edges/phones/{phone_id}",
                json_body=payload,
                expected_statuses=[200, 202],
            )
            result["action"] = "UPDATED"
        except Exception as exc:
            result["action"] = "FAILED"
            result["notes"] = str(exc)
        rows.append(result)

    if not hq:
        tool_log("warn", f'HQ site "{hq_site_name}" not found — CSV moves still processed by phone_id')

    return rows


def dump_template(client, site_code: str) -> int:
    sites = client.paged_get("/api/v2/telephony/providers/edges/sites")
    site = find_by_name(sites, site_code)
    if not site:
        tool_log("error", f"Site not found: {site_code}")
        return 1

    site_id = site["id"]
    detail = client.request("GET", f"/api/v2/telephony/providers/edges/sites/{site_id}")
    location = detail.get("location") or {}
    loc_detail = {}
    if location.get("id"):
        loc_detail = client.request("GET", f"/api/v2/locations/{location['id']}")

    routes = client.paged_get(f"/api/v2/telephony/providers/edges/sites/{site_id}/outboundroutes")
    route_details = []
    for route in routes:
        rid = route.get("id")
        if rid:
            route_details.append(
                client.request("GET", f"/api/v2/telephony/providers/edges/sites/{site_id}/outboundroutes/{rid}")
            )
        else:
            route_details.append(route)

    ok, note, route_health = verify_default_route(client, site_id)

    template = {
        "site_code": site_code,
        "site_summary": site,
        "site_detail": detail,
        "location_detail": loc_detail,
        "outbound_routes": route_details,
        "default_route_health": route_health,
        "default_route_operational": ok,
        "default_route_note": note,
    }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = output_path(f"market_site_template_{site_code.lower()}_{ts}.json")
    out_json.write_text(json.dumps(template, indent=2, default=str), encoding="utf-8")
    tool_log("ok", f"Template written: {out_json}")
    print(json.dumps(template, indent=2, default=str))
    return 0


def build_address_from_args(args) -> dict | None:
    if not any([args.address_line1, args.city, args.state, args.zip_code]):
        return None
    if not all([args.address_line1, args.city, args.state, args.zip_code]):
        raise RuntimeError("When not using --copy-from-location, provide --address-line1, --city, --state, and --zip")
    country = args.country or "US"
    return {
        "city": args.city,
        "country": country,
        "street1": args.address_line1,
        "street2": args.address_line2 or "",
        "zip": args.zip_code,
        "state": args.state,
    }


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Market site setup: location + site + verified outbound route.")
    add_apply_arg(parser)
    add_env_arg(parser)
    parser.add_argument("--site-code", help="6-character market site code (e.g. CLEMKT)")
    parser.add_argument("--location-name", help="New or existing location name (e.g. Cleveland)")
    parser.add_argument("--caller-id", help="ELIN / site caller ID in E.164 (e.g. +12167699935)")
    parser.add_argument(
        "--copy-from-location",
        help="Copy address fields from an existing location (e.g. CLEJEF)",
    )
    parser.add_argument(
        "--copy-from-site",
        help="Copy timezone/mediaModel/callerName defaults from an existing site",
    )
    parser.add_argument("--site-description", help="Optional site description override")
    parser.add_argument("--timezone", help="Override timezone (default from copy-from-site or America/New_York)")
    parser.add_argument("--trunk-name", default=DEFAULT_TRUNK_NAME, help=f"Outbound trunk name (default: {DEFAULT_TRUNK_NAME})")
    parser.add_argument("--route-name", default=DEFAULT_ROUTE_NAME, help=f"Outbound route name (default: {DEFAULT_ROUTE_NAME})")
    parser.add_argument("--queue-name", help="Move joined=true queue members' HQ Web phones to this site")
    parser.add_argument("--phone-moves-csv", help="CSV of phone moves (phone_id or user_email; optional target_site_code)")
    parser.add_argument("--hq-site", default=DEFAULT_HQ_SITE, help=f"HQ site name for queue-based moves (default: {DEFAULT_HQ_SITE})")
    parser.add_argument("--address-line1", help="Street address when not copying from a location")
    parser.add_argument("--address-line2", default="", help="Address line 2")
    parser.add_argument("--city", help="City")
    parser.add_argument("--state", help="State code (e.g. OH)")
    parser.add_argument("--zip", dest="zip_code", help="ZIP code")
    parser.add_argument("--country", default="US", help="Country code (default US)")
    parser.add_argument(
        "--dump-template",
        metavar="SITE_CODE",
        help="Read-only: export live site/location/route config as JSON template",
    )
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)

    if args.dump_template:
        return dump_template(client, args.dump_template.strip().upper())

    missing = [name for name, val in [
        ("--site-code", args.site_code),
        ("--location-name", args.location_name),
        ("--caller-id", args.caller_id),
    ] if not val]
    if missing:
        parser.error(f"Required when not using --dump-template: {', '.join(missing)}")

    site_code = args.site_code.strip().upper()
    if len(site_code) != 6:
        parser.error("--site-code must be exactly 6 characters")

    location_name = args.location_name.strip()
    caller_id = normalize_e164(args.caller_id)

    if not args.copy_from_location:
        try:
            build_address_from_args(args)
        except RuntimeError as exc:
            parser.error(str(exc))
    elif any([args.address_line1, args.city, args.state, args.zip_code]):
        tool_log("warn", "Address fields ignored when --copy-from-location is set")

    address = build_address_from_args(args) if not args.copy_from_location else None
    site_defaults = resolve_site_defaults(client, args.copy_from_site)
    if args.timezone:
        site_defaults["timeZone"] = args.timezone

    print()
    print("=" * 72)
    print(f"MARKET SITE SETUP — {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"  Site: {site_code}  Location: {location_name}  Caller ID: {caller_id}")
    print("=" * 72)

    loc_id, loc_status, loc_logs = ensure_location(
        client,
        location_name=location_name,
        caller_id=caller_id,
        copy_from_location=args.copy_from_location,
        address=address,
        apply=args.apply,
    )
    for line in loc_logs:
        print(line)

    site_id, site_status, site_logs = ensure_site(
        client,
        site_code=site_code,
        location_name=location_name,
        location_id=loc_id,
        caller_id=caller_id,
        site_defaults=site_defaults,
        site_description=args.site_description,
        apply=args.apply,
    )
    for line in site_logs:
        print(line)

    effective_site_id = site_id
    if not effective_site_id:
        sites = client.paged_get("/api/v2/telephony/providers/edges/sites")
        existing = find_by_name(sites, site_code)
        effective_site_id = existing["id"] if existing else ""

    route_status = "skipped"
    route_logs: list[str] = []
    if effective_site_id:
        route_status, route_logs = ensure_outbound_route(
            client,
            effective_site_id,
            trunk_name=args.trunk_name,
            route_name=args.route_name,
            apply=args.apply,
        )
        for line in route_logs:
            print(line)
    elif args.apply:
        tool_log("error", "Cannot create outbound route without site id")
        return 1
    else:
        tool_log("info", "Outbound route step skipped in dry run (site would be created)")

    phone_rows: list[dict] = []
    if args.queue_name:
        phone_rows.extend(
            move_phones_from_queue(
                client,
                site_code=site_code,
                site_id=effective_site_id,
                queue_name=args.queue_name,
                hq_site_name=args.hq_site,
                apply=args.apply,
            )
        )
    if args.phone_moves_csv:
        phone_rows.extend(
            move_phones_from_csv(
                client,
                csv_path=Path(args.phone_moves_csv),
                site_code=site_code,
                site_id=effective_site_id,
                hq_site_name=args.hq_site,
                apply=args.apply,
            )
        )

    verification: dict = {}
    route_verify_ok = True
    route_verify_note = "not_run"
    if args.apply and effective_site_id:
        route_verify_ok, route_verify_note, verification = verify_default_route(
            client, effective_site_id, route_name=args.route_name
        )
        if not route_verify_ok:
            tool_log("error", f"POST-CREATE VERIFICATION FAILED: {route_verify_note}")
            tool_log("error", json.dumps(verification, indent=2))
        else:
            tool_log("ok", "Post-create outbound route verification passed")

        if loc_id:
            loc_detail = client.request("GET", f"/api/v2/locations/{loc_id}")
        else:
            locs = client.paged_get("/api/v2/locations")
            loc = find_by_name(locs, location_name)
            loc_detail = client.request("GET", f"/api/v2/locations/{loc['id']}") if loc else {}

        site_detail = client.request("GET", f"/api/v2/telephony/providers/edges/sites/{effective_site_id}")
        verification["location_emergency"] = loc_detail.get("emergencyNumber")
        verification["site_caller_id"] = site_detail.get("callerAddress")
        verification["site_location"] = (site_detail.get("location") or {}).get("name")

        if verification.get("site_caller_id") and verification["site_caller_id"] != caller_id:
            tool_log("warn", f"Site caller ID {verification['site_caller_id']} != requested {caller_id}")
        elin = (loc_detail.get("emergencyNumber") or {}).get("e164")
        if elin and elin != caller_id:
            tool_log("warn", f"Location ELIN {elin} != requested {caller_id}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_rows: list[dict] = []

    setup_row = {
        "record_type": "setup",
        "site_code": site_code,
        "location_name": location_name,
        "location_id": loc_id,
        "site_id": effective_site_id,
        "caller_id": caller_id,
        "location_status": loc_status,
        "site_status": site_status,
        "route_status": route_status,
        "route_verify_ok": "yes" if route_verify_ok else ("no" if args.apply else "dry_run"),
        "route_verify_note": route_verify_note,
        "verification_json": json.dumps(verification) if verification else "",
        "user_name": "",
        "user_email": "",
        "phone_name": "",
        "phone_id": "",
        "old_site": "",
        "new_site": "",
        "action": "SETUP_SUMMARY",
        "notes": "",
    }
    report_rows.append(setup_row)

    for row in phone_rows:
        report_rows.append({
            "record_type": "phone_move",
            "site_code": site_code,
            "location_name": location_name,
            "location_id": loc_id,
            "site_id": effective_site_id,
            "caller_id": caller_id,
            "location_status": loc_status,
            "site_status": site_status,
            "route_status": route_status,
            "route_verify_ok": setup_row["route_verify_ok"],
            "route_verify_note": route_verify_note,
            "verification_json": "",
            **row,
        })

    out_csv = output_path(f"market_site_setup_{site_code.lower()}_{ts}.csv")
    fields = [
        "record_type",
        "site_code",
        "location_name",
        "location_id",
        "site_id",
        "caller_id",
        "location_status",
        "site_status",
        "route_status",
        "route_verify_ok",
        "route_verify_note",
        "source",
        "user_name",
        "user_email",
        "user_id",
        "phone_name",
        "phone_id",
        "old_site",
        "new_site",
        "action",
        "notes",
        "verification_json",
    ]
    write_csv(out_csv, report_rows, fields)

    updated = [r for r in phone_rows if r.get("action") == "UPDATED"]
    would = [r for r in phone_rows if r.get("action") == "WOULD_MOVE"]
    failed = [r for r in phone_rows if r.get("action") == "FAILED"]

    print()
    print("=" * 72)
    print("SUMMARY")
    print(f"  Location {location_name}: {loc_status} ({loc_id or 'n/a'})")
    print(f"  Site {site_code}: {site_status} ({effective_site_id or 'n/a'})")
    print(f"  Outbound route: {route_status}")
    if args.apply:
        print(f"  Route verification: {'PASS' if route_verify_ok else 'FAIL — ' + route_verify_note}")
    print(f"  Phones moved: {len(updated)}")
    if would:
        print(f"  Would move: {len(would)}")
    if failed:
        print(f"  Phone failures: {len(failed)}")
    print(f"  Report: {out_csv}")
    print("=" * 72)

    if args.apply and effective_site_id and not route_verify_ok:
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
