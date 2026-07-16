"""Live Genesys queue data for authenticated agent workspace sessions."""
from __future__ import annotations

from typing import Any

from app.agent_workspace.demo_state import WRAPUP_CODES, _utc_now
from app.agent_workspace.live_api import LiveQueueError, UserApiClient
from app.agent_workspace.live_queue_panels import fetch_queue_panel_data
from core.config import env_first
def _queue_name_prefix() -> str:
    return (env_first("AGENT_QUEUE_NAME_PREFIX") or "").strip()


def _pilot_queue_allowlist() -> set[str]:
    raw = (env_first("AGENT_PILOT_QUEUE_IDS") or "").strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _normalize_queue(row: dict[str, Any]) -> dict[str, Any]:
    queue_id = str(row.get("id") or "").strip()
    name = str(row.get("name") or queue_id).strip()
    joined = bool(row.get("joined"))
    target_sec = 180
    if isinstance(row.get("serviceLevel"), dict):
        # Genesys may expose serviceLevel.target in some payloads
        inline = row.get("serviceLevel", {})
        if isinstance(inline.get("target"), (int, float)):
            target_sec = int(inline["target"])
    return {
        "id": queue_id,
        "name": name,
        "joined": joined,
        "service_level_target_sec": target_sec,
    }


def fetch_agent_queues(client: UserApiClient, user_id: str) -> list[dict[str, Any]]:
    raw = client.paged_get(f"/api/v2/users/{user_id}/queues")
    prefix = _queue_name_prefix().lower()
    allowlist = _pilot_queue_allowlist()
    queues = [_normalize_queue(row) for row in raw if row.get("id")]
    if allowlist:
        queues = [q for q in queues if q["id"] in allowlist]
    if prefix:
        queues = [q for q in queues if q["name"].lower().startswith(prefix)]
    queues.sort(key=lambda item: item["name"].lower())
    return queues


def _resolve_selected_queue(
    queues: list[dict[str, Any]],
    session: dict[str, Any],
    *,
    preferred_id: str | None = None,
) -> dict[str, Any] | None:
    if not queues:
        return None
    ids = {q["id"] for q in queues}
    for candidate in (preferred_id, session.get("agent_selected_queue_id")):
        if candidate and candidate in ids:
            return next(q for q in queues if q["id"] == candidate)
    return queues[0]


def set_selected_queue(session: dict[str, Any], queue_id: str, queues: list[dict[str, Any]]) -> None:
    if queue_id not in {q["id"] for q in queues}:
        raise LiveQueueError("Queue not in your membership list")
    session["agent_selected_queue_id"] = queue_id


def set_queue_joined(
    client: UserApiClient,
    *,
    user_id: str,
    queue_id: str,
    joined: bool,
) -> None:
    client.request(
        "PATCH",
        f"/api/v2/users/{user_id}/queues/{queue_id}",
        json_body={"joined": joined},
    )


def build_live_snapshot(session: dict[str, Any]) -> dict[str, Any]:
    access_token = session.get("genesys_access_token")
    user_id = session.get("genesys_user_id")
    if not access_token or not user_id:
        raise LiveQueueError("Missing Genesys session")

    client = UserApiClient(access_token)
    queues = fetch_agent_queues(client, user_id)
    selected = _resolve_selected_queue(queues, session)
    if selected and not session.get("agent_selected_queue_id"):
        session["agent_selected_queue_id"] = selected["id"]
    selected_id = selected["id"] if selected else ""
    selected_name = selected["name"] if selected else ""
    on_queue = bool(selected and selected.get("joined"))
    sl_target = selected.get("service_level_target_sec", 180) if selected else 180

    panels = {
        "calls_waiting": [],
        "calls_active": [],
        "membership": [],
        "activity": {
            "calls_in_progress": 0,
            "service_level_pct": None,
            "asa_sec": None,
            "service_level_target_sec": sl_target if selected else None,
            "waiting_count": 0,
        },
        "panels_error": None,
    }
    if selected_id:
        panels = fetch_queue_panel_data(
            client,
            selected_id,
            service_level_target_sec=sl_target,
        )

    return {
        "demo_mode": False,
        "authenticated": True,
        "auth_required": False,
        "live_queues": True,
        "agent_name": session.get("genesys_user_name") or "Agent",
        "on_queue": on_queue,
        "selected_queue_id": selected_id,
        "selected_queue_name": selected_name,
        "queues": queues,
        "media": {
            "mic": session.get("agent_media_mic") or "System default audio",
            "mic_id": session.get("agent_media_mic_id") or "default",
            "speaker": session.get("agent_media_speaker") or "System default audio",
            "speaker_id": session.get("agent_media_speaker_id") or "default",
            "webrtc_ready": False,
        },
        "current_call": None,
        "calls_waiting": panels["calls_waiting"],
        "calls_active": panels["calls_active"],
        "wrapup_codes": WRAPUP_CODES,
        "membership": panels["membership"],
        "activity": panels["activity"],
        "panels_error": panels.get("panels_error"),
        "chat": [],
        "agent_stats": {
            "handled_today": 0,
            "avg_handle": "—",
            "avg_talk": "—",
            "avg_acw": "—",
            "callbacks_due": 0,
        },
        "stats_updated_at": None,
        "dial_requires_queue": True,
        "dial_requires_on_queue": False,
        "nav": {"supervisor": False, "admin": False},
        "updated_at": _utc_now(),
    }


def apply_queue_selection(
    session: dict[str, Any],
    *,
    queue_id: str,
) -> dict[str, Any]:
    client = UserApiClient(session["genesys_access_token"])
    queues = fetch_agent_queues(client, session["genesys_user_id"])
    set_selected_queue(session, queue_id, queues)
    return build_live_snapshot(session)


def apply_on_queue(
    session: dict[str, Any],
    *,
    joined: bool,
) -> dict[str, Any]:
    access_token = session["genesys_access_token"]
    user_id = session["genesys_user_id"]
    client = UserApiClient(access_token)
    queues = fetch_agent_queues(client, user_id)
    selected = _resolve_selected_queue(queues, session)
    if not selected:
        raise LiveQueueError("No queue selected")
    set_queue_joined(
        client,
        user_id=user_id,
        queue_id=selected["id"],
        joined=joined,
    )
    return build_live_snapshot(session)
