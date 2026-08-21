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


def _try_user_request(
    access_token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
) -> Any | None:
    try:
        return _user_request(
            access_token,
            method,
            path,
            params=params,
            json_body=json_body,
        )
    except GenesysError as exc:
        message = str(exc)
        if "HTTP 403" in message or "HTTP 404" in message:
            return None
        raise


def _pick_user_station(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    for key in (
        "effectiveStation",
        "associatedStation",
        "defaultStation",
        "lastAssociatedStation",
    ):
        station = (payload or {}).get(key) or {}
        if isinstance(station, dict) and station.get("id"):
            return station
    return None


def _phone_has_site(phone: dict[str, Any]) -> bool:
    site = phone.get("site") or {}
    return bool(safe_str(site.get("id")) or safe_str(phone.get("site_id")))


def _merge_phone_site_from_filter(
    access_token: str,
    user_id: str,
    phone: dict[str, Any],
) -> dict[str, Any]:
    if _phone_has_site(phone) or not user_id:
        return phone

    payload = _try_user_request(
        access_token,
        "GET",
        "/api/v2/telephony/providers/edges/phones",
        params={
            "webRtcUser.id": user_id,
            "expand": "site",
            "pageSize": 25,
            "pageNumber": 1,
        },
    )
    entities = (payload or {}).get("entities") or []
    if not entities:
        return phone

    detail = entities[0]
    merged = dict(phone)
    if detail.get("id"):
        merged["id"] = detail["id"]
    if detail.get("name"):
        merged["name"] = detail["name"]
    if detail.get("site"):
        merged["site"] = detail["site"]
    return merged


def _phone_from_station(station: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": station.get("id"),
        "name": station.get("name"),
        "site": {},
    }


def _fetch_phone_by_id(access_token: str, phone_id: str) -> dict[str, Any] | None:
    return _try_user_request(
        access_token,
        "GET",
        f"/api/v2/telephony/providers/edges/phones/{phone_id}",
        params={"expand": "site"},
    )


def _find_phone_via_user_station(access_token: str) -> dict[str, Any] | None:
    payload = _try_user_request(access_token, "GET", "/api/v2/users/stations/me")
    station = _pick_user_station(payload if isinstance(payload, dict) else None)
    if not station:
        return None
    phone = _fetch_phone_by_id(access_token, safe_str(station.get("id")))
    return phone or _phone_from_station(station)


def _find_phone_via_webrtc_station(access_token: str, user_id: str) -> dict[str, Any] | None:
    payload = _try_user_request(
        access_token,
        "GET",
        "/api/v2/stations",
        params={
            "webRtcUserId": user_id,
            "pageSize": 25,
            "pageNumber": 1,
        },
    )
    entities = (payload or {}).get("entities") or []
    if not entities:
        return None
    station = entities[0]
    phone = _fetch_phone_by_id(access_token, safe_str(station.get("id")))
    return phone or _phone_from_station(station)


def _find_phone_via_phone_api(
    access_token: str,
    *,
    user_id: str,
    user_name: str,
) -> dict[str, Any] | None:
    if user_id:
        payload = _try_user_request(
            access_token,
            "GET",
            "/api/v2/telephony/providers/edges/phones",
            params={
                "webRtcUser.id": user_id,
                "fields": "webRtcUser",
                "expand": "site",
                "pageSize": 25,
                "pageNumber": 1,
            },
        )
        entities = (payload or {}).get("entities") or []
        if entities:
            return entities[0]

    expected_name = f"{user_name.strip()} Web"
    payload = _try_user_request(
        access_token,
        "GET",
        "/api/v2/telephony/providers/edges/phones",
        params={
            "name": expected_name,
            "expand": "site",
            "pageSize": 25,
            "pageNumber": 1,
        },
    )
    entities = (payload or {}).get("entities") or []
    if len(entities) == 1:
        return entities[0]

    expected_key = normalize_phone_key(expected_name)
    matches = [
        phone
        for phone in entities
        if normalize_phone_key(safe_str(phone.get("name"))) == expected_key
    ]
    if len(matches) == 1:
        return matches[0]
    return None


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
    # Prefer filtered phone lookup when available — it includes site info agents need.
    if user_id:
        payload = _try_user_request(
            access_token,
            "GET",
            "/api/v2/telephony/providers/edges/phones",
            params={
                "webRtcUser.id": user_id,
                "expand": "site",
                "pageSize": 25,
                "pageNumber": 1,
            },
        )
        entities = (payload or {}).get("entities") or []
        if entities:
            return entities[0]

    # Agent-friendly station paths — avoid the full phone inventory API.
    for finder in (
        lambda: _find_phone_via_user_station(access_token),
        lambda: _find_phone_via_webrtc_station(access_token, user_id) if user_id else None,
        lambda: _find_phone_via_phone_api(
            access_token,
            user_id=user_id,
            user_name=user_name,
        ),
    ):
        phone = finder()
        if phone:
            return _merge_phone_site_from_filter(access_token, user_id, phone)
    return None


def get_phone_detail(access_token: str, phone_id: str) -> dict[str, Any] | None:
    return _fetch_phone_by_id(access_token, phone_id)


SITE_PICKER_BUILD = "2026-06-29-agent-phone-change-v3"


def _admin_credentials_available() -> bool:
    try:
        from core.config import get_client_credentials, load_client_env

        load_client_env()
        get_client_credentials()
        return True
    except Exception:
        return False


def change_phone_site_admin(phone_id: str, target_site: dict[str, Any]) -> dict[str, Any] | None:
    if not _admin_credentials_available():
        return None
    from core.cli import create_client

    client = create_client()
    phone = client.request(
        "GET",
        f"/api/v2/telephony/providers/edges/phones/{phone_id}",
    )
    payload = build_phone_update_payload(phone, target_site)
    return client.request(
        "PUT",
        f"/api/v2/telephony/providers/edges/phones/{phone_id}",
        json_body=payload,
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
    target_site = resolve_site(access_token, site_id)
    minimal_payload = {
        "site": {
            "id": target_site["id"],
            "name": target_site.get("name"),
        }
    }

    # Agents with phone assign (but not view) can often update site with a minimal PUT.
    result = _try_user_request(
        access_token,
        "PUT",
        f"/api/v2/telephony/providers/edges/phones/{phone_id}",
        json_body=minimal_payload,
    )
    if result is not None:
        return result or {}

    phone = get_phone_detail(access_token, phone_id)
    if phone:
        full_payload = build_phone_update_payload(phone, target_site)
        result = _try_user_request(
            access_token,
            "PUT",
            f"/api/v2/telephony/providers/edges/phones/{phone_id}",
            json_body=full_payload,
        )
        if result is not None:
            return result or {}

    admin_result = change_phone_site_admin(phone_id, target_site)
    if admin_result is not None:
        return admin_result

    if _admin_credentials_available():
        raise GenesysError(
            "Could not update phone location even with the server admin client. "
            "Check Genesys telephony permissions for the target site."
        )
    raise GenesysError(
        "Could not update phone location with your agent permissions. "
        "Ask IT to add GENESYS_CLIENT_ID and GENESYS_CLIENT_SECRET on Render for admin fallback, "
        "or grant Telephony > Phone > Assign on the target site."
    )


def phone_summary(
    phone: dict[str, Any] | None,
    *,
    sites_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not phone:
        return None
    site = phone.get("site") or {}
    site_id = safe_str(site.get("id"))
    site_name = safe_str(site.get("name"))
    if site_id and not site_name and sites_by_id:
        matched = sites_by_id.get(site_id) or {}
        site_name = safe_str(matched.get("name"))
    return {
        "id": safe_str(phone.get("id")),
        "name": safe_str(phone.get("name")),
        "site_id": site_id,
        "site_name": site_name,
    }


def fetch_phone_site_admin_fallback(user_id: str) -> dict[str, str]:
    """Read-only phone/site lookup via admin client creds when user token cannot view phones."""
    try:
        from core.cli import create_client
        from core.config import get_client_credentials, load_client_env

        load_client_env()
        get_client_credentials()
    except Exception:
        return {}

    try:
        client = create_client()
        payload = client.request(
            "GET",
            "/api/v2/telephony/providers/edges/phones",
            params={
                "webRtcUser.id": user_id,
                "expand": "site",
                "pageSize": 5,
                "pageNumber": 1,
            },
        )
    except GenesysError:
        return {}

    entities = (payload or {}).get("entities") or []
    if not entities:
        return {}

    phone = entities[0]
    site = phone.get("site") or {}
    return {
        "id": safe_str(phone.get("id")),
        "name": safe_str(phone.get("name")),
        "site_id": safe_str(site.get("id")),
        "site_name": safe_str(site.get("name")),
    }


def enrich_phone_site_name(
    access_token: str,
    phone: dict[str, Any] | None,
    *,
    sites_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not phone or phone.get("site_name"):
        return phone
    site_id = safe_str(phone.get("site_id"))
    if not site_id:
        return phone
    if sites_by_id:
        matched = sites_by_id.get(site_id) or {}
        site_name = safe_str(matched.get("name"))
        if site_name:
            return {**phone, "site_name": site_name}
    try:
        site = resolve_site(access_token, site_id)
        site_name = safe_str((site or {}).get("name"))
        if site_name:
            return {**phone, "site_name": site_name}
    except GenesysError:
        pass
    return phone
