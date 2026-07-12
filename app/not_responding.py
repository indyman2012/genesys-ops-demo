from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

from core.genesys_client import GenesysClient, safe_str

MIN_DURATION_SECONDS = 300
LOOKBACK_MINUTES = 20
MAX_ROUTING_WORKERS = 20


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_interval(start: datetime, end: datetime) -> str:
    return (
        start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        + "/"
        + end.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    )


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    text = safe_str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _user_info(client: GenesysClient, user_id: str) -> dict[str, str]:
    try:
        user = client.request("GET", f"/api/v2/users/{user_id}")
    except Exception:
        return {"name": user_id, "email": ""}
    return {
        "name": safe_str(user.get("name")) or user_id,
        "email": safe_str(user.get("email")),
    }


def _attach_user_info(client: GenesysClient, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with ThreadPoolExecutor(max_workers=MAX_ROUTING_WORKERS) as pool:
        futures = {pool.submit(_user_info, client, row["user_id"]): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            info = future.result()
            row["name"] = info["name"]
            row["email"] = info["email"]


def _analytics_user_ids(client: GenesysClient, *, filter_key: str, filter_body: dict[str, Any]) -> set[str]:
    now = _utc_now()
    start = now - timedelta(minutes=LOOKBACK_MINUTES)
    body = {
        "interval": _iso_interval(start, now),
        "order": "desc",
        filter_key: [filter_body],
    }
    payload = client.request("POST", "/api/v2/analytics/users/details/query", json_body=body)
    return {
        safe_str(item.get("userId"))
        for item in (payload.get("userDetails") or [])
        if safe_str(item.get("userId"))
    }


def _routing_status(client: GenesysClient, user_id: str) -> dict[str, Any] | None:
    try:
        return client.request("GET", f"/api/v2/users/{user_id}/routingstatus")
    except Exception:
        return None


def fetch_not_responding_agents(
    client: GenesysClient,
    *,
    min_duration_seconds: int = MIN_DURATION_SECONDS,
) -> dict[str, Any]:
    started = time.perf_counter()
    now = _utc_now()

    candidates: set[str] = set()
    candidates.update(
        _analytics_user_ids(
            client,
            filter_key="routingStatusFilters",
            filter_body={
                "type": "or",
                "predicates": [{
                    "type": "dimension",
                    "dimension": "routingStatus",
                    "operator": "matches",
                    "value": "NOT_RESPONDING",
                }],
            },
        )
    )
    candidates.update(
        _analytics_user_ids(
            client,
            filter_key="presenceFilters",
            filter_body={
                "type": "or",
                "predicates": [{
                    "type": "dimension",
                    "dimension": "systemPresence",
                    "operator": "matches",
                    "value": "ON_QUEUE",
                }],
            },
        )
    )

    verified: list[dict[str, Any]] = []
    if candidates:
        with ThreadPoolExecutor(max_workers=MAX_ROUTING_WORKERS) as pool:
            futures = {pool.submit(_routing_status, client, uid): uid for uid in candidates}
            for future in as_completed(futures):
                uid = futures[future]
                routing = future.result()
                if not routing or safe_str(routing.get("status")).upper() != "NOT_RESPONDING":
                    continue
                start_time = _parse_utc(routing.get("startTime"))
                if not start_time:
                    continue
                duration_seconds = (now - start_time).total_seconds()
                if duration_seconds < min_duration_seconds:
                    continue
                verified.append({
                    "user_id": uid,
                    "start_time": start_time.isoformat(),
                    "duration_seconds": int(duration_seconds),
                    "duration_display": _format_duration(duration_seconds),
                })

    _attach_user_info(client, verified)
    verified.sort(key=lambda item: item["duration_seconds"], reverse=True)

    return {
        "generated_at": now.isoformat(),
        "count": len(verified),
        "min_duration_seconds": min_duration_seconds,
        "refresh_seconds": 30,
        "query_ms": int((time.perf_counter() - started) * 1000),
        "candidates_checked": len(candidates),
        "agents": verified,
    }
