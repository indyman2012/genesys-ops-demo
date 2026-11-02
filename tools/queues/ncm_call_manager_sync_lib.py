"""NCM roster sync: queue membership, station, Care Managers Lookup data table."""
from __future__ import annotations

import re
from io import BytesIO
from typing import Any, BinaryIO

from openpyxl import load_workbook

from core.datatables import (
    build_call_manager_row_body,
    create_datatable_row,
    datatable_name_from_env,
    find_datatable_by_name,
    find_row_by_dnis,
    list_datatable_rows,
    load_datatable_with_schema,
    schema_property_key,
)
from core.genesys_client import GenesysClient, GenesysError, ensure_e164, find_by_name, safe_str
from core.lookups import find_user_by_email, user_is_queue_member
from tools.telephony.reassign_hq_phones_by_active_queue import DEFAULT_HQ_SITE, queue_parts
from tools.telephony.web_station_helpers import (
    create_web_phone,
    find_web_phone,
    get_phone_base_settings,
    index_phones_by_user,
    load_phones,
    resolve_site_by_name,
)

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

RESULT_FIELDS = [
    "input_email",
    "queue_name",
    "phone",
    "user_id",
    "user_name",
    "status",
    "actions",
    "detail",
    "center_queue",
    "datatable_row",
]


def normalize_email(value: str) -> str:
    return safe_str(value).strip().lower()


def _header_map(header_row: tuple[Any, ...]) -> dict[str, int]:
    header = [safe_str(cell).strip().lower() for cell in header_row]
    mapping: dict[str, int] = {}
    for idx, label in enumerate(header):
        if not label:
            continue
        if label in {"email", "e-mail", "work email", "email address", "mail"} or "email" in label:
            mapping.setdefault("email", idx)
        if label in {"queue", "queue name", "queuename"} or "queue" in label:
            mapping.setdefault("queue", idx)
        if label in {"phone", "phone number", "telephone", "dnis"} or "phone" in label:
            mapping.setdefault("phone", idx)
    return mapping


def parse_ncm_roster_workbook(source: BinaryIO | bytes) -> list[dict[str, str]]:
    data = source.read() if hasattr(source, "read") else source
    wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []

    col = _header_map(rows[0])
    email_col = col.get("email", 0)
    queue_col = col.get("queue", 1)
    phone_col = col.get("phone", 2)
    start = 1 if any(safe_str(c).strip() for c in rows[0]) else 0

    out: list[dict[str, str]] = []
    for row in rows[start:]:
        if not row:
            continue
        email = normalize_email(row[email_col] if email_col < len(row) else "")
        queue_name = safe_str(row[queue_col] if queue_col < len(row) else "").strip()
        phone = safe_str(row[phone_col] if phone_col < len(row) else "").strip()
        if not email and not queue_name and not phone:
            continue
        if email.lower() in {"email", "email address"}:
            continue
        out.append({"email": email, "queue_name": queue_name, "phone": phone})
    return out


def resolve_patient_services_center_queue(
    ncm_queue_name: str,
    queues: list[dict[str, Any]],
) -> str:
    """Map NCM_* queue name to Patient Services queue display name for CenterQueue."""
    name = safe_str(ncm_queue_name).strip()
    mkt, center = queue_parts(name)
    if not center:
        parts = [p for p in name.split("_") if p]
        if len(parts) >= 2:
            center = parts[-1].replace("_", " ")
            if len(parts) >= 3:
                mkt = parts[-2]

    candidates: list[str] = []
    if mkt and center:
        center_sp = center.replace("_", " ")
        candidates.extend(
            [
                f"Patient Services - {mkt} {center_sp}",
                f"Patient Services - {mkt} - {center_sp}",
                f"Patient Services - {mkt}-{center_sp}",
            ]
        )
    if center:
        center_sp = center.replace("_", " ")
        candidates.append(f"Patient Services - {center_sp}")

    for candidate in candidates:
        hit = find_by_name(queues, candidate)
        if hit:
            return safe_str(hit.get("name"))

    center_fold = safe_str(center or name.split("_")[-1]).replace("_", " ").lower()
    for queue in queues:
        qname = safe_str(queue.get("name"))
        if not qname.lower().startswith("patient services"):
            continue
        normalized = qname.lower().replace("-", " ")
        if center_fold and center_fold in normalized:
            return qname

    raise GenesysError(
        f"Could not resolve Patient Services center queue for NCM queue {name!r} (segment {center_fold!r})"
    )


