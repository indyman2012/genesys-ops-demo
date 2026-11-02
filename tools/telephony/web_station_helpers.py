"""Minimal WebRTC phone / station helpers for ops web workflows (committed for Render)."""
from __future__ import annotations

from collections import defaultdict

from core.genesys_client import GenesysClient, GenesysError, find_by_name, safe_str

PHONE_PAGE_SIZE = 50


def normalize_phone_key(name: str) -> str:
    parts = " ".join((name or "").split())
    folded = parts.casefold()
    if folded.endswith(" web"):
        return folded
    if folded.endswith("web") and len(folded) > 3:
        return f"{folded[:-3].rstrip()} web"
    return folded


def load_phones(client: GenesysClient) -> list[dict]:
    page = 1
    phones: list[dict] = []
    while True:
        payload = client.request(
            "GET",
            "/api/v2/telephony/providers/edges/phones",
            params={"pageSize": PHONE_PAGE_SIZE, "pageNumber": page},
        )
        phones.extend(payload.get("entities") or [])
        if page >= int(payload.get("pageCount") or 1):
            break
        page += 1
    return phones


def index_phones_by_user(phones: list[dict]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    by_user: dict[str, dict] = {}
    by_name: dict[str, list[dict]] = defaultdict(list)
    for phone in phones:
        name = safe_str(phone.get("name"))
        if name:
            by_name[normalize_phone_key(name)].append(phone)
        for key in ("webRtcUser", "owner", "assignedUser"):
            obj = phone.get(key) or {}
            if isinstance(obj, dict):
                uid = safe_str(obj.get("id"))
                if uid and uid not in by_user:
                    by_user[uid] = phone
    return by_user, by_name


def find_web_phone(
    client: GenesysClient,
    user_id: str,
    user_name: str,
    by_user: dict[str, dict],
    by_name: dict[str, list[dict]],
) -> dict | None:
    if user_id in by_user:
        return by_user[user_id]
    for page in range(1, 5):
        payload = client.request(
            "GET",
            "/api/v2/telephony/providers/edges/phones",
            params={
                "webRtcUser.id": user_id,
                "pageSize": PHONE_PAGE_SIZE,
                "pageNumber": page,
            },
        )
        entities = payload.get("entities") or []
        if entities:
            return entities[0]
        if page >= int(payload.get("pageCount") or 1):
            break
    expected = normalize_phone_key(f"{user_name.strip()} Web")
    phones = by_name.get(expected, [])
    return phones[0] if phones else None


def get_phone_base_settings(client: GenesysClient) -> tuple[dict | None, dict | None]:
    for page in range(1, 20):
        payload = client.request(
            "GET",
            "/api/v2/telephony/providers/edges/phones",
            params={"pageSize": 100, "pageNumber": page},
        )
        for phone in payload.get("entities") or []:
            name = safe_str(phone.get("name"))
            if not name.casefold().endswith(" web"):
                continue
            detail = client.request(
                "GET",
                f"/api/v2/telephony/providers/edges/phones/{phone['id']}",
            )
            pbs = detail.get("phoneBaseSettings") or {}
            lines = detail.get("lines") or []
            lbs = (lines[0].get("lineBaseSettings") or {}) if lines else {}
            if pbs.get("id") and lbs.get("id"):
                return pbs, lbs
        if page >= int(payload.get("pageCount") or 1):
            break
    return None, None


def create_web_phone(
    client: GenesysClient,
    user: dict,
    site: dict,
    phone_base: dict,
    line_base: dict,
    *,
    apply: bool,
) -> tuple[dict | None, str]:
    name = f"{safe_str(user.get('name')).strip()} Web"
    payload = {
        "name": name,
        "state": "active",
        "site": {"id": site["id"], "name": site.get("name")},
        "phoneBaseSettings": {"id": phone_base["id"]},
        "webRtcUser": {"id": user["id"]},
        "lines": [{"displayOrder": 1, "lineBaseSettings": {"id": line_base["id"]}}],
    }
    if not apply:
        return None, f"would_create_web_phone:{name}@{site.get('name')}"
    try:
        created = client.request(
            "POST",
            "/api/v2/telephony/providers/edges/phones",
            json_body=payload,
            expected_statuses=[200, 201],
        )
        return created, f"created_web_phone:{name}"
    except GenesysError as exc:
        return None, f"web_phone_error:{exc}"


def resolve_site_by_name(client: GenesysClient, site_name: str) -> dict | None:
    return find_by_name(client.paged_get("/api/v2/telephony/providers/edges/sites"), site_name)
