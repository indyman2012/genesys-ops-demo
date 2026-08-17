from __future__ import annotations

import json
import re
from typing import Any

import requests

from core.config import get_api_host
from core.genesys_client import GenesysClient, GenesysError, safe_str


def normalize_phone_key(name: str) -> str:
    folded = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    return folded


def build_phone_update_payload(phone: dict[str, Any], target_site: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(phone))
    payload["site"] = {"id": target_site["id"], "name": target_site["name"]}
    return payload


def fetch_sites_with_user_token(access_token: str) -> list[dict[str, Any]]:
    api_host = get_api_host()
    page = 1
    sites: list[dict[str, Any]] = []
    while True:
        response = requests.get(
            f"{api_host}/api/v2/telephony/providers/edges/sites",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"pageSize": 100, "pageNumber": page},
            timeout=60,
        )
        if response.status_code >= 400:
            raise GenesysError(
                f"Sites list failed ({response.status_code}): {response.text[:500]}"
            )
        payload = response.json()
        entities = payload.get("entities") or []
        sites.extend(entities)
        page_count = payload.get("pageCount")
        if page_count is not None and page >= int(page_count):
            break
        if not entities:
            break
        page += 1
    sites.sort(key=lambda s: safe_str(s.get("name")).lower())
    return sites


def find_user_web_phone(
    client: GenesysClient,
    *,
    user_id: str,
    user_name: str,
) -> dict[str, Any] | None:
    phones = client.paged_get("/api/v2/telephony/providers/edges/phones")
    for phone in phones:
        web_user = phone.get("webRtcUser") or {}
        if safe_str(web_user.get("id")) == user_id:
            return phone

    expected_key = normalize_phone_key(f"{user_name.strip()} Web")
    matches: list[dict[str, Any]] = []
    for phone in phones:
        if normalize_phone_key(safe_str(phone.get("name"))) == expected_key:
            matches.append(phone)
    if len(matches) == 1:
        return matches[0]
    return None


def get_phone_detail(client: GenesysClient, phone_id: str) -> dict[str, Any]:
    return client.request("GET", f"/api/v2/telephony/providers/edges/phones/{phone_id}")


def resolve_site(client: GenesysClient, site_id: str) -> dict[str, Any]:
    return client.request("GET", f"/api/v2/telephony/providers/edges/sites/{site_id}")


def change_phone_site(
    client: GenesysClient,
    *,
    phone_id: str,
    site_id: str,
) -> dict[str, Any]:
    phone = get_phone_detail(client, phone_id)
    target_site = resolve_site(client, site_id)
    payload = build_phone_update_payload(phone, target_site)
    return client.request(
        "PUT",
        f"/api/v2/telephony/providers/edges/phones/{phone_id}",
        json_body=payload,
    )


def phone_summary(phone: dict[str, Any] | None) -> dict[str, Any] | None:
    if not phone:
        return None
    site = phone.get("site") or {}
    return {
        "id": safe_str(phone.get("id")),
        "name": safe_str(phone.get("name")),
        "site_id": safe_str(site.get("id")),
        "site_name": safe_str(site.get("name")),
    }