def _user_has_station(
    client: GenesysClient,
    user_id: str,
    *,
    phones_by_user: dict[str, dict],
    phones_by_name: dict[str, list[dict]],
    stations_by_user: dict[str, dict],
    user_name: str,
) -> tuple[bool, str]:
    if user_id in stations_by_user:
        st = stations_by_user[user_id]
        return True, safe_str(st.get("name")) or "station"
    web = find_web_phone(client, user_id, user_name, phones_by_user, phones_by_name)
    if web:
        return True, safe_str(web.get("name")) or "web phone"
    return False, ""


def _ensure_queue_joined(
    client: GenesysClient,
    *,
    user_id: str,
    queue: dict[str, Any],
    apply: bool,
) -> list[str]:
    queue_id = safe_str(queue.get("id"))
    queue_name = safe_str(queue.get("name"))
    actions: list[str] = []
    member = user_is_queue_member(client, queue_id, user_id)
    if member:
        if member.get("joined"):
            actions.append(f"queue ok: joined on {queue_name}")
            return actions
        if apply:
            client.request(
                "PATCH",
                f"/api/v2/users/{user_id}/queues/{queue_id}",
                json_body={"joined": True},
            )
            actions.append(f"set joined=true on {queue_name}")
        else:
            actions.append(f"would set joined=true on {queue_name}")
        return actions

    if apply:
        client.request(
            "POST",
            f"/api/v2/routing/queues/{queue_id}/members",
            json_body=[{"id": user_id}],
            expected_statuses=[200, 201, 202],
        )
        client.request(
            "PATCH",
            f"/api/v2/users/{user_id}/queues/{queue_id}",
            json_body={"joined": True},
        )
        actions.append(f"added member + joined=true on {queue_name}")
    else:
        actions.append(f"would add to {queue_name} with joined=true")
    return actions


def _ensure_station(
    client: GenesysClient,
    user: dict[str, Any],
    *,
    apply: bool,
    phones_by_user: dict[str, dict],
    phones_by_name: dict[str, list[dict]],
    stations_by_user: dict[str, dict],
    telephony_cache: dict[str, Any],
) -> tuple[list[str], str]:
    uid = safe_str(user.get("id"))
    name = safe_str(user.get("name"))
    has, label = _user_has_station(
        client,
        uid,
        phones_by_user=phones_by_user,
        phones_by_name=phones_by_name,
        stations_by_user=stations_by_user,
        user_name=name,
    )
    if has:
        return [f"station ok: {label}"], ""

    if not apply:
        return ["would ensure station (WebRTC phone at Headquarters if missing)"], "no station"

    if not telephony_cache.get("ready"):
        return [], telephony_cache.get("error") or "telephony prerequisites not loaded"

    site = telephony_cache["hq_site"]
    phone_base = telephony_cache["phone_base"]
    line_base = telephony_cache["line_base"]
    _created, note = create_web_phone(client, user, site, phone_base, line_base, apply=True)
    if note.startswith("web_phone_error"):
        return [], note.replace("web_phone_error:", "", 1)
    return [note], ""


def _index_stations_by_user(client: GenesysClient) -> dict[str, dict]:
    by_user: dict[str, dict] = {}
    for station in client.paged_get("/api/v2/stations", params={"pageSize": 100}):
        if safe_str(station.get("status")).upper() != "ASSOCIATED":
            continue
        uid = safe_str(station.get("userId"))
        if uid:
            by_user[uid] = station
    return by_user


def _load_telephony_cache(client: GenesysClient) -> dict[str, Any]:
    try:
        hq_site = resolve_site_by_name(client, DEFAULT_HQ_SITE)
        if not hq_site:
            return {"ready": False, "error": f"Site not found: {DEFAULT_HQ_SITE}"}
        phone_base, line_base = get_phone_base_settings(client)
        if not phone_base or not line_base:
            return {"ready": False, "error": "Could not load phone base settings"}
        return {
            "ready": True,
            "hq_site": hq_site,
            "phone_base": phone_base,
            "line_base": line_base,
        }
    except GenesysError as exc:
        return {"ready": False, "error": str(exc)}


