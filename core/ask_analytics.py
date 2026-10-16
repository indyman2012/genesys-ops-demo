"""Read-only Genesys analytics helpers for Ask Genesys Advanced scripts."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from core.genesys_client import GenesysClient, safe_str

# Same day-boundary convention as app/queue_performance (04:00Z anchors).
DEFAULT_TZ_OFFSET_HOURS = 4

# Genesys aggregate granularity for 30-minute buckets (NOT the top-level interval field).
GRANULARITY_30M = "PT30M"


def api_interval(start: date, end: date, tz_offset_hours: int = DEFAULT_TZ_OFFSET_HOURS) -> str:
    """Build Genesys analytics interval: inclusive calendar start/end dates in org day boundaries."""
    start_iso = f"{start.isoformat()}T{tz_offset_hours:02d}:00:00.000Z"
    end_exclusive = end + timedelta(days=1)
    end_iso = f"{end_exclusive.isoformat()}T{tz_offset_hours:02d}:00:00.000Z"
    return f"{start_iso}/{end_iso}"


def date_range_last_days(days: int, *, through: date | None = None) -> tuple[date, date]:
    if days < 1:
        raise ValueError("days must be >= 1")
    end = through or date.today()
    start = end - timedelta(days=days - 1)
    return start, end


def _parse_interval_start(interval_value: str) -> datetime | None:
    start = (interval_value or "").split("/")[0].strip()
    if not start:
        return None
    try:
        if start.endswith("Z"):
            return datetime.fromisoformat(start.replace("Z", "+00:00"))
        return datetime.fromisoformat(start)
    except ValueError:
        return None


def _local_wall_time_label(dt: datetime, tz_offset_hours: int) -> str:
    """Wall-clock HH:MM using the toolkit's fixed offset convention."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(timezone.utc) - timedelta(hours=tz_offset_hours)
    return f"{local.hour:02d}:{local.minute:02d}"


def _metric_count(data_bucket: dict[str, Any], metric: str) -> int:
    for row in data_bucket.get("metrics") or []:
        if safe_str(row.get("metric")) != metric:
            continue
        stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
        return int(stats.get("count") or 0)
    return 0


def query_queue_inbound_offered_buckets(
    client: GenesysClient,
    queue_id: str,
    interval: str,
    *,
    granularity: str = GRANULARITY_30M,
) -> list[dict[str, Any]]:
    """30m (or other) inbound voice offered counts for one queue over interval."""
    body = {
        "interval": interval,
        "granularity": granularity,
        "groupBy": ["queueId"],
        "filter": {
            "type": "and",
            "predicates": [
                {"type": "dimension", "dimension": "queueId", "operator": "matches", "value": queue_id},
                {"type": "dimension", "dimension": "direction", "operator": "matches", "value": "inbound"},
                {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "voice"},
            ],
        },
        "metrics": ["nOffered"],
    }
    resp = client.request("POST", "/api/v2/analytics/conversations/aggregates/query", json_body=body)
    out: list[dict[str, Any]] = []
    for result in resp.get("results") or []:
        group = result.get("group") or {}
        if safe_str(group.get("queueId")) != queue_id:
            continue
        for data_bucket in result.get("data") or []:
            offered = _metric_count(data_bucket, "nOffered")
            if offered <= 0:
                continue
            out.append(
                {
                    "interval": safe_str(data_bucket.get("interval")),
                    "offered": offered,
                }
            )
    return out


def peak_30min_time_of_day_inbound(
    client: GenesysClient,
    queue_id: str,
    *,
    days: int = 90,
    tz_offset_hours: int = DEFAULT_TZ_OFFSET_HOURS,
    chunk_days: int = 7,
) -> dict[str, Any]:
    """Sum inbound offered by clock-time 30m slot across many days; return the busiest slot."""
    start, end = date_range_last_days(days)
    slot_totals: dict[str, int] = defaultdict(int)
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=chunk_days - 1), end)
        interval = api_interval(cur, chunk_end, tz_offset_hours)
        for row in query_queue_inbound_offered_buckets(client, queue_id, interval):
            dt = _parse_interval_start(row.get("interval") or "")
            if not dt:
                continue
            label = _local_wall_time_label(dt, tz_offset_hours)
            slot_totals[label] += int(row.get("offered") or 0)
        cur = chunk_end + timedelta(days=1)

    if not slot_totals:
        return {
            "queue_id": queue_id,
            "days": days,
            "peak_slot_start": None,
            "peak_slot_end": None,
            "total_offered_in_peak_slot": 0,
            "slots_with_volume": 0,
        }

    peak_start = max(slot_totals, key=slot_totals.get)
    h, m = (int(x) for x in peak_start.split(":"))
    end_min = h * 60 + m + 30
    peak_end = f"{end_min // 60:02d}:{end_min % 60:02d}"
    return {
        "queue_id": queue_id,
        "days": days,
        "peak_slot_start": peak_start,
        "peak_slot_end": peak_end,
        "total_offered_in_peak_slot": slot_totals[peak_start],
        "slots_with_volume": len(slot_totals),
        "top_slots": sorted(
            [{"slot": k, "offered": v} for k, v in slot_totals.items()],
            key=lambda x: x["offered"],
            reverse=True,
        )[:5],
    }
