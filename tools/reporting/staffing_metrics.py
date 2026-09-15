"""Shared staffing and agent burden metric fetchers for Genesys analytics."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.cli import tool_log

PHONE_METRICS = ("tHandle", "tHeld", "tAcw")
EXCLUDED_PRESENCE = frozenset(
    {"OFFLINE", "ON_BREAK", "MEAL", "TRAINING", "AWAY", "DND", "BUSY", "INVALID"}
)
USER_DETAILS_WORKERS = 10


@dataclass
class RoleExpectation:
    title_contains: str
    expected_phone_pct: float
    include_in_burden: bool


@dataclass
class AgentBurdenRow:
    user_id: str
    name: str
    title: str
    role_match: str
    logged_in_hours: float
    phone_controlled_hours: float
    qualifying_queue_hours: float
    expected_phone_hours: float
    phone_utilization_pct: float | None
    phone_allocation_util: float
    excess_burden_hours: float
    avg_staffing_burden: float
    abi: float
    queues_active: int
    include_in_burden: bool


def interval_key(interval_value: str) -> str:
    return (interval_value or "").split("/")[0]


def metric_stats(bucket: dict[str, Any], metric: str) -> tuple[int, int]:
    for row in bucket.get("metrics") or []:
        if (row.get("metric") or "").strip() != metric:
            continue
        stats = row.get("stats") or {}
        return int(stats.get("count") or 0), int(stats.get("sum") or 0)
    return 0, 0


def phone_ms_from_bucket(bucket: dict[str, Any]) -> int:
    total = 0
    for metric in PHONE_METRICS:
        _count, ms = metric_stats(bucket, metric)
        total += ms
    return total


def load_role_expectations(rows: list[dict[str, str]]) -> list[RoleExpectation]:
    expectations: list[RoleExpectation] = []
    for row in rows:
        needle = (row.get("title_contains") or "").strip()
        if not needle:
            continue
        expectations.append(
            RoleExpectation(
                title_contains=needle,
                expected_phone_pct=float(row.get("expected_phone_pct") or 20),
                include_in_burden=(row.get("include_in_burden") or "true").strip().lower()
                in {"1", "true", "yes", "y"},
            )
        )
    return expectations


def match_role(title: str, expectations: list[RoleExpectation]) -> RoleExpectation | None:
    low = (title or "").lower()
    for item in expectations:
        if item.title_contains.lower() in low:
            return item
    return None


def fetch_queue_user_interval_occupancy(
    client,
    interval: str,
    queue_ids: set[str],
) -> dict[tuple[str, str, str], int]:
    """
    Occupied phone milliseconds per (queue_id, interval_start, user_id).

    Uses conversation analytics because user aggregates do not support queueId
    filters in this org (tOnQueueTime per queue is unavailable).
    """
    body = {
        "interval": interval,
        "granularity": "PT30M",
        "groupBy": ["queueId", "userId"],
        "filter": {
            "type": "and",
            "predicates": [
                {"type": "dimension", "dimension": "direction", "operator": "matches", "value": "inbound"},
                {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "voice"},
            ],
        },
        "metrics": list(PHONE_METRICS),
    }
    tool_log("info", "Querying 30-minute inbound voice occupancy by queue and agent")
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)
    occupancy: dict[tuple[str, str, str], int] = defaultdict(int)

    for result in resp.get("results") or []:
        group = result.get("group") or {}
        queue_id = (group.get("queueId") or "").strip()
        user_id = (group.get("userId") or "").strip()
        if queue_id not in queue_ids or not user_id:
            continue
        for data_bucket in result.get("data") or []:
            key = interval_key(data_bucket.get("interval") or "")
            phone_ms = phone_ms_from_bucket(data_bucket)
            if phone_ms > 0:
                occupancy[(queue_id, key, user_id)] += phone_ms
    return occupancy


def queue_interval_actual_fte(
    occupancy: dict[tuple[str, str, str], int],
    interval_seconds: int,
) -> dict[tuple[str, str], float]:
    """Sum occupied agent-ms per queue-interval and convert to active phone FTE."""
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for (queue_id, interval_start, _user_id), phone_ms in occupancy.items():
        totals[(queue_id, interval_start)] += phone_ms
    if interval_seconds <= 0:
        return {}
    return {
        key: round(ms / (interval_seconds * 1000), 2)
        for key, ms in totals.items()
    }


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _segment_ms(segment: dict[str, Any]) -> int:
    start = _parse_utc(segment.get("startTime"))
    end = _parse_utc(segment.get("endTime"))
    if not start or not end or end <= start:
        return 0
    return int((end - start).total_seconds() * 1000)


def fetch_logged_in_hours(client, interval: str, user_ids: list[str]) -> dict[str, float]:
    """
    Logged-in hours proxy via tAgentRoutingStatus (ms in routing-capable states).

    Genesys user details presence segments ignore requested userIds in this org
    and always return the same user, so presence segments cannot be used per agent.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    hours: dict[str, float] = {}
    if not user_ids:
        return hours

    def fetch_one(user_id: str) -> tuple[str, float]:
        body = {
            "interval": interval,
            "granularity": "P1W",
            "groupBy": ["userId"],
            "filter": {
                "type": "and",
                "predicates": [
                    {
                        "type": "dimension",
                        "dimension": "userId",
                        "operator": "matches",
                        "value": user_id,
                    },
                ],
            },
            "metrics": ["tAgentRoutingStatus"],
        }
        try:
            resp = client.request("POST", "/api/v2/analytics/users/aggregates/query", json_body=body)
        except Exception:
            return user_id, 0.0
        routing_ms = 0
        for result in resp.get("results") or []:
            group = result.get("group") or {}
            if (group.get("userId") or "").strip() != user_id:
                continue
            for bucket in result.get("data") or []:
                _count, total_ms = metric_stats(bucket, "tAgentRoutingStatus")
                routing_ms += total_ms
        return user_id, routing_ms / 3_600_000 if routing_ms > 0 else 0.0

    tool_log("info", f"Fetching routing hours for {len(user_ids)} agents")
    completed = 0
    with ThreadPoolExecutor(max_workers=USER_DETAILS_WORKERS) as pool:
        futures = {pool.submit(fetch_one, uid): uid for uid in user_ids}
        for future in as_completed(futures):
            completed += 1
            if completed == 1 or completed % 50 == 0:
                tool_log("info", f"Routing hours progress {completed}/{len(user_ids)}")
            uid, logged = future.result()
            if logged > 0:
                hours[uid] = logged
    tool_log("info", f"Routing hours resolved for {len(hours)} of {len(user_ids)} agents")
    return hours


