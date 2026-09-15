#!/usr/bin/env python3
"""
Reassign Headquarters WebRTC phones to center sites based on e911 routing.

Uses ONLY queue member rows where joined=true. For e911, ambiguous cases
must never auto-pick a site — a phone is moved only when the user has
exactly one joined=true queue and it maps to a single center site.

Phones at Headquarters with no attached user are left unchanged.

Default: DRY RUN (screen output + CSV under outputs/).
Use --apply to perform phone site updates (unambiguous cases only).
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import (
    add_apply_arg,
    add_env_arg,
    create_client,
    output_path,
    tool_log,
    write_csv,
)

DEFAULT_HQ_SITE = "Headquarters"

# Site codes that need explicit queue-name overrides (queue name substring match)
MANUAL_SITE_QUEUE_HINTS: dict[str, str] = {
    "CLEELK": "NCM_CLE_Eastlake",
    "CLEMPH": "NCM_CLE_Maple Heights",
    "CLEPMA": "NCM_CLE_Parma",
    "CLEWHI": "NCM_CLE_Woodland Hills",
    "CLEWLK": "NCM_CLE_Westlake",
    "CLEMKT": "Sales - CLE - Cleveland",
    "DENLWD": "NCM_DEN_Lakewood",
    "JAXBNT": "NCM_JAX_Brentwood",
    "JAXHBV": "NCM_JAX_Harborview",
    "JAXJXH": "NCM_JAX_Jax Heights",
    "LITFPK": "NCM_LIT_Fair Park",
    "LITPBF": "NCM_LIT_Pine Bluff",
    "PHLGMT": "NCM_PHL_Germantown",
    "PHXPAP": "NCM_PHX_Papago",
    "TMPPPK": "NCM_FGC_Page Park",
    "TMPPTC": "NCM_FGC_Port Charlotte",
    "TMPZHI": "Patient Services - TMP - Zephyrhills",
}


def queue_parts(name: str) -> tuple[str | None, str | None]:
    name = (name or "").strip()
    patterns = [
        r"^Patient Services\s*-\s*([A-Z0-9]{2,4})\s*-\s*(.+)$",
        r"^Patient Services\s*-\s*([A-Z0-9]{2,4})\s+(.+)$",
        r"^NCM_([A-Z0-9]{2,4})_(.+)$",
        r"^Sales\s*-\s*([A-Z0-9]{2,4})\s*-\s*(.+)$",
    ]
    for pat in patterns:
        m = re.match(pat, name, re.I)
        if m:
            return m.group(1).upper()[:3], m.group(2).strip()
    return None, None


def center_tokens(center: str) -> set[str]:
    words = re.findall(r"[A-Za-z0-9]+", center)
    tokens: set[str] = set()
    for w in words:
        tokens.add(w.upper())
        if len(w) >= 2:
            tokens.add(w[:2].upper())
        if len(w) >= 3:
            tokens.add(w[:3].upper())
            tokens.add(w[0].upper() + w[1].upper() + w[-1].upper())
    if len(words) >= 2:
        tokens.add("".join(w[0] for w in words).upper())
        tokens.add("".join(w[0] for w in words[:3]).upper())
    compact = re.sub(r"[^A-Za-z]", "", center).upper()
    if len(compact) >= 3:
        tokens.add(compact[:3])
    return tokens


def build_queue_site_maps(client, sites: list[dict], queues: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    """Return queue_id->site and site_code->queue info."""
    six_char_sites = {
        (s.get("name") or "").upper(): s
        for s in sites
        if len((s.get("name") or "").strip()) == 6
    }
    queue_by_name = {(q.get("name") or ""): q for q in queues}

    queue_rows = []
    for q in queues:
        market, center = queue_parts(q.get("name") or "")
        if market and center:
            queue_rows.append({
                "id": q["id"],
                "name": q["name"],
                "market": market,
                "center": center,
                "tokens": center_tokens(center),
            })

    site_to_queue: dict[str, dict] = {}
    for code, site in six_char_sites.items():
        hint = MANUAL_SITE_QUEUE_HINTS.get(code)
        if hint:
            for qname, q in queue_by_name.items():
                if hint.lower() in qname.lower() or qname.lower() == hint.lower():
                    site_to_queue[code] = {
                        "site_id": site["id"],
                        "site_name": site["name"],
                        "queue_id": q["id"],
                        "queue_name": q["name"],
                    }
                    break
            if code in site_to_queue:
                continue

        market = code[:3]
        center_code = code[3:]
        candidates = [q for q in queue_rows if q["market"] == market]
        best = None
        for q in candidates:
            if center_code in q["tokens"]:
                best = q
                break
        if not best:
            for q in candidates:
                letters = re.sub(r"[^A-Z]", "", q["center"].upper())
                if center_code in letters:
                    best = q
                    break
        if not best and len(candidates) == 1:
            best = candidates[0]
        if best:
            site_to_queue[code] = {
                "site_id": site["id"],
                "site_name": site["name"],
                "queue_id": best["id"],
                "queue_name": best["name"],
            }

    queue_to_site: dict[str, dict] = {}
    for code, info in site_to_queue.items():
        queue_to_site[info["queue_id"]] = {
            "site_id": info["site_id"],
            "site_name": info["site_name"],
            "site_code": code,
            "queue_name": info["queue_name"],
        }

    # Also map Patient Services queues to the same site when NCM sibling exists
    for q in queue_rows:
        if q["id"] in queue_to_site:
            continue
        market = q["market"]
        for code, info in site_to_queue.items():
            if not code.startswith(market):
                continue
            if q["name"].startswith("Patient Services") and q["center"].lower() in info["queue_name"].lower():
                queue_to_site[q["id"]] = {
                    "site_id": info["site_id"],
                    "site_name": info["site_name"],
                    "site_code": code,
                    "queue_name": q["name"],
                }
                break

    return queue_to_site, site_to_queue


def build_user_joined_queues(
    client,
    queues: list[dict],
    user_ids: set[str] | None = None,
) -> dict[str, list[dict]]:
    """Map user_id -> queues where member row has joined=true."""
    user_queues: dict[str, list[dict]] = defaultdict(list)
    total = len(queues)
    for i, q in enumerate(queues, 1):
        qid = q["id"]
        qname = q.get("name") or qid
        if i == 1 or i % 25 == 0 or i == total:
            tool_log("info", f"Scanning joined=true members {i}/{total} queues...")
        for member in client.paged_get(
            f"/api/v2/routing/queues/{qid}/members",
            params={"pageSize": 100},
        ):
            if member.get("joined") is not True:
                continue
            user = member.get("user") or {}
            uid = user.get("id")
            if not uid:
                continue
            if user_ids is not None and uid not in user_ids:
                continue
            user_queues[uid].append({
                "queue_id": qid,
                "queue_name": qname,
                "member_by": member.get("memberBy") or "",
            })
    return user_queues


def mappable_joined_queues(
    joined: list[dict],
    queue_to_site: dict[str, dict],
) -> list[dict]:
    """Return joined=true queue rows that map to a center site."""
    out: list[dict] = []
    for item in joined:
        qid = item["queue_id"]
        if qid not in queue_to_site:
            continue
        out.append({**item, **queue_to_site[qid]})
    return out


def append_review_rows(
    review_rows: list[dict],
    *,
    phone_name: str,
    phone_id: str,
    user: dict,
    uid: str,
    joined: list[dict],
    mappable: list[dict],
    queue_to_site: dict[str, dict],
) -> None:
    """Export every joined=true queue for manual e911 review."""
    distinct_sites = {m["site_id"] for m in mappable}
    for q in sorted(joined, key=lambda x: x.get("queue_name", "")):
        site = queue_to_site.get(q["queue_id"], {})
        review_rows.append({
            "phone_name": phone_name,
            "phone_id": phone_id,
            "user_name": user["name"],
            "user_email": user["email"],
            "user_id": uid,
            "joined_true_count": len(joined),
            "joined_mappable_count": len(mappable),
            "distinct_site_count": len(distinct_sites),
            "queue_name": q.get("queue_name") or "",
            "queue_id": q.get("queue_id") or "",
            "member_by": q.get("member_by") or "",
            "maps_to_site": "yes" if q["queue_id"] in queue_to_site else "no",
            "site_code": site.get("site_code") or "",
            "site_name": site.get("site_name") or "",
            "site_id": site.get("site_id") or "",
        })


def get_phone_user_id(client, phone: dict) -> tuple[str, str]:
    """Return (user_id, source) from phone detail / station."""
    detail = client.request("GET", f"/api/v2/telephony/providers/edges/phones/{phone['id']}")
    web_user = detail.get("webRtcUser") or {}
    uid = web_user.get("id")
    if uid:
        return uid, "webRtcUser"

    lines = detail.get("lines") or phone.get("lines") or []
    if lines:
        line_id = lines[0].get("id")
        if line_id:
            station = client.request("GET", f"/api/v2/stations/{line_id}")
            uid = station.get("userId")
            if uid:
                return uid, "station.userId"
    return "", ""


def build_phone_update_payload(phone: dict, target_site: dict) -> dict:
    import json as _json
    payload = _json.loads(_json.dumps(phone))
    payload["site"] = {"id": target_site["id"], "name": target_site["name"]}
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reassign HQ phones to center sites based on joined queue membership.",
    )
    add_apply_arg(parser)
    add_env_arg(parser)
    parser.add_argument("--hq-site", default=DEFAULT_HQ_SITE, help="Source site name (default Headquarters)")
    args = parser.parse_args()

    client = create_client(env_file=args.env_file)

    tool_log("info", "Loading sites and queues...")
    sites = client.paged_get("/api/v2/telephony/providers/edges/sites")
    queues = client.paged_get("/api/v2/routing/queues")

    hq_site = next((s for s in sites if (s.get("name") or "").lower() == args.hq_site.lower()), None)
    if not hq_site:
        tool_log("error", f'Headquarters site "{args.hq_site}" not found')
        return 1
    hq_site_id = hq_site["id"]

    queue_to_site, site_to_queue = build_queue_site_maps(client, sites, queues)
    tool_log("info", f"Mapped {len(queue_to_site)} queues to {len(site_to_queue)} center sites")

    tool_log("info", "Loading phones...")
    all_phones = client.paged_get("/api/v2/telephony/providers/edges/phones", params={"pageSize": 100})
    hq_phones = []
    hq_no_user = 0
    for phone in all_phones:
        site = phone.get("site") or {}
        if site.get("id") != hq_site_id:
            continue
        hq_phones.append(phone)

    tool_log("info", f"Found {len(hq_phones)} phones at {args.hq_site}")

    tool_log("info", "Resolving attached users on HQ phones...")
    phone_users: list[dict] = []
    for i, phone in enumerate(hq_phones, 1):
        if i == 1 or i % 25 == 0 or i == len(hq_phones):
            tool_log("info", f"  Phone user lookup {i}/{len(hq_phones)}")
        uid, source = get_phone_user_id(client, phone)
        if not uid:
            hq_no_user += 1
            continue
        phone_users.append({
            "phone": phone,
            "user_id": uid,
            "user_source": source,
        })

    tool_log("info", f"HQ phones with users: {len(phone_users)} (no user: {hq_no_user})")

    user_ids_needed = {p["user_id"] for p in phone_users}
    user_joined = build_user_joined_queues(client, queues, user_ids_needed)

    user_cache: dict[str, dict] = {}

    def user_info(uid: str) -> dict:
        if uid not in user_cache:
            try:
                u = client.request("GET", f"/api/v2/users/{uid}")
                user_cache[uid] = {
                    "name": u.get("name") or uid,
                    "email": u.get("email") or "",
                }
            except Exception:
                user_cache[uid] = {"name": uid, "email": ""}
        return user_cache[uid]

    rows: list[dict] = []
    review_rows: list[dict] = []
    counts = defaultdict(int)

    print()
    print("=" * 100)
    print(f"HQ PHONE SITE REASSIGNMENT — {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"Source site: {args.hq_site} | Mapped center sites: {len(site_to_queue)}")
    print("=" * 100)

    for entry in sorted(phone_users, key=lambda x: (x["phone"].get("name") or "").lower()):
        phone = entry["phone"]
        uid = entry["user_id"]
        phone_id = phone.get("id") or ""
        phone_name = (phone.get("name") or "").strip()
        user = user_info(uid)
        joined = user_joined.get(uid, [])
        joined_true_count = len(joined)
        mappable = mappable_joined_queues(joined, queue_to_site)
        distinct_sites = {m["site_id"] for m in mappable}

        row = {
            "phone_name": phone_name,
            "phone_id": phone_id,
            "user_name": user["name"],
            "user_email": user["email"],
            "user_id": uid,
            "current_site": args.hq_site,
            "joined_true_count": joined_true_count,
            "joined_mappable_count": len(mappable),
            "distinct_site_count": len(distinct_sites),
            "active_queue": "",
            "target_site_code": "",
            "target_site_name": "",
            "target_site_id": "",
            "action": "",
            "notes": "",
        }

        if not joined:
            row["action"] = "SKIP_NO_JOINED_QUEUE"
            row["notes"] = "No joined=true queues"
            counts[row["action"]] += 1
            rows.append(row)
            continue

        if joined_true_count > 1 or len(mappable) != 1:
            row["action"] = "REVIEW_AMBIGUOUS_E911"
            row["notes"] = (
                f"{joined_true_count} joined=true queue(s); "
                f"{len(mappable)} map to center site(s) — "
                f"manual review required (no auto-pick for e911)"
            )
            counts[row["action"]] += 1
            print(f"\nREVIEW (e911 ambiguous): {phone_name}")
            print(f"  User: {user['name']} ({user['email']})")
            print(f"  Joined=true: {joined_true_count} | mappable: {len(mappable)} | sites: {len(distinct_sites)}")
            for q in sorted(joined, key=lambda x: x.get("queue_name", "")):
                site = queue_to_site.get(q["queue_id"], {})
                site_label = site.get("site_code") or "(no center site)"
                print(f"    - {q.get('queue_name')} -> {site_label}")
            append_review_rows(
                review_rows,
                phone_name=phone_name,
                phone_id=phone_id,
                user=user,
                uid=uid,
                joined=joined,
                mappable=mappable,
                queue_to_site=queue_to_site,
            )
            rows.append(row)
            continue

        if not mappable:
            row["action"] = "SKIP_NO_MAPPED_JOINED_QUEUE"
            row["notes"] = "One joined=true queue, but it does not map to a center site"
            counts[row["action"]] += 1
            rows.append(row)
            continue

        active = mappable[0]
        row["active_queue"] = active.get("queue_name") or ""
        row["target_site_code"] = active.get("site_code") or ""
        row["target_site_name"] = active.get("site_name") or ""
        row["target_site_id"] = active.get("site_id") or ""

        if active["site_name"].upper() == args.hq_site.upper():
            row["action"] = "SKIP_ALREADY_HQ_TARGET"
            counts[row["action"]] += 1
            rows.append(row)
            continue

        # Unambiguous for e911: exactly one joined=true queue, maps to one center site
        if args.apply:
            detail = client.request("GET", f"/api/v2/telephony/providers/edges/phones/{phone_id}")
            target = {"id": active["site_id"], "name": active["site_name"]}
            payload = build_phone_update_payload(detail, target)
            client.request(
                "PUT",
                f"/api/v2/telephony/providers/edges/phones/{phone_id}",
                json_body=payload,
                expected_statuses=[200, 202],
            )
            row["action"] = "UPDATED"
        else:
            row["action"] = "WOULD_MOVE"

        counts[row["action"]] += 1
        print(f"\n{row['action']}: {phone_name}")
        print(f"  User: {user['name']} ({user['email']})")
        print(f"  Joined=true queue: {row['active_queue']}")
        print(f"  {args.hq_site} -> {row['target_site_code']} ({row['target_site_name']})")
        rows.append(row)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fieldnames = [
        "phone_name", "phone_id", "user_name", "user_email", "user_id",
        "current_site", "joined_true_count", "joined_mappable_count",
        "distinct_site_count", "active_queue",
        "target_site_code", "target_site_name", "target_site_id", "action", "notes",
    ]
    out_csv = output_path(f"hq_phone_reassign_by_queue_{ts}.csv")
    write_csv(out_csv, rows, fieldnames)

    review_csv = output_path(f"hq_phone_e911_review_{ts}.csv")
    review_fields = [
        "phone_name", "phone_id", "user_name", "user_email", "user_id",
        "joined_true_count", "joined_mappable_count", "distinct_site_count",
        "queue_name", "queue_id", "member_by", "maps_to_site",
        "site_code", "site_name", "site_id",
    ]
    write_csv(review_csv, review_rows, review_fields)

    print()
    print("=" * 100)
    print("SUMMARY")
    print(f"  HQ phones total:              {len(hq_phones)}")
    print(f"  HQ phones without user:       {hq_no_user} (correct at HQ)")
    print(f"  HQ phones with user:          {len(phone_users)}")
    for action, cnt in sorted(counts.items()):
        print(f"  {action}: {cnt}")
    print(f"\nMain CSV: {out_csv}")
    print(f"E911 review CSV (ambiguous): {review_csv} ({len(review_rows)} rows)")
    print("=" * 100)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
