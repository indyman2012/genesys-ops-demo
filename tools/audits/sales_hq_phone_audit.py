#!/usr/bin/env python3
"""
Audit Sales queue members with Headquarters Web phones that should be on market sites.

Read-only. Uses queue-to-site mapping from reassign_hq_phones_by_active_queue.py.
Outputs CSV under outputs/ with per-member status cohorts.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_env_arg, create_client, output_path, tool_log, write_csv
from core.genesys_client import safe_str

from tools.telephony.reassign_hq_phones_by_active_queue import (
    DEFAULT_HQ_SITE,
    build_queue_site_maps,
    build_user_joined_queues,
    queue_parts,
)

SALES_QUEUE_PREFIX = re.compile(r"^Sales\s*-", re.I)

OUTPUT_FIELDS = [
    "user_name",
    "email",
    "queue_name",
    "current_phone_site",
    "expected_site",
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


def is_sales_queue(name: str) -> bool:
    return bool(SALES_QUEUE_PREFIX.match((name or "").strip()))


def suggest_site_code(queue_name: str) -> str:
    """Best-effort 6-char site code when no site exists yet."""
    market, center = queue_parts(queue_name)
    if not market or not center:
        return ""
    letters = re.sub(r"[^A-Za-z]", "", center).upper()
    if len(letters) >= 3:
        return f"{market}{letters[:3]}"
    compact = re.sub(r"[^A-Za-z0-9]", "", center).upper()
    if len(compact) >= 3:
        return f"{market}{compact[:3]}"
    return ""


def build_phone_indexes(
    client,
    phones: list[dict],
    user_ids: set[str] | None = None,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Return (by_user_id, by_name) maps; detail-fetch webRtcUser only when needed."""
    by_user: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    for phone in phones:
        name = safe_str(phone.get("name"))
        if name:
            by_name[normalize_phone_key(name)] = phone
        for key in ("webRtcUser", "owner", "assignedUser"):
            obj = phone.get(key) or {}
            if isinstance(obj, dict):
                uid = safe_str(obj.get("id"))
                if uid:
                    by_user[uid] = phone

    if not user_ids:
        return by_user, by_name

    unresolved = user_ids - set(by_user.keys())
    if not unresolved:
        return by_user, by_name

    web_phones = [
        p for p in phones
        if normalize_phone_key(safe_str(p.get("name"))).endswith(" web")
    ]
    tool_log("info", f"Detail lookup for {len(unresolved)} members without list webRtcUser...")
    fetched = 0
    for phone in web_phones:
        try:
            detail = client.request(
                "GET",
                f"/api/v2/telephony/providers/edges/phones/{phone['id']}",
            )
        except Exception:
            continue
        uid = ""
        for key in ("webRtcUser", "owner", "assignedUser"):
            obj = detail.get(key) or {}
            if isinstance(obj, dict):
                uid = safe_str(obj.get("id"))
                if uid:
                    break
        if not uid or uid not in unresolved:
            continue
        merged = {**phone, **{k: v for k, v in detail.items() if v is not None}}
        by_user[uid] = merged
        name = safe_str(merged.get("name"))
        if name:
            by_name[normalize_phone_key(name)] = merged
        fetched += 1
        if fetched >= len(unresolved):
            break

    return by_user, by_name


def find_web_phone(
    user_id: str,
    user_name: str,
    by_user: dict[str, dict],
    by_name: dict[str, dict],
) -> dict | None:
    if user_id in by_user:
        return by_user[user_id]
    expected = normalize_phone_key(f"{user_name.strip()} Web")
    return by_name.get(expected)


def phone_site_name(phone: dict | None) -> str:
    if not phone:
        return ""
    site = phone.get("site") or {}
    return safe_str(site.get("name"))


