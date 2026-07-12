"""In-memory demo state for agent workspace socialization builds."""
from __future__ import annotations

import random
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

DEMO_QUEUES = [
    {
        "id": "q-stl-ferguson",
        "name": "Patient Services - STL Ferguson",
        "service_level_target_sec": 180,
    },
    {
        "id": "q-jax-normandy",
        "name": "Patient Services - JAX Normandy",
        "service_level_target_sec": 180,
    },
    {
        "id": "q-bhm-roebuck",
        "name": "Patient Services - BHM Roebuck",
        "service_level_target_sec": 180,
    },
    {
        "id": "q-member-services",
        "name": "Member Services",
        "service_level_target_sec": 180,
    },
]

WRAPUP_CODES = [
    {"id": "resolved", "label": "Resolved"},
    {"id": "callback", "label": "Callback scheduled"},
    {"id": "voicemail", "label": "Left voicemail"},
    {"id": "wrong-number", "label": "Wrong number"},
    {"id": "transfer", "label": "Transferred"},
]

MEMBER_SEEDS = [
    ("Maria Chen", "on_queue"),
    ("James Okonkwo", "on_call"),
    ("Priya Patel", "on_queue"),
    ("Alex Rivera", "off_queue"),
    ("Jordan Kim", "away"),
    ("Taylor Brooks", "on_call"),
    ("Casey Nguyen", "on_queue"),
    ("Riley Adams", "off_queue"),
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentWorkspaceDemo:
    def __init__(self) -> None:
        self.agent_name = "Demo Agent"
        self.on_queue = False
        self.selected_queue_id = DEMO_QUEUES[0]["id"]
        self.media = {
            "mic": "Default — Headset Microphone",
            "speaker": "Default — Headset Earphone",
            "webrtc_ready": True,
        }
        self.current_call: dict[str, Any] | None = None
        self.chat_by_queue: dict[str, list[dict[str, Any]]] = {
            queue["id"]: [
                {
                    "id": str(uuid.uuid4()),
                    "author": "Supervisor",
                    "text": "Welcome — use chat for queue help only (no PHI).",
                    "at": _utc_now().isoformat(),
                }
            ]
            for queue in DEMO_QUEUES
        }
        self.stats_updated_at = _utc_now().isoformat()
        self.agent_stats = {
            "handled_today": 18,
            "avg_handle": "4:06",
            "avg_talk": "2:58",
            "avg_acw": "0:42",
            "callbacks_due": 2,
        }
        self._member_state: dict[str, list[dict[str, str]]] = {}
        for queue_index, queue in enumerate(DEMO_QUEUES):
            members = []
            for index, (name, status) in enumerate(MEMBER_SEEDS):
                if index % 2 == queue_index % 2:
                    members.append({"name": name, "status": status})
            if len(members) < 4:
                members = [
                    {"name": name, "status": status}
                    for name, status in MEMBER_SEEDS[:5]
                ]
            self._member_state[queue["id"]] = members
        self._activity_state: dict[str, dict[str, Any]] = {}
        for queue in DEMO_QUEUES:
            self._activity_state[queue["id"]] = {
                "calls_in_progress": random.randint(2, 8),
                "service_level_pct": round(random.uniform(72, 92), 1),
                "asa_sec": random.randint(22, 58),
            }

    def queue_by_id(self, queue_id: str) -> dict[str, Any] | None:
        for queue in DEMO_QUEUES:
            if queue["id"] == queue_id:
                return queue
        return None

    def jitter_realtime(self, queue_id: str) -> None:
        activity = self._activity_state[queue_id]
        activity["calls_in_progress"] = max(
            0,
            activity["calls_in_progress"] + random.choice([-1, 0, 0, 1]),
        )
        activity["asa_sec"] = max(8, activity["asa_sec"] + random.choice([-3, -1, 0, 2, 4]))
        activity["service_level_pct"] = round(
            min(99.0, max(55.0, activity["service_level_pct"] + random.uniform(-1.5, 1.5))),
            1,
        )
        for member in self._member_state[queue_id]:
            if random.random() < 0.08:
                if member["status"] == "on_call" and random.random() < 0.4:
                    member["status"] = "on_queue"
                elif member["status"] == "on_queue" and random.random() < 0.35:
                    member["status"] = "on_call"
                elif member["status"] == "off_queue" and random.random() < 0.2:
                    member["status"] = "on_queue"

    def snapshot(self, *, jitter: bool = True) -> dict[str, Any]:
        queue_id = self.selected_queue_id
        if jitter:
            self.jitter_realtime(queue_id)
        queue = self.queue_by_id(queue_id) or DEMO_QUEUES[0]
        activity = deepcopy(self._activity_state[queue_id])
        activity["service_level_target_sec"] = queue["service_level_target_sec"]
        return {
            "demo_mode": True,
            "agent_name": self.agent_name,
            "on_queue": self.on_queue,
            "selected_queue_id": queue_id,
            "selected_queue_name": queue["name"],
            "queues": DEMO_QUEUES,
            "media": self.media,
            "current_call": deepcopy(self.current_call),
            "wrapup_codes": WRAPUP_CODES,
            "membership": deepcopy(self._member_state[queue_id]),
            "activity": activity,
            "chat": deepcopy(self.chat_by_queue.get(queue_id, [])),
            "agent_stats": deepcopy(self.agent_stats),
            "stats_updated_at": self.stats_updated_at,
            "dial_requires_queue": True,
            "updated_at": _utc_now().isoformat(),
        }

    def set_queue(self, queue_id: str) -> None:
        if self.queue_by_id(queue_id):
            self.selected_queue_id = queue_id

    def toggle_queue(self, on_queue: bool) -> None:
        self.on_queue = on_queue

    def post_chat(self, queue_id: str, author: str, text: str) -> dict[str, Any]:
        message = {
            "id": str(uuid.uuid4()),
            "author": author.strip() or self.agent_name,
            "text": text.strip(),
            "at": _utc_now().isoformat(),
        }
        self.chat_by_queue.setdefault(queue_id, []).append(message)
        return message

    def start_inbound_call(self) -> dict[str, Any] | None:
        if self.current_call or not self.on_queue:
            return None
        queue = self.queue_by_id(self.selected_queue_id) or DEMO_QUEUES[0]
        self.current_call = {
            "direction": "inbound",
            "caller_name": "Memphis TN",
            "caller_number": "+1 (901) 497-5151",
            "queue_name": queue["name"],
            "started_at": _utc_now().isoformat(),
            "connected": False,
        }
        return self.current_call

    def answer_call(self) -> dict[str, Any] | None:
        if not self.current_call:
            return None
        self.current_call["connected"] = True
        self.current_call["answered_at"] = _utc_now().isoformat()
        return self.current_call

    def dial_on_behalf(self, phone_number: str) -> dict[str, Any] | None:
        if not self.on_queue:
            return None
        queue = self.queue_by_id(self.selected_queue_id)
        if not queue:
            return None
        self.current_call = {
            "direction": "outbound",
            "caller_name": "Outbound",
            "caller_number": phone_number.strip(),
            "queue_name": queue["name"],
            "on_behalf_of_queue": queue["name"],
            "started_at": _utc_now().isoformat(),
            "connected": True,
            "answered_at": _utc_now().isoformat(),
        }
        self._activity_state[self.selected_queue_id]["calls_in_progress"] += 1
        return self.current_call

    def end_call(self, wrapup_code_id: str) -> None:
        if self.current_call and self.current_call.get("direction") == "outbound":
            self._activity_state[self.selected_queue_id]["calls_in_progress"] = max(
                0,
                self._activity_state[self.selected_queue_id]["calls_in_progress"] - 1,
            )
        self.current_call = None
        self.agent_stats["handled_today"] += 1

    def refresh_stats(self) -> None:
        self.agent_stats["handled_today"] += random.randint(0, 2)
        self.agent_stats["avg_handle"] = random.choice(["3:58", "4:02", "4:06", "4:11"])
        self.agent_stats["avg_talk"] = random.choice(["2:44", "2:51", "2:58", "3:05"])
        self.agent_stats["avg_acw"] = random.choice(["0:36", "0:42", "0:48"])
        self.agent_stats["callbacks_due"] = random.randint(0, 4)
        self.stats_updated_at = _utc_now().isoformat()


demo_state = AgentWorkspaceDemo()
