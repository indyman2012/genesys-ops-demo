from __future__ import annotations

import json
import re
from typing import Any

import requests

from core.config import get_api_host
from core.genesys_client import GenesysError, safe_str


def normalize_phone_key(name: str) -> str:
    folded = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    return folded


def build_phone_update_payload(phone: dict[str, Any], target_site: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(phone))
    payload["site"] = {"id": target_site["id"], "name": target_site["name"]}
    return payload


def _user_request(
    access_token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
) -> Any:
    api_host = get_api_host().rstrip("/")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    response = requests.request(
        method,
        f"{api_host}{path}",
        headers=headers,
        params=params,
        json=json_body,
        timeout=60,
    )
    if response.status_code == 401:
        raise GenesysError("Genesys session expired — sign in again.")
    if response.status_code >= 400:
        raise GenesysError(
            f"HTTP {response.status_code} on {method} {path}: {response.text[:500]}"
        )
    if response.status_code == 204 or not response.text.strip():
        return None
    return response.json()


def _user_paged_get(access_token: str, path: str, *, page_size: int = 100) -> list[dict[str, Any]]:
    page = 1
    rows: list[dict[str, Any]] = []
    while True:
        payload = _user_request(
            access_token,
            "GET",
            path,
            params={"pageSize": page_size, "pageNumber": page},
        )
        entities = (payload or {}).get("entities") or []
        if not isinstance(entities, list):
            raise GenesysError(f"Unexpected list shape from {path}")
        rows.extend(entities)
        page_count = int((payload or {}).get("pageCount") or 1)
        if page >= page_count:
            break
        page += 1
    return rows


def fetch_sites_with_user_token(access_token: str) -> list[dict[str, Any]]:
    sites = _user_paged_get(access_token, "/api/v2/telephony/providers/edges/sites")
    sites.sort(key=lambda s: safe_str(s.get("name")).lower())
    return sites


def find_user_web_phone(
    access_token: str,
    *,
    user_id: str,
    user_name: str,
) -> dict[str, Any] | None:
    phones = _user_paged_get(access_token, "/api/v2/telephony/providers/edges/phones")
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


def get_phone_detail(access_token: str, phone_id: str) -> dict[str, Any]:
    return _user_request(
        access_token,
        "GET",
        f"/api/v2/telephony/providers/edges/phones/{phone_id}",
    )


def resolve_site(access_token: str, site_id: str) -> dict[str, Any]:
    return _user_request(
        access_token,
        "GET",
        f"/api/v2/telephony/providers/edges/sites/{site_id}",
    )


def change_phone_site(
    access_token: str,
    *,
    phone_id: str,
    site_id: str,
) -> dict[str, Any]:
    phone = get_phone_detail(access_token, phone_id)
    target_site = resolve_site(access_token, site_id)
    payload = build_phone_update_payload(phone, target_site)
    return _user_request(
        access_token,
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
