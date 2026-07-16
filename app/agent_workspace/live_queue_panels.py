"""Live queue panels: waiting/active calls, membership, activity metrics."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from app.agent_workspace.live_api import LiveQueueError, UserApiClient


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _user_name(row: dict[str, Any]) -> str:
    user = row.get("user") if isinstance(row.get("user"), dict) else {}
    return _safe_str(user.get("name") or row.get("name"))


def _user_id(row: dict[str, Any]) -> str:
    user = row.get("user") if isinstance(row.get("user"), dict) else {}
    return _safe_str(user.get("id") or row.get("id"))


def _observation_filter(queue_id: str) -> dict[str, Any]:
    return {
        "type": "and",
        "predicates": [
            {
                "dimension": "queueId",
                "operator": "matches",
                "value": queue_id,
            }
        ],
    }


def _observation_metric_stats(
    payload: dict[str, Any] | None,
    metric: str,
    *,
    media_type: str | None = "voice",
) -> dict[str, Any] | None:
    for result in (payload or {}).get("results") or []:
        group = result.get("group") if isinstance(result.get("group"), dict) else {}
        if media_type and _safe_str(group.get("mediaType")).lower() not in ("", media_type.lower()):
            if _safe_str(group.get("mediaType")):
                continue
        for row in result.get("data") or []:
            if _safe_str(row.get("metric")) != metric:
                continue
            stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
            return stats
    return None


def _observation_count(
    payload: dict[str, Any] | None,
    metric: str,
    *,
    media_type: str | None = "voice",
) -> int | None:
    stats = _observation_metric_stats(payload, metric, media_type=media_type)
    if not stats:
        return None
    if "count" in stats:
        return int(stats.get("count") or 0)
    if "sum" in stats:
        return int(stats.get("sum") or 0)
    return None


def _observation_ratio(
    payload: dict[str, Any] | None,
    metric: str,
    *,
    media_type: str | None = "voice",
) -> float | None:
    stats = _observation_metric_stats(payload, metric, media_type=media_type)
    if not stats:
        return None
    if "ratio" in stats and stats.get("ratio") is not None:
        return round(float(stats["ratio"]) * 100, 1)
    count = float(stats.get("count") or 0)
    total = float(stats.get("total") or 0)
    if total > 0:
        return round((count / total) * 100, 1)
    return None


def fetch_queue_observations(client: UserApiClient, queue_id: str) -> dict[str, Any] | None:
    body = {
        "filter": _observation_filter(queue_id),
        "metrics": [
            "oWaiting",
            "oInteracting",
            "oServiceLevel",
            "oOnQueueUsers",
        ],
    }
    try:
        return client.request(
            "POST",
            "/api/v2/analytics/queues/observations/query",
            json_body=body,
        )
    except LiveQueueError:
        return None


def _activity_query(
    client: UserApiClient,
    queue_id: str,
    metric: str,
) -> dict[str, Any] | None:
    body = {
        "order": "asc",
        "filter": {
            "type": "or",
            "predicates": [
                {
                    "type": "dimension",
                    "dimension": "queueId",
                    "value": queue_id,
                }
            ],
        },
        "metrics": [{"metric": metric, "details": True}],
    }
    try:
        return client.request(
            "POST",
            "/api/v2/analytics/conversations/activity/query",
            json_body=body,
        )
    except LiveQueueError:
        return None


def _iter_activity_details(payload: dict[str, Any] | None, metric: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in (payload or {}).get("results") or []:
        for item in result.get("data") or []:
            if _safe_str(item.get("metric")) != metric:
                continue
            details = item.get("details")
            if isinstance(details, list):
                rows.extend(d for d in details if isinstance(d, dict))
            elif isinstance(details, dict):
                for key in ("entities", "conversations", "items", "results"):
                    nested = details.get(key)
                    if isinstance(nested, list):
                        rows.extend(d for d in nested if isinstance(d, dict))
    return rows


def _wait_seconds(detail: dict[str, Any], now: datetime) -> int:
    for key in ("waitTime", "waitTimeMs", "tWait"):
        if detail.get(key) is not None:
            raw = float(detail[key])
            if raw > 500:
                return max(0, int(raw / 1000))
            return max(0, int(raw))
    for key in ("conversationStart", "startTime", "enteredQueueTime"):
        start = _parse_ts(_safe_str(detail.get(key)))
        if start:
            return max(0, int((now - start).total_seconds()))
    return 0


def _duration_seconds(detail: dict[str, Any], now: datetime) -> int:
    for key in ("interactingTime", "talkTime", "duration", "durationSec"):
        if detail.get(key) is not None:
            raw = float(detail[key])
            if raw > 500:
                return max(0, int(raw / 1000))
            return max(0, int(raw))
    start = _parse_ts(_safe_str(detail.get("conversationStart") or detail.get("startTime")))
    if start:
        return max(0, int((now - start).total_seconds()))
    return 0


def _caller_number(detail: dict[str, Any]) -> str:
    for key in ("ani", "callerNumber", "phoneNumber", "dnis", "address"):
        value = _safe_str(detail.get(key))
        if value:
            return value
    customer = detail.get("customer") if isinstance(detail.get("customer"), dict) else {}
    for key in ("ani", "number", "phoneNumber"):
        value = _safe_str(customer.get(key))
        if value:
            return value
    return ""


def _caller_name(detail: dict[str, Any], number: str) -> str:
    for key in ("callerName", "name", "customerName"):
        value = _safe_str(detail.get(key))
        if value:
            return value
    customer = detail.get("customer") if isinstance(detail.get("customer"), dict) else {}
    value = _safe_str(customer.get("name"))
    if value:
        return value
    if number:
        return number
    return "Caller"


def _agent_name(detail: dict[str, Any]) -> str:
    for key in ("agentName", "userName"):
        value = _safe_str(detail.get(key))
        if value:
            return value
    agent = detail.get("agent") if isinstance(detail.get("agent"), dict) else {}
    value = _safe_str(agent.get("name"))
    if value:
        return value
    user = detail.get("user") if isinstance(detail.get("user"), dict) else {}
    return _safe_str(user.get("name")) or "Agent"


def _conversation_id(detail: dict[str, Any]) -> str:
    return _safe_str(detail.get("conversationId") or detail.get("id"))


def _is_callback(detail: dict[str, Any]) -> bool:
    media = _safe_str(detail.get("mediaType") or detail.get("media")).lower()
    if media == "callback":
        return True
    purpose = _safe_str(detail.get("purpose")).lower()
    return purpose == "callback"


def parse_waiting_calls(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    now = _utc_now()
    calls: list[dict[str, Any]] = []
    for detail in _iter_activity_details(payload, "oWaiting"):
        number = _caller_number(detail)
        calls.append({
            "id": _conversation_id(detail) or f"waiting-{len(calls) + 1}",
            "caller_name": _caller_name(detail, number),
            "caller_number": number,
            "wait_sec": _wait_seconds(detail, now),
            "is_callback": _is_callback(detail),
        })
    calls.sort(key=lambda item: item["wait_sec"], reverse=True)
    return calls


def parse_active_calls(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    now = _utc_now()
    calls: list[dict[str, Any]] = []
    for detail in _iter_activity_details(payload, "oInteracting"):
        number = _caller_number(detail)
        calls.append({
            "id": _conversation_id(detail) or f"active-{len(calls) + 1}",
            "agent_name": _agent_name(detail),
            "caller_name": _caller_name(detail, number),
            "caller_number": number,
            "duration_sec": _duration_seconds(detail, now),
            "direction": _safe_str(detail.get("direction")).lower() or "inbound",
        })
    calls.sort(key=lambda item: item["duration_sec"], reverse=True)
    return calls


def _details_query_waiting(client: UserApiClient, queue_id: str) -> list[dict[str, Any]]:
    """Fallback when activity API is unavailable."""
    now = _utc_now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    interval = start.strftime("%Y-%m-%dT%H:%M:%S.000Z") + "/" + now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body = {
        "interval": interval,
        "order": "asc",
        "orderBy": "conversationStart",
        "paging": {"pageSize": 50, "pageNumber": 1},
        "conversationFilters": [
            {
                "type": "and",
                "predicates": [
                    {
                        "type": "dimension",
                        "dimension": "conversationEnd",
                        "operator": "notExists",
                    }
                ],
            }
        ],
        "segmentFilters": [
            {
                "type": "and",
                "predicates": [
                    {
                        "type": "dimension",
                        "dimension": "queueId",
                        "operator": "matches",
                        "value": queue_id,
                    },
                    {
                        "type": "dimension",
                        "dimension": "purpose",
                        "operator": "matches",
                        "value": "acd",
                    },
                ],
            }
        ],
    }
    try:
        payload = client.request(
            "POST",
            "/api/v2/analytics/conversations/details/query",
            json_body=body,
        )
    except LiveQueueError:
        return []

    calls: list[dict[str, Any]] = []
    for conv in (payload or {}).get("conversations") or []:
        if not isinstance(conv, dict):
            continue
        conv_id = _safe_str(conv.get("conversationId"))
        number = ""
        is_callback = False
        wait_sec = 0
        start_ts = _parse_ts(_safe_str(conv.get("conversationStart")))
        for participant in conv.get("participants") or []:
            if _safe_str(participant.get("purpose")).lower() == "customer":
                for session in participant.get("sessions") or []:
                    number = number or _safe_str(session.get("ani"))
                    media = _safe_str(session.get("mediaType")).lower()
                    if media == "callback":
                        is_callback = True
                    for segment in session.get("segments") or []:
                        if _safe_str(segment.get("queueId")) != queue_id:
                            continue
                        seg_start = _parse_ts(_safe_str(segment.get("segmentStart"))) or start_ts
                        if seg_start:
                            wait_sec = max(wait_sec, int((now - seg_start).total_seconds()))
        if not conv_id:
            continue
        calls.append({
            "id": conv_id,
            "caller_name": number or "Caller",
            "caller_number": number,
            "wait_sec": wait_sec,
            "is_callback": is_callback,
        })
    calls.sort(key=lambda item: item["wait_sec"], reverse=True)
    return calls


def _details_query_active(client: UserApiClient, queue_id: str) -> list[dict[str, Any]]:
    now = _utc_now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    interval = start.strftime("%Y-%m-%dT%H:%M:%S.000Z") + "/" + now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body = {
        "interval": interval,
        "order": "desc",
        "orderBy": "conversationStart",
        "paging": {"pageSize": 50, "pageNumber": 1},
        "conversationFilters": [
            {
                "type": "and",
                "predicates": [
                    {
                        "type": "dimension",
                        "dimension": "conversationEnd",
                        "operator": "notExists",
                    }
                ],
            }
        ],
        "segmentFilters": [
            {
                "type": "and",
                "predicates": [
                    {
                        "type": "dimension",
                        "dimension": "queueId",
                        "operator": "matches",
                        "value": queue_id,
                    },
                    {
                        "type": "dimension",
                        "dimension": "purpose",
                        "operator": "matches",
                        "value": "agent",
                    },
                ],
            }
        ],
    }
    try:
        payload = client.request(
            "POST",
            "/api/v2/analytics/conversations/details/query",
            json_body=body,
        )
    except LiveQueueError:
        return []

    calls: list[dict[str, Any]] = []
    for conv in (payload or {}).get("conversations") or []:
        if not isinstance(conv, dict):
            continue
        conv_id = _safe_str(conv.get("conversationId"))
        agent_name = ""
        number = ""
        duration_sec = 0
        direction = "inbound"
        start_ts = _parse_ts(_safe_str(conv.get("conversationStart")))
        for participant in conv.get("participants") or []:
            purpose = _safe_str(participant.get("purpose")).lower()
            if purpose == "agent":
                agent_name = _safe_str(participant.get("name")) or agent_name
            for session in participant.get("sessions") or []:
                if purpose == "customer":
                    number = number or _safe_str(session.get("ani"))
                direction = direction or _safe_str(session.get("direction")).lower() or "inbound"
                for segment in session.get("segments") or []:
                    if _safe_str(segment.get("queueId")) != queue_id:
                        continue
                    seg_start = _parse_ts(_safe_str(segment.get("segmentStart"))) or start_ts
                    if seg_start:
                        duration_sec = max(duration_sec, int((now - seg_start).total_seconds()))
        if not conv_id:
            continue
        calls.append({
            "id": conv_id,
            "agent_name": agent_name or "Agent",
            "caller_name": number or "Caller",
            "caller_number": number,
            "duration_sec": duration_sec,
            "direction": direction or "inbound",
        })
    return calls


def _routing_status(client: UserApiClient, user_id: str) -> dict[str, Any] | None:
    try:
        return client.request("GET", f"/api/v2/users/{user_id}/routingstatus")
    except LiveQueueError:
        return None


def _presence(client: UserApiClient, user_id: str) -> dict[str, Any] | None:
    try:
        return client.request("GET", f"/api/v2/users/{user_id}/presences/purecloud")
    except LiveQueueError:
        return None


def _member_ui_status(
    *,
    joined: bool,
    routing: dict[str, Any] | None,
    presence: dict[str, Any] | None,
) -> str:
    presence_def = presence.get("presenceDefinition") if isinstance(presence, dict) else {}
    if not isinstance(presence_def, dict):
        presence_def = {}
    system_presence = _safe_str(presence_def.get("systemPresence")).upper()
    if system_presence in {"AWAY", "ON_BREAK", "MEAL", "TRAINING", "BUSY", "DND"}:
        return "away"

    routing_status = _safe_str((routing or {}).get("status")).upper()
    if routing_status in {"INTERACTING", "COMMUNICATING"}:
        return "on_call"
    if joined:
        return "on_queue"
    return "off_queue"


def fetch_queue_membership(client: UserApiClient, queue_id: str) -> list[dict[str, Any]] | None:
    try:
        members = client.paged_get(f"/api/v2/routing/queues/{queue_id}/members")
    except LiveQueueError:
        return None

    user_rows: list[dict[str, Any]] = []
    for member in members:
        uid = _user_id(member)
        if not uid:
            continue
        user_rows.append({
            "uid": uid,
            "name": _user_name(member) or uid,
            "joined": bool(member.get("joined")),
        })

    if not user_rows:
        return []

    routing_by_user: dict[str, dict[str, Any] | None] = {}
    presence_by_user: dict[str, dict[str, Any] | None] = {}
    max_workers = min(12, len(user_rows))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for row in user_rows:
            futures[pool.submit(_routing_status, client, row["uid"])] = ("routing", row["uid"])
            futures[pool.submit(_presence, client, row["uid"])] = ("presence", row["uid"])
        for future in as_completed(futures):
            kind, uid = futures[future]
            result = future.result()
            if kind == "routing":
                routing_by_user[uid] = result
            else:
                presence_by_user[uid] = result

    membership: list[dict[str, Any]] = []
    for row in user_rows:
        membership.append({
            "name": row["name"],
            "status": _member_ui_status(
                joined=row["joined"],
                routing=routing_by_user.get(row["uid"]),
                presence=presence_by_user.get(row["uid"]),
            ),
        })
    membership.sort(key=lambda item: (item["status"], item["name"].lower()))
    return membership


def _estimated_asa_sec(client: UserApiClient, queue_id: str) -> int | None:
    try:
        payload = client.request(
            "GET",
            f"/api/v2/routing/queues/{queue_id}/mediatypes/voice/estimatedwaittime",
        )
    except LiveQueueError:
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("estimatedWaitTimeSeconds", "estimatedWaitTimeSec", "waitTimeSeconds"):
        if payload.get(key) is not None:
            return max(0, int(payload[key]))
    return None


def fetch_queue_panel_data(
    client: UserApiClient,
    queue_id: str,
    *,
    service_level_target_sec: int = 180,
) -> dict[str, Any]:
    errors: list[str] = []

    observations = fetch_queue_observations(client, queue_id)

    waiting_payload = _activity_query(client, queue_id, "oWaiting")
    active_payload = _activity_query(client, queue_id, "oInteracting")

    if waiting_payload is None:
        errors.append("waiting")
        calls_waiting = _details_query_waiting(client, queue_id)
    else:
        calls_waiting = parse_waiting_calls(waiting_payload)

    if active_payload is None:
        errors.append("active")
        calls_active = _details_query_active(client, queue_id)
    else:
        calls_active = parse_active_calls(active_payload)

    membership_result = fetch_queue_membership(client, queue_id)
    if membership_result is None:
        errors.append("membership")
        membership = []
    else:
        membership = membership_result

    waiting_count = _observation_count(observations, "oWaiting")
    interacting_count = _observation_count(observations, "oInteracting")
    service_level_pct = _observation_ratio(observations, "oServiceLevel")
    asa_sec = _estimated_asa_sec(client, queue_id)

    if observations is None:
        errors.append("activity")

    activity = {
        "calls_in_progress": interacting_count
        if interacting_count is not None
        else len(calls_active),
        "service_level_pct": service_level_pct,
        "asa_sec": asa_sec,
        "service_level_target_sec": service_level_target_sec,
        "waiting_count": waiting_count if waiting_count is not None else len(calls_waiting),
    }

    panels_error = None
    if len(errors) >= 3:
        panels_error = "Live queue data unavailable — check Genesys permissions for this agent."

    return {
        "calls_waiting": calls_waiting,
        "calls_active": calls_active,
        "membership": membership,
        "activity": activity,
        "panels_error": panels_error or None,
    }