def classify_status(
    *,
    current_site: str,
    expected_site: str,
    has_mapping: bool,
    joined_count: int,
    joined_sales_count: int,
    mappable_count: int,
    queue_name: str,
) -> tuple[str, str]:
    notes_parts: list[str] = []

    if not current_site:
        return "NO_WEB_PHONE", "No Web phone found for user"

    expected_upper = expected_site.upper()
    current_upper = current_site.upper()
    hq_upper = DEFAULT_HQ_SITE.upper()

    if expected_site and current_upper == expected_upper:
        return "OK", "Phone already on expected market site"

    if joined_count > 1:
        notes_parts.append(
            f"{joined_count} joined=true queue(s) total ({joined_sales_count} Sales)"
        )
        if mappable_count > 1:
            notes_parts.append(f"{mappable_count} map to distinct center sites")

    if current_upper != hq_upper:
        if expected_site and current_upper != expected_upper:
            note = "; ".join(notes_parts) if notes_parts else ""
            extra = f"Phone on {current_site}, expected {expected_site}"
            return "WRONG_SITE", f"{extra}; {note}".strip("; ")
        if not expected_site:
            return "NOT_HQ_NO_MAPPING", "Not at HQ; queue has no market site mapping"
        note = "; ".join(notes_parts) if notes_parts else "Not at Headquarters"
        return "NOT_HQ", note

    # Phone at Headquarters
    if joined_count > 1:
        note = "; ".join(notes_parts) if notes_parts else "Multiple joined=true queues"
        if has_mapping:
            return "AMBIGUOUS", f"{note}; would need manual e911 pick"
        suggested = suggest_site_code(queue_name)
        extra = f"suggested site code {suggested}" if suggested else "no site code inferred"
        return "AMBIGUOUS", f"{note}; no site mapping ({extra})"

    if not has_mapping:
        suggested = suggest_site_code(queue_name)
        if suggested:
            return "NEEDS NEW SITE", f"No 6-char market site mapped; suggest creating {suggested}"
        return "NEEDS NEW SITE", "No 6-char market site mapped for this Sales queue"

    return "NEEDS FIX", "HQ Web phone; unambiguous queue-to-site mapping exists"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit Sales queue members on HQ Web phones vs expected market sites.",
    )
    add_env_arg(parser)
    parser.add_argument("--hq-site", default=DEFAULT_HQ_SITE, help="Headquarters site name")
    args = parser.parse_args()

    client = create_client(env_file=args.env_file)

    tool_log("info", "Loading sites and queues...")
    sites = client.paged_get("/api/v2/telephony/providers/edges/sites")
    all_queues = client.paged_get("/api/v2/routing/queues")
    sales_queues = [q for q in all_queues if is_sales_queue(q.get("name") or "")]
    tool_log("info", f"Found {len(sales_queues)} Sales queues (of {len(all_queues)} total)")

    queue_to_site, site_to_queue = build_queue_site_maps(client, sites, all_queues)
    sales_queue_ids = {q["id"] for q in sales_queues}

    tool_log("info", "Scanning joined=true Sales queue members...")
    member_rows: list[tuple[dict, dict, dict]] = []
    user_ids: set[str] = set()
    for i, q in enumerate(sorted(sales_queues, key=lambda x: (x.get("name") or "").lower()), 1):
        qid = q["id"]
        qname = q.get("name") or qid
        if i == 1 or i % 10 == 0 or i == len(sales_queues):
            tool_log("info", f"  Queue members {i}/{len(sales_queues)}: {qname}")
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
            user_ids.add(uid)
            member_rows.append((q, member, user))

    tool_log("info", "Loading phones and building indexes...")
    phones = client.paged_get("/api/v2/telephony/providers/edges/phones", params={"pageSize": 100})
    by_user, by_name = build_phone_indexes(client, phones, user_ids)

    tool_log("info", f"Building joined-queue map for {len(user_ids)} unique members...")
    user_joined = build_user_joined_queues(client, all_queues, user_ids)

    user_cache: dict[str, dict] = {}

    def user_info(uid: str, fallback: dict) -> dict:
        if uid not in user_cache:
            try:
                u = client.request("GET", f"/api/v2/users/{uid}")
                user_cache[uid] = {
                    "name": u.get("name") or fallback.get("name") or uid,
                    "email": u.get("email") or "",
                }
            except Exception:
                user_cache[uid] = {
                    "name": fallback.get("name") or uid,
                    "email": fallback.get("email") or "",
                }
        return user_cache[uid]

    rows: list[dict] = []
    for q, _member, user_stub in member_rows:
        uid = user_stub["id"]
        user = user_info(uid, user_stub)
        qid = q["id"]
        qname = q.get("name") or ""

        phone = find_web_phone(uid, user["name"], by_user, by_name)
        current_site = phone_site_name(phone)

        site_info = queue_to_site.get(qid, {})
        expected_site = site_info.get("site_name") or site_info.get("site_code") or ""
        has_mapping = bool(expected_site)

        joined = user_joined.get(uid, [])
        joined_sales = [j for j in joined if is_sales_queue(j.get("queue_name") or "")]
        mappable = [j for j in joined if j["queue_id"] in queue_to_site]

        status, notes = classify_status(
            current_site=current_site,
            expected_site=expected_site,
            has_mapping=has_mapping,
            joined_count=len(joined),
            joined_sales_count=len(joined_sales),
            mappable_count=len({queue_to_site[j["queue_id"]]["site_id"] for j in mappable}),
            queue_name=qname,
        )

        rows.append({
            "user_name": user["name"],
            "email": user["email"],
            "queue_name": qname,
            "current_phone_site": current_site or "(none)",
            "expected_site": expected_site or "(none)",
            "status": status,
            "notes": notes,
        })

    rows.sort(key=lambda r: (r["queue_name"].lower(), r["user_name"].lower()))

    ts = datetime.now().strftime("%Y%m%d")
    out_csv = output_path(f"sales_hq_phone_audit_{ts}.csv")
    write_csv(out_csv, rows, OUTPUT_FIELDS)

    # Summary by queue
    by_queue: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        by_queue[r["queue_name"]][r["status"]] += 1

    print()
    print("=" * 100)
    print("SALES HQ WEB PHONE AUDIT")
    print(f"Sales queues: {len(sales_queues)} | Members (joined=true): {len(rows)}")
    print(f"Mapped Sales/center sites: {len(site_to_queue)}")
    print("=" * 100)

    status_totals: dict[str, int] = defaultdict(int)
    for r in rows:
        status_totals[r["status"]] += 1

    print("\nStatus totals:")
    for status in sorted(status_totals.keys()):
        print(f"  {status}: {status_totals[status]}")

    print("\nBy queue (NEEDS FIX / NEEDS NEW SITE / AMBIGUOUS / OK):")
    print(f"{'Queue':<45} {'FIX':>4} {'NEW':>4} {'AMB':>4} {'OK':>4} {'Expected site':<12}")
    print("-" * 100)
    queues_needing_work: list[tuple[str, int, str]] = []
    for qname in sorted(by_queue.keys()):
        counts = by_queue[qname]
        fix = counts.get("NEEDS FIX", 0)
        new = counts.get("NEEDS NEW SITE", 0)
        amb = counts.get("AMBIGUOUS", 0)
        ok = counts.get("OK", 0)
        q_obj = next((q for q in sales_queues if q.get("name") == qname), None)
        expected = ""
        if q_obj:
            si = queue_to_site.get(q_obj["id"], {})
            expected = si.get("site_code") or si.get("site_name") or suggest_site_code(qname)
        print(f"{qname[:45]:<45} {fix:>4} {new:>4} {amb:>4} {ok:>4} {expected:<12}")
        if fix or new:
            queues_needing_work.append((qname, fix + new, expected))

    needs_fix = status_totals.get("NEEDS FIX", 0)
    needs_new = status_totals.get("NEEDS NEW SITE", 0)
    ambiguous = status_totals.get("AMBIGUOUS", 0)

    print()
    print("RECOMMENDATION")
    if needs_fix and not needs_new and not ambiguous:
        print("  GLOBAL BATCH: All HQ fixes are unambiguous. Run reassign_hq_phones_by_active_queue.py --apply")
        print("  (ensure MANUAL_SITE_QUEUE_HINTS covers any new markets first).")
    elif needs_fix and (needs_new or ambiguous):
        print("  MIXED:")
        if needs_fix:
            print(f"    - {needs_fix} unambiguous HQ moves -> reassign_hq_phones_by_active_queue.py --apply")
        if needs_new:
            print(f"    - {needs_new} need new market site(s) first (like CLEMKT one-off setup per market)")
        if ambiguous:
            print(f"    - {ambiguous} ambiguous (multi-queue) -> manual e911 review, no auto-pick")
    elif needs_new and not needs_fix:
        print("  INDIVIDUAL SITE CREATION: No unambiguous batch moves; create market sites per queue first.")
    else:
        print("  NO HQ FIXES NEEDED for unambiguous Sales queue members.")

    if queues_needing_work:
        print("\n  Queues with work remaining:")
        for qname, cnt, expected in queues_needing_work:
            print(f"    - {qname}: {cnt} member(s), expected site {expected or '(TBD)'}")

    print(f"\nCSV: {out_csv}")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