def run_ncm_call_manager_sync(
    client: GenesysClient,
    roster_rows: list[dict[str, str]],
    *,
    apply: bool,
    datatable_name: str | None = None,
) -> dict[str, Any]:
    datatable_name = datatable_name or datatable_name_from_env()
    table_meta = find_datatable_by_name(client, datatable_name)
    table_id = safe_str(table_meta.get("id"))
    table_doc = load_datatable_with_schema(client, table_id)
    schema = table_doc.get("schema") or {}
    dnis_field = schema_property_key(schema, "dnis", "key")

    all_queues = client.paged_get("/api/v2/routing/queues")
    datatable_rows = list_datatable_rows(client, table_id)
    stations_by_user = _index_stations_by_user(client)
    phones = load_phones(client)
    phones_by_user, phones_by_name = index_phones_by_user(phones)
    telephony_cache = _load_telephony_cache(client)

    stats = {
        "ok": 0,
        "error": 0,
        "queue_fixes": 0,
        "station_fixes": 0,
        "datatable_created": 0,
        "datatable_exists": 0,
    }
    result_rows: list[dict[str, Any]] = []

    for item in roster_rows:
        email = normalize_email(item.get("email", ""))
        queue_name = safe_str(item.get("queue_name")).strip()
        phone_raw = safe_str(item.get("phone")).strip()
        actions: list[str] = []
        base = {
            "input_email": email,
            "queue_name": queue_name,
            "phone": phone_raw,
            "user_id": "",
            "user_name": "",
            "status": "error",
            "actions": "",
            "detail": "",
            "center_queue": "",
            "datatable_row": "",
        }

        if not email or not EMAIL_PATTERN.match(email):
            base["detail"] = "Invalid or missing email"
            stats["error"] += 1
            result_rows.append(base)
            continue
        if not queue_name:
            base["detail"] = "Missing queue name"
            stats["error"] += 1
            result_rows.append(base)
            continue
        if not phone_raw:
            base["detail"] = "Missing phone number"
            stats["error"] += 1
            result_rows.append(base)
            continue

        try:
            dnis = ensure_e164(phone_raw)
            if not dnis:
                raise GenesysError(f"Could not format phone as E.164: {phone_raw}")

            user = find_user_by_email(client, email)
            uid = safe_str(user.get("id"))
            base["user_id"] = uid
            base["user_name"] = safe_str(user.get("name"))

            queue = find_by_name(all_queues, queue_name)
            if not queue:
                raise GenesysError(f"Queue not found: {queue_name}")
            queue_actions = _ensure_queue_joined(
                client, user_id=uid, queue=queue, apply=apply
            )
            actions.extend(queue_actions)
            for act in queue_actions:
                if "joined" in act or "added" in act or "would" in act:
                    if "ok" not in act:
                        stats["queue_fixes"] += 1

            station_actions, station_err = _ensure_station(
                client,
                user,
                apply=apply,
                phones_by_user=phones_by_user,
                phones_by_name=phones_by_name,
                stations_by_user=stations_by_user,
                telephony_cache=telephony_cache,
            )
            actions.extend(station_actions)
            if station_err and not station_actions:
                raise GenesysError(station_err)
            if any("created_web_phone" in a or "would ensure station" in a for a in station_actions):
                stats["station_fixes"] += 1

            center_queue_name = resolve_patient_services_center_queue(queue_name, all_queues)
            base["center_queue"] = center_queue_name

            existing = find_row_by_dnis(datatable_rows, dnis, dnis_field=dnis_field)
            if existing:
                base["datatable_row"] = "exists"
                actions.append(f"datatable row exists for {dnis}")
                stats["datatable_exists"] += 1
            else:
                row_body = build_call_manager_row_body(
                    schema,
                    dnis_e164=dnis,
                    queue_name=queue_name,
                    center_queue_name=center_queue_name,
                    skill="NCM",
                    open_value=True,
                )
                note = create_datatable_row(client, table_id, row_body, apply=apply)
                actions.append(f"{note} DNIS={dnis}")
                if apply:
                    datatable_rows.append(row_body)
                    stats["datatable_created"] += 1
                    base["datatable_row"] = "created"
                else:
                    base["datatable_row"] = "would_create"

            base["status"] = "ok"
            base["actions"] = "; ".join(actions)
            stats["ok"] += 1
            result_rows.append(base)
        except GenesysError as exc:
            base["detail"] = str(exc)
            base["actions"] = "; ".join(actions)
            stats["error"] += 1
            result_rows.append(base)

    return {
        "mode": "ncm_call_manager_sync",
        "apply": apply,
        "datatable_name": datatable_name,
        "datatable_id": table_id,
        "input_count": len(roster_rows),
        "stats": stats,
        "rows": result_rows,
    }
