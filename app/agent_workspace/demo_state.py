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

WAITING_CALL_SEEDS = [
    ("Memphis TN", "+1 (901) 497-5151"),
    ("Nashville TN", "+1 (615) 555-0142"),
    ("Birmingham AL", "+1 (205) 555-0198"),
    ("Jacksonville FL", "+1 (904) 555-0167"),
    ("St. Louis MO", "+1 (314) 555-0133"),
    ("Callback — M. Johnson", "+1 (901) 555-0188"),
    ("Little Rock AR", "+1 (501) 555-0174"),
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentWorkspaceDemo:
    def __init__(self) -> None:
        self.agent_name = "Demo Agent"
        self.on_queue = False
        self.selected_queue_id = DEMO_QUEUES[0]["id"]
        self.media = {
            "mic": "System default audio",
            "mic_id": "default",
            "speaker": "System default audio",
            "speaker_id": "default",
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
        self._waiting_state: dict[str, list[dict[str, Any]]] = {
            queue["id"]: self._seed_waiting_calls(queue["id"])
            for queue in DEMO_QUEUES
        }
        self._active_calls_state: dict[str, list[dict[str, Any]]] = {
            queue["id"]: self._seed_active_calls(queue["id"])
            for queue in DEMO_QUEUES
        }

    def _seed_active_calls(self, queue_id: str) -> list[dict[str, Any]]:
        members = [
            member["name"]
            for member in self._member_state[queue_id]
            if member["status"] == "on_call"
        ]
        if not members:
            members = [name for name, status in MEMBER_SEEDS if status == "on_call"][:2]
        calls = []
        for index, agent_name in enumerate(members):
            caller_name, caller_number = WAITING_CALL_SEEDS[index % len(WAITING_CALL_SEEDS)]
            calls.append({
                "id": str(uuid.uuid4()),
                "agent_name": agent_name,
                "caller_name": caller_name,
                "caller_number": caller_number,
                "duration_sec": random.randint(45, 420) + index * 18,
                "direction": "inbound",
            })
        return calls

    def _seed_waiting_calls(self, queue_id: str) -> list[dict[str, Any]]:
        count = 2 + (hash(queue_id) % 4)
        calls = []
        for index in range(count):
            name, number = WAITING_CALL_SEEDS[index % len(WAITING_CALL_SEEDS)]
            calls.append({
                "id": str(uuid.uuid4()),
                "caller_name": name,
                "caller_number": number,
                "wait_sec": random.randint(18, 220) + index * 12,
                "is_callback": name.startswith("Callback"),
            })
        return sorted(calls, key=lambda item: item["wait_sec"], reverse=True)

    def _new_waiting_call(self) -> dict[str, Any]:
        name, number = random.choice(WAITING_CALL_SEEDS)
        return {
            "id": str(uuid.uuid4()),
            "caller_name": name,
            "caller_number": number,
            "wait_sec": random.randint(4, 20),
            "is_callback": name.startswith("Callback"),
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
        waiting = self._waiting_state[queue_id]
        for call in waiting:
            call["wait_sec"] += random.randint(4, 9)
        if random.random() < 0.18 and len(waiting) < 8:
            waiting.append(self._new_waiting_call())
            waiting.sort(key=lambda item: item["wait_sec"], reverse=True)
        if random.random() < 0.12 and waiting:
            waiting.pop(random.randrange(len(waiting)))
        active = self._active_calls_state[queue_id]
        for call in active:
            call["duration_sec"] += random.randint(3, 8)
        if random.random() < 0.1 and len(active) < 6:
            on_call_members = [
                member["name"]
                for member in self._member_state[queue_id]
                if member["status"] == "on_call"
            ]
            if on_call_members:
                caller_name, caller_number = random.choice(WAITING_CALL_SEEDS)
                active.append({
                    "id": str(uuid.uuid4()),
                    "agent_name": random.choice(on_call_members),
                    "caller_name": caller_name,
                    "caller_number": caller_number,
                    "duration_sec": random.randint(12, 40),
                    "direction": "inbound",
                })
        if random.random() < 0.08 and active:
            active.pop(random.randrange(len(active)))

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
            "calls_waiting": deepcopy(self._waiting_state[queue_id]),
            "calls_active": deepcopy(self._active_calls_state[queue_id]),
            "wrapup_codes": WRAPUP_CODES,
            "membership": deepcopy(self._member_state[queue_id]),
            "activity": activity,
            "chat": deepcopy(self.chat_by_queue.get(queue_id, [])),
            "agent_stats": deepcopy(self.agent_stats),
            "stats_updated_at": self.stats_updated_at,
            "dial_requires_queue": True,
            "dial_requires_on_queue": False,
            "nav": {
                "supervisor": True,
                "admin": True,
            },
            "updated_at": _utc_now().isoformat(),
        }

    def set_queue(self, queue_id: str) -> None:
        if self.queue_by_id(queue_id):
            self.selected_queue_id = queue_id

    def toggle_queue(self, on_queue: bool) -> None:
        self.on_queue = on_queue

    def set_media(
        self,
        *,
        mic_id: str | None = None,
        mic_label: str | None = None,
        speaker_id: str | None = None,
        speaker_label: str | None = None,
    ) -> None:
        if mic_label is not None:
            self.media["mic"] = mic_label
            self.media["mic_id"] = mic_id or "default"
        if speaker_label is not None:
            self.media["speaker"] = speaker_label
            self.media["speaker_id"] = speaker_id or "default"

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
        waiting = self._waiting_state[self.selected_queue_id]
        if waiting:
            next_call = waiting.pop(0)
            caller_name = next_call["caller_name"]
            caller_number = next_call["caller_number"]
        else:
            caller_name = "Memphis TN"
            caller_number = "+1 (901) 497-5151"
        self.current_call = {
            "direction": "inbound",
            "caller_name": caller_name,
            "caller_number": caller_number,
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
        queue = self.queue_by_id(self.selected_queue_id)
        if not queue or self.current_call:
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
