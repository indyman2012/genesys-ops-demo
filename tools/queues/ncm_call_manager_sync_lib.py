"""NCM roster sync: queue membership, station, Care Managers Lookup data table."""
from __future__ import annotations

import re
import time
from io import BytesIO
from typing import Any, BinaryIO

from openpyxl import Workbook, load_workbook

from core.datatables import (
    build_call_manager_row_body,
    create_datatable_row,
    datatable_name_from_env,
    datatable_row_by_key,
    find_datatable_by_name,
    load_datatable_with_schema,
    schema_property_key,
)
from core.genesys_client import GenesysClient, GenesysError, ensure_e164, find_by_name, safe_str
from core.lookups import find_users_by_email_search
from tools.telephony.reassign_hq_phones_by_active_queue import DEFAULT_HQ_SITE, queue_parts
from tools.telephony.web_station_helpers import (
    create_web_phone,
    get_phone_base_settings,
    resolve_site_by_name,
)

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ROSTER_TEMPLATE_HEADERS = ("Email Address", "Queue Name", "Phone Number")
ROSTER_TEMPLATE_FILENAME = "ncm_call_manager_roster_template.xlsx"


def build_roster_template_xlsx() -> bytes:
    """Three-column roster template with header row and one example row."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Roster"
    ws.append(list(ROSTER_TEMPLATE_HEADERS))
    ws.append(["agent@example.com", "NCM_BHM_Roebuck", "2055551212"])
    for col, width in zip("ABC", (36, 32, 18)):
        ws.column_dimensions[col].width = width
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()

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


def _pick_user(matches: list[dict[str, Any]]) -> dict[str, Any]:
    active = [u for u in matches if safe_str(u.get("state")).upper() == "ACTIVE"]
    if len(active) == 1:
        return active[0]
    if len(matches) == 1:
        return matches[0]
    raise GenesysError(f"Multiple users matched: {[u.get('name') for u in matches]}")


def prefetch_users_by_email(client: GenesysClient, emails: set[str]) -> dict[str, dict[str, Any]]:
    """Search API only — never scans the full user directory."""
    out: dict[str, dict[str, Any]] = {}
    for email in sorted(emails):
        matches = find_users_by_email_search(client, email)
        if matches:
            out[email] = _pick_user(matches)
    return out


def build_queue_name_index(queues: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for queue in queues:
        name = safe_str(queue.get("name"))
        if name:
            index[name.casefold()] = queue
    return index


def _load_user_queues(client: GenesysClient, user_id: str, cache: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    if user_id not in cache:
        cache[user_id] = client.paged_get(f"/api/v2/users/{user_id}/queues")
    return cache[user_id]


def _queue_membership(user_queues: list[dict[str, Any]], queue_id: str) -> dict[str, Any] | None:
    for row in user_queues:
        if safe_str(row.get("id")) == queue_id:
            return row
    return None


def _ensure_queue_joined(
    client: GenesysClient,
    *,
    user_id: str,
    queue: dict[str, Any],
    user_queues: list[dict[str, Any]],
    apply: bool,
) -> list[str]:
    queue_id = safe_str(queue.get("id"))
    queue_name = safe_str(queue.get("name"))
    actions: list[str] = []
    member = _queue_membership(user_queues, queue_id)
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
            member["joined"] = True
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
        user_queues.append({"id": queue_id, "name": queue_name, "joined": True})
        actions.append(f"added member + joined=true on {queue_name}")
    else:
        actions.append(f"would add to {queue_name} with joined=true")
    return actions


def _index_stations_by_user(client: GenesysClient) -> dict[str, dict]:
    by_user: dict[str, dict] = {}
    for station in client.paged_get("/api/v2/stations", params={"pageSize": 100}):
        if safe_str(station.get("status")).upper() != "ASSOCIATED":
            continue
        uid = safe_str(station.get("userId"))
        if uid:
            by_user[uid] = station
    return by_user


def _user_has_web_phone(client: GenesysClient, user_id: str) -> bool:
    payload = client.request(
        "GET",
        "/api/v2/telephony/providers/edges/phones",
        params={"webRtcUser.id": user_id, "pageSize": 1, "pageNumber": 1},
    )
    return bool((payload or {}).get("entities"))


def _user_has_station(
    client: GenesysClient,
    user_id: str,
    *,
    stations_by_user: dict[str, dict],
    web_phone_cache: dict[str, bool],
) -> tuple[bool, str]:
    if user_id in stations_by_user:
        st = stations_by_user[user_id]
        return True, safe_str(st.get("name")) or "station"
    if user_id not in web_phone_cache:
        web_phone_cache[user_id] = _user_has_web_phone(client, user_id)
    if web_phone_cache[user_id]:
        return True, "WebRTC phone"
    return False, ""


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


def _ensure_station(
    client: GenesysClient,
    user: dict[str, Any],
    *,
    apply: bool,
    stations_by_user: dict[str, dict],
    web_phone_cache: dict[str, bool],
    telephony_cache: dict[str, Any] | None,
) -> tuple[list[str], str]:
    uid = safe_str(user.get("id"))
    has, label = _user_has_station(
        client, uid, stations_by_user=stations_by_user, web_phone_cache=web_phone_cache
    )
    if has:
        return [f"station ok: {label}"], ""

    if not apply:
        return ["would ensure station (WebRTC phone at Headquarters if missing)"], ""

    if not telephony_cache or not telephony_cache.get("ready"):
        return [], (telephony_cache or {}).get("error") or "telephony prerequisites not loaded"

    _created, note = create_web_phone(
        client,
        user,
        telephony_cache["hq_site"],
        telephony_cache["phone_base"],
        telephony_cache["line_base"],
        apply=True,
    )
    if note.startswith("web_phone_error"):
        return [], note.replace("web_phone_error:", "", 1)
    web_phone_cache[uid] = True
    return [note], ""


def _dnis_exists(
    client: GenesysClient,
    table_id: str,
    dnis: str,
    cache: dict[str, bool],
) -> bool:
    if dnis in cache:
        return cache[dnis]
    exists = datatable_row_by_key(client, table_id, dnis) is not None
    cache[dnis] = exists
    return exists


def run_ncm_call_manager_sync(
    client: GenesysClient,
    roster_rows: list[dict[str, str]],
    *,
    apply: bool,
    datatable_name: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    datatable_name = datatable_name or datatable_name_from_env()
    table_meta = find_datatable_by_name(client, datatable_name)
    table_id = safe_str(table_meta.get("id"))
    table_doc = load_datatable_with_schema(client, table_id)
    schema = table_doc.get("schema") or {}

    all_queues = client.paged_get("/api/v2/routing/queues")
    queue_index = build_queue_name_index(all_queues)
    center_queue_cache: dict[str, str] = {}

    valid_emails = {
        normalize_email(item.get("email", ""))
        for item in roster_rows
        if normalize_email(item.get("email", "")) and EMAIL_PATTERN.match(normalize_email(item.get("email", "")))
    }
    users_by_email = prefetch_users_by_email(client, valid_emails)
    user_queues_cache: dict[str, list[dict[str, Any]]] = {}
    stations_by_user = _index_stations_by_user(client)
    web_phone_cache: dict[str, bool] = {}
    dnis_cache: dict[str, bool] = {}
    telephony_cache: dict[str, Any] | None = None

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

            user = users_by_email.get(email)
            if not user:
                raise GenesysError(f"User not found: {email}")
            uid = safe_str(user.get("id"))
            base["user_id"] = uid
            base["user_name"] = safe_str(user.get("name"))

            queue = queue_index.get(queue_name.casefold())
            if not queue:
                raise GenesysError(f"Queue not found: {queue_name}")
            user_queues = _load_user_queues(client, uid, user_queues_cache)
            queue_actions = _ensure_queue_joined(
                client,
                user_id=uid,
                queue=queue,
                user_queues=user_queues,
                apply=apply,
            )
            actions.extend(queue_actions)
            for act in queue_actions:
                if "joined" in act or "added" in act or "would" in act:
                    if "ok" not in act:
                        stats["queue_fixes"] += 1

            if apply and not _user_has_station(
                client, uid, stations_by_user=stations_by_user, web_phone_cache=web_phone_cache
            )[0]:
                if telephony_cache is None:
                    telephony_cache = _load_telephony_cache(client)

            station_actions, station_err = _ensure_station(
                client,
                user,
                apply=apply,
                stations_by_user=stations_by_user,
                web_phone_cache=web_phone_cache,
                telephony_cache=telephony_cache,
            )
            actions.extend(station_actions)
            if station_err and not station_actions:
                raise GenesysError(station_err)
            if any("created_web_phone" in a or "would ensure station" in a for a in station_actions):
                stats["station_fixes"] += 1

            if queue_name not in center_queue_cache:
                center_queue_cache[queue_name] = resolve_patient_services_center_queue(queue_name, all_queues)
            center_queue_name = center_queue_cache[queue_name]
            base["center_queue"] = center_queue_name

            if _dnis_exists(client, table_id, dnis, dnis_cache):
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
                    dnis_cache[dnis] = True
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

    elapsed = round(time.monotonic() - started, 2)
    return {
        "mode": "ncm_call_manager_sync",
        "apply": apply,
        "datatable_name": datatable_name,
        "datatable_id": table_id,
        "input_count": len(roster_rows),
        "elapsed_seconds": elapsed,
        "stats": stats,
        "rows": result_rows,
    }