def load_user_directory(client, user_ids: set[str]) -> dict[str, dict[str, str]]:
    directory: dict[str, dict[str, str]] = {}
    for user in client.paged_get("/api/v2/users", params={"state": "active"}):
        uid = (user.get("id") or "").strip()
        if uid in user_ids:
            directory[uid] = {
                "name": (user.get("name") or uid).strip(),
                "title": (user.get("title") or "").strip(),
            }
    for uid in sorted(user_ids - set(directory)):
        try:
            user = client.request("GET", f"/api/v2/users/{uid}")
        except Exception:
            directory[uid] = {"name": uid, "title": ""}
            continue
        directory[uid] = {
            "name": (user.get("name") or uid).strip(),
            "title": (user.get("title") or "").strip(),
        }
    return directory


def build_agent_burden_rows(
    occupancy: dict[tuple[str, str, str], int],
    logged_in_hours: dict[str, float],
    user_directory: dict[str, dict[str, str]],
    role_expectations: list[RoleExpectation],
    *,
    interval_seconds: int = 1800,
) -> list[AgentBurdenRow]:
    phone_ms_by_user: dict[str, int] = defaultdict(int)
    queue_ms_by_user: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    interval_ms_by_user: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    interval_cap_ms = interval_seconds * 1000

    for (queue_id, interval_start, user_id), phone_ms in occupancy.items():
        phone_ms_by_user[user_id] += phone_ms
        queue_ms_by_user[user_id][queue_id] += phone_ms
        interval_ms_by_user[user_id][interval_start] += phone_ms

    clock_phone_ms_by_user: dict[str, int] = {}
    for user_id, intervals in interval_ms_by_user.items():
        clock_phone_ms_by_user[user_id] = sum(
            min(ms, interval_cap_ms) for ms in intervals.values()
        )

    rows: list[AgentBurdenRow] = []
    user_ids = set(phone_ms_by_user) | set(logged_in_hours)
    for user_id in sorted(user_ids):
        info = user_directory.get(user_id, {"name": user_id, "title": ""})
        title = info.get("title", "")
        role = match_role(title, role_expectations)
        role_label = role.title_contains if role else "(no role match)"
        include = role.include_in_burden if role else False
        expected_pct = (role.expected_phone_pct / 100.0) if role else 0.20

        phone_hours = clock_phone_ms_by_user.get(user_id, 0) / 3_600_000
        qualifying_queue_hours = sum(ms / 3_600_000 for ms in queue_ms_by_user[user_id].values())
        logged_hours = logged_in_hours.get(user_id) or 0.0

        expected_phone = logged_hours * expected_pct if logged_hours > 0 else 0.0
        phone_util = (phone_hours / logged_hours * 100) if logged_hours > 0 else None
        allocation_util = (phone_hours / expected_phone) if expected_phone > 0 else 0.0
        excess = phone_hours - expected_phone
        staffing_burden = (qualifying_queue_hours / logged_hours) if logged_hours > 0 else 0.0
        abi = allocation_util * staffing_burden if include else 0.0

        rows.append(
            AgentBurdenRow(
                user_id=user_id,
                name=info.get("name", user_id),
                title=title,
                role_match=role_label,
                logged_in_hours=round(logged_hours, 2),
                phone_controlled_hours=round(phone_hours, 2),
                qualifying_queue_hours=round(qualifying_queue_hours, 2),
                expected_phone_hours=round(expected_phone, 2),
                phone_utilization_pct=round(phone_util, 1) if phone_util is not None else None,
                phone_allocation_util=round(allocation_util, 2),
                excess_burden_hours=round(excess, 2),
                avg_staffing_burden=round(staffing_burden, 2),
                abi=round(abi, 2),
                queues_active=len(queue_ms_by_user[user_id]),
                include_in_burden=include,
            )
        )

    scored = [row for row in rows if row.include_in_burden and row.phone_controlled_hours > 0]
    scored.sort(key=lambda row: (-row.abi, -row.excess_burden_hours, row.name))
    unscored = [row for row in rows if not row.include_in_burden and row.phone_controlled_hours > 0]
    unscored.sort(key=lambda row: (-row.phone_controlled_hours, row.name))
    return scored + unscored
