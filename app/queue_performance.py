"""Queue performance dashboard — Genesys Analytics aggregates (Queue Performance parity)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.ui.metric_thresholds import METRIC_THRESHOLDS, metric_status, metric_status_higher_better
from core.genesys_client import safe_str

TZ_OFFSET_HOURS = 4
DEFAULT_QUEUE_PREFIX = "Patient Services"
DEFAULT_DAYS = 7
MAX_DAYS = 90
TREND_CHART_DAYS = 90
TIME_METRICS = ("tTalk", "tHeld", "tAcw", "tHandle", "tAnswered", "tWait")
AGGREGATE_METRICS = (
    "nOffered",
    "tAnswered",
    "tAbandon",
    "tShortAbandon",
    "oServiceLevel",
    "oServiceTarget",
    *TIME_METRICS,
)


@dataclass
class MetricAccumulator:
    offered: int = 0
    answered: int = 0
    abandoned: int = 0
    short_abandoned: int = 0
    sl_numerator: int = 0
    sl_denominator: int = 0
    sl_target: float | None = None
    talk_ms: int = 0
    talk_count: int = 0
    hold_ms: int = 0
    hold_count: int = 0
    acw_ms: int = 0
    acw_count: int = 0
    handle_ms: int = 0
    handle_count: int = 0
    asa_ms: int = 0
    asa_count: int = 0
    wait_ms: int = 0
    wait_count: int = 0

    def absorb(self, bucket_metrics: dict[str, dict[str, Any]]) -> None:
        self.offered += int(bucket_metrics.get("nOffered", {}).get("count") or 0)
        self.answered += int(bucket_metrics.get("tAnswered", {}).get("count") or 0)
        self.abandoned += int(bucket_metrics.get("tAbandon", {}).get("count") or 0)
        self.short_abandoned += int(bucket_metrics.get("tShortAbandon", {}).get("count") or 0)

        sl = bucket_metrics.get("oServiceLevel") or {}
        self.sl_numerator += int(sl.get("numerator") or 0)
        self.sl_denominator += int(sl.get("denominator") or 0)
        if sl.get("target") is not None:
            self.sl_target = float(sl["target"])

        target = bucket_metrics.get("oServiceTarget") or {}
        if target.get("current") is not None and self.sl_target is None:
            self.sl_target = float(target["current"])

        for metric, ms_attr, count_attr in (
            ("tTalk", "talk_ms", "talk_count"),
            ("tHeld", "hold_ms", "hold_count"),
            ("tAcw", "acw_ms", "acw_count"),
            ("tHandle", "handle_ms", "handle_count"),
            ("tAnswered", "asa_ms", "asa_count"),
            ("tWait", "wait_ms", "wait_count"),
        ):
            stats = bucket_metrics.get(metric) or {}
            setattr(self, ms_attr, getattr(self, ms_attr) + int(stats.get("sum") or 0))
            setattr(self, count_attr, getattr(self, count_attr) + int(stats.get("count") or 0))

    def avg_seconds(self, ms: int, count: int) -> float | None:
        if count <= 0 or ms <= 0:
            return None
        return round(ms / count / 1000.0, 1)

    def to_metrics(self) -> dict[str, Any]:
        abandon_pct = round(self.abandoned / self.offered * 100, 2) if self.offered else None
        answer_pct = round(self.answered / self.offered * 100, 2) if self.offered else None
        service_level_pct = (
            round(self.sl_numerator / self.sl_denominator * 100, 1)
            if self.sl_denominator
            else None
        )
        asa_sec = self.avg_seconds(self.asa_ms, self.asa_count)
        talk_sec = self.avg_seconds(self.talk_ms, self.talk_count)
        hold_sec = self.avg_seconds(self.hold_ms, self.hold_count)
        acw_sec = self.avg_seconds(self.acw_ms, self.acw_count)
        handle_sec = self.avg_seconds(self.handle_ms, self.handle_count)
        return {
            "offered": self.offered,
            "answered": self.answered,
            "abandoned": self.abandoned,
            "short_abandoned": self.short_abandoned,
            "answer_pct": answer_pct,
            "abandon_pct": abandon_pct,
            "service_level_pct": service_level_pct,
            "service_level_target_pct": round((self.sl_target or 0.8) * 100, 1) if self.sl_target else 80.0,
            "asa_sec": asa_sec,
            "avg_talk_sec": talk_sec,
            "avg_hold_sec": hold_sec,
            "avg_acw_sec": acw_sec,
            "avg_handle_sec": handle_sec,
        }


def parse_filters(args: dict[str, Any]) -> dict[str, Any]:
    today = date.today()
    start_raw = safe_str(args.get("start_date"))
    end_raw = safe_str(args.get("end_date"))
    days_raw = safe_str(args.get("days"))

    if start_raw and end_raw:
        start_date = date.fromisoformat(start_raw)
        end_date = date.fromisoformat(end_raw)
    else:
        days = int(days_raw or DEFAULT_DAYS)
        days = max(1, min(days, MAX_DAYS))
        end_date = today
        start_date = today - timedelta(days=days - 1)

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    span_days = (end_date - start_date).days + 1
    if span_days > MAX_DAYS:
        start_date = end_date - timedelta(days=MAX_DAYS - 1)

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": (date.fromisoformat(end_date.isoformat()) - date.fromisoformat(start_date.isoformat())).days + 1,
        "division": safe_str(args.get("division")),
        "queue_prefix": safe_str(args.get("queue_prefix")) or DEFAULT_QUEUE_PREFIX,
        "target_asa": float(args.get("target_asa") or METRIC_THRESHOLDS["asa"]["good_max"]),
        "target_aht": float(args.get("target_aht") or METRIC_THRESHOLDS["aht"]["good_max"]),
        "target_acw": float(args.get("target_acw") or METRIC_THRESHOLDS["acw"]["good_max"]),
        "target_abandon_pct": float(args.get("target_abandon_pct") or METRIC_THRESHOLDS["abandon_pct"]["good_max"]),
        "target_service_level_pct": float(args.get("target_service_level_pct") or 80),
    }


def api_interval(start: date, end: date, tz_offset_hours: int = TZ_OFFSET_HOURS) -> str:
    start_iso = f"{start.isoformat()}T{tz_offset_hours:02d}:00:00.000Z"
    end_exclusive = end + timedelta(days=1)
    end_iso = f"{end_exclusive.isoformat()}T{tz_offset_hours:02d}:00:00.000Z"
    return f"{start_iso}/{end_iso}"


def day_key(interval_value: str) -> str:
    return (interval_value or "").split("/")[0][:10]


def bucket_metrics(data_bucket: dict[str, Any]) -> dict[str, dict[str, Any]]:
    parsed: dict[str, dict[str, Any]] = {}
    for metric in data_bucket.get("metrics") or []:
        name = safe_str(metric.get("metric"))
        stats = metric.get("stats") if isinstance(metric.get("stats"), dict) else {}
        if not name:
            continue
        if name == "nOffered":
            parsed[name] = {"count": int(stats.get("count") or 0)}
        elif name == "oServiceLevel":
            parsed[name] = {
                "numerator": int(stats.get("numerator") or 0),
                "denominator": int(stats.get("denominator") or 0),
                "ratio": stats.get("ratio"),
                "target": stats.get("target"),
            }
        elif name == "oServiceTarget":
            parsed[name] = {"current": stats.get("current")}
        else:
            parsed[name] = {
                "count": int(stats.get("count") or 0),
                "sum": int(stats.get("sum") or 0),
                "max": int(stats.get("max") or 0),
            }
    return parsed


def load_queue_catalog(client) -> tuple[dict[str, dict[str, str]], list[str]]:
    queues = client.paged_get("/api/v2/routing/queues")
    by_id: dict[str, dict[str, str]] = {}
    divisions: set[str] = set()
    for queue in queues:
        queue_id = safe_str(queue.get("id"))
        name = safe_str(queue.get("name"))
        if not queue_id or not name:
            continue
        division = queue.get("division") if isinstance(queue.get("division"), dict) else {}
        division_name = safe_str(division.get("name"))
        if division_name:
            divisions.add(division_name)
        by_id[queue_id] = {
            "id": queue_id,
            "name": name,
            "division_name": division_name,
        }
    return by_id, sorted(divisions)


def queue_in_scope(
    queue_meta: dict[str, str],
    *,
    queue_prefix: str,
    division: str,
) -> bool:
    name = queue_meta.get("name") or ""
    if queue_prefix and not name.startswith(queue_prefix):
        return False
    if division and (queue_meta.get("division_name") or "") != division:
        return False
    return True


def variance_pct(actual: float | None, target: float | None) -> float | None:
    if actual is None or not target:
        return None
    return round(((actual - target) / target) * 100, 1)


def band_from_variance(variance: float | None) -> str:
    if variance is None:
        return "na"
    value = abs(variance)
    if value < 10:
        return "green"
    if value < 20:
        return "yellow"
    return "red"


def overall_status(statuses: list[str]) -> str:
    normalized = [s for s in statuses if s not in ("metric-na", "na", "")]
    if not normalized:
        return "green"
    if any(s in ("metric-bad", "red") for s in normalized):
        return "red"
    if any(s in ("metric-warning", "yellow") for s in normalized):
        return "yellow"
    return "green"


def enrich_queue_row(
    queue_meta: dict[str, str],
    metrics: dict[str, Any],
    filters: dict[str, Any],
) -> dict[str, Any]:
    targets = {
        "asa": filters["target_asa"],
        "aht": filters["target_aht"],
        "acw": filters["target_acw"],
        "abandon_pct": filters["target_abandon_pct"],
        "service_level_pct": filters["target_service_level_pct"],
    }
    statuses = {
        "asa": metric_status("asa", metrics.get("asa_sec")),
        "aht": metric_status("aht", metrics.get("avg_talk_sec")),
        "acw": metric_status("acw", metrics.get("avg_acw_sec")),
        "abandon_pct": metric_status("abandon_pct", metrics.get("abandon_pct")),
        "service_level_pct": metric_status_higher_better(
            "service_level_pct",
            metrics.get("service_level_pct"),
        ),
    }
    variances = {
        "asa": variance_pct(metrics.get("asa_sec"), targets["asa"]),
        "aht": variance_pct(metrics.get("avg_talk_sec"), targets["aht"]),
        "acw": variance_pct(metrics.get("avg_acw_sec"), targets["acw"]),
        "abandon_pct": variance_pct(metrics.get("abandon_pct"), targets["abandon_pct"]),
    }
    return {
        "queue_id": queue_meta["id"],
        "queue_name": queue_meta["name"],
        "division_name": queue_meta.get("division_name") or "",
        **metrics,
        "statuses": statuses,
        "overall_status": overall_status(list(statuses.values())),
        "variances": variances,
        "variance_bands": {key: band_from_variance(value) for key, value in variances.items()},
    }


def inbound_voice_filter(queue_id: str | None = None) -> dict[str, Any]:
    predicates: list[dict[str, Any]] = [
        {
            "type": "dimension",
            "dimension": "direction",
            "operator": "matches",
            "value": "inbound",
        },
        {
            "type": "dimension",
            "dimension": "mediaType",
            "operator": "matches",
            "value": "voice",
        },
    ]
    if queue_id:
        predicates.append(
            {
                "type": "dimension",
                "dimension": "queueId",
                "operator": "matches",
                "value": queue_id,
            }
        )
    return {"type": "and", "predicates": predicates}


def query_conversation_aggregates(
    client,
    *,
    interval: str,
    group_by: list[str],
    queue_id: str | None = None,
    granularity: str = "P1D",
) -> dict[str, Any]:
    body = {
        "interval": interval,
        "granularity": granularity,
        "groupBy": group_by,
        "filter": inbound_voice_filter(queue_id),
        "metrics": list(AGGREGATE_METRICS),
    }
    return client.request(
        "POST",
        "/api/v2/analytics/conversations/aggregates/query",
        json_body=body,
    )


def load_user_names(client, user_ids: set[str]) -> dict[str, str]:
    names: dict[str, str] = {}
    for uid in sorted(user_ids):
        try:
            user = client.request("GET", f"/api/v2/users/{uid}")
            names[uid] = safe_str(user.get("name")) or uid
        except Exception:
            names[uid] = uid
    return names


def enrich_agent_row(
    user_id: str,
    user_name: str,
    metrics: dict[str, Any],
    filters: dict[str, Any],
) -> dict[str, Any]:
    row = enrich_queue_row(
        {"id": user_id, "name": user_name, "division_name": ""},
        metrics,
        filters,
    )
    return {
        "user_id": user_id,
        "user_name": user_name,
        **{key: row[key] for key in row if key not in {"queue_id", "queue_name", "division_name"}},
    }


def build_daily_trend(
    start_date: date,
    end_date: date,
    per_day: dict[str, MetricAccumulator],
) -> list[dict[str, Any]]:
    trend: list[dict[str, Any]] = []
    cursor = start_date
    while cursor <= end_date:
        key = cursor.isoformat()
        day_metrics = per_day.get(key, MetricAccumulator()).to_metrics()
        trend.append({"date": key, **day_metrics})
        cursor += timedelta(days=1)
    return trend


def chart_trend_window(end_date: date) -> tuple[date, date]:
    start_date = end_date - timedelta(days=TREND_CHART_DAYS - 1)
    return start_date, end_date


def fetch_daily_trend(
    client,
    *,
    start_date: date,
    end_date: date,
    scoped_ids: set[str] | None = None,
    queue_id: str | None = None,
) -> list[dict[str, Any]]:
    interval = api_interval(start_date, end_date)
    resp = query_conversation_aggregates(
        client,
        interval=interval,
        group_by=["queueId"],
        queue_id=queue_id,
    )
    allowed = {queue_id} if queue_id else scoped_ids
    per_day: dict[str, MetricAccumulator] = defaultdict(MetricAccumulator)
    for result in resp.get("results") or []:
        group = result.get("group") if isinstance(result.get("group"), dict) else {}
        entity_id = safe_str(group.get("queueId"))
        if allowed is not None and entity_id not in allowed:
            continue
        for data_bucket in result.get("data") or []:
            parsed = bucket_metrics(data_bucket)
            if parsed:
                per_day[day_key(data_bucket.get("interval") or "")].absorb(parsed)
    return build_daily_trend(start_date, end_date, per_day)


def absorb_aggregate_results(
    resp: dict[str, Any],
    *,
    group_key: str,
    per_group: dict[str, MetricAccumulator],
    per_day: dict[str, MetricAccumulator] | None = None,
    allowed_ids: set[str] | None = None,
) -> None:
    for result in resp.get("results") or []:
        group = result.get("group") if isinstance(result.get("group"), dict) else {}
        entity_id = safe_str(group.get(group_key))
        if allowed_ids is not None and entity_id not in allowed_ids:
            continue
        if not entity_id and group_key:
            continue
        bucket_target = per_group.setdefault(entity_id or "__all__", MetricAccumulator())
        for data_bucket in result.get("data") or []:
            parsed = bucket_metrics(data_bucket)
            if not parsed:
                continue
            bucket_target.absorb(parsed)
            if per_day is not None:
                per_day[day_key(data_bucket.get("interval") or "")].absorb(parsed)


def fetch_queue_detail(
    client,
    queue_id: str,
    filters: dict[str, Any],
) -> dict[str, Any]:
    queue_catalog, _divisions = load_queue_catalog(client)
    queue_meta = queue_catalog.get(queue_id)
    if not queue_meta:
        raise ValueError(f"Unknown queue id: {queue_id}")

    start_date = date.fromisoformat(filters["start_date"])
    end_date = date.fromisoformat(filters["end_date"])
    interval = api_interval(start_date, end_date)

    queue_resp = query_conversation_aggregates(
        client,
        interval=interval,
        group_by=["queueId"],
        queue_id=queue_id,
        granularity="P1D",
    )
    per_day: dict[str, MetricAccumulator] = defaultdict(MetricAccumulator)
    per_queue: dict[str, MetricAccumulator] = defaultdict(MetricAccumulator)
    absorb_aggregate_results(
        queue_resp,
        group_key="queueId",
        per_group=per_queue,
        per_day=per_day,
        allowed_ids={queue_id},
    )

    agent_resp = query_conversation_aggregates(
        client,
        interval=interval,
        group_by=["userId"],
        queue_id=queue_id,
        granularity="P1D",
    )
    per_agent: dict[str, MetricAccumulator] = defaultdict(MetricAccumulator)
    absorb_aggregate_results(
        agent_resp,
        group_key="userId",
        per_group=per_agent,
    )

    queue_acc = per_queue.get(queue_id) or MetricAccumulator()
    summary = enrich_queue_row(queue_meta, queue_acc.to_metrics(), filters)
    trend = build_daily_trend(start_date, end_date, per_day)
    chart_start, chart_end = chart_trend_window(end_date)
    trend_90d = fetch_daily_trend(
        client,
        start_date=chart_start,
        end_date=chart_end,
        queue_id=queue_id,
    )

    agent_rows: list[dict[str, Any]] = []
    user_ids = {uid for uid, acc in per_agent.items() if acc.offered > 0 or acc.answered > 0}
    user_names = load_user_names(client, user_ids)
    for user_id, acc in per_agent.items():
        if acc.offered <= 0 and acc.answered <= 0:
            continue
        agent_rows.append(
            enrich_agent_row(
                user_id,
                user_names.get(user_id, user_id),
                acc.to_metrics(),
                filters,
            )
        )
    agent_rows.sort(
        key=lambda row: (-(row.get("offered") or 0), row.get("user_name") or ""),
    )

    return {
        "filters": filters,
        "interval": interval,
        "queue": summary,
        "trend": trend,
        "trend_90d": trend_90d,
        "trend_chart_days": TREND_CHART_DAYS,
        "agents": agent_rows,
        "agent_count": len(agent_rows),
        "metric_thresholds": METRIC_THRESHOLDS,
    }


def fetch_queue_performance(client, filters: dict[str, Any]) -> dict[str, Any]:
    start_date = date.fromisoformat(filters["start_date"])
    end_date = date.fromisoformat(filters["end_date"])
    interval = api_interval(start_date, end_date)

    queue_catalog, divisions = load_queue_catalog(client)
    scoped_ids = {
        queue_id
        for queue_id, meta in queue_catalog.items()
        if queue_in_scope(
            meta,
            queue_prefix=filters["queue_prefix"],
            division=filters["division"],
        )
    }

    resp = query_conversation_aggregates(
        client,
        interval=interval,
        group_by=["queueId"],
    )

    per_queue: dict[str, MetricAccumulator] = defaultdict(MetricAccumulator)
    per_day: dict[str, MetricAccumulator] = defaultdict(MetricAccumulator)

    for result in resp.get("results") or []:
        group = result.get("group") if isinstance(result.get("group"), dict) else {}
        queue_id = safe_str(group.get("queueId"))
        if queue_id not in scoped_ids:
            continue
        for data_bucket in result.get("data") or []:
            parsed = bucket_metrics(data_bucket)
            if not parsed:
                continue
            per_queue[queue_id].absorb(parsed)
            per_day[day_key(data_bucket.get("interval") or "")].absorb(parsed)

    queue_rows: list[dict[str, Any]] = []
    for queue_id in scoped_ids:
        acc = per_queue.get(queue_id)
        if not acc or acc.offered <= 0:
            continue
        metrics = acc.to_metrics()
        queue_rows.append(
            enrich_queue_row(queue_catalog[queue_id], metrics, filters),
        )

    queue_rows.sort(
        key=lambda row: (
            {"red": 0, "yellow": 1, "green": 2}.get(row["overall_status"], 3),
            -(row.get("offered") or 0),
            row.get("queue_name") or "",
        ),
    )

    summary_acc = MetricAccumulator()
    for acc in per_queue.values():
        summary_acc.offered += acc.offered
        summary_acc.answered += acc.answered
        summary_acc.abandoned += acc.abandoned
        summary_acc.short_abandoned += acc.short_abandoned
        summary_acc.sl_numerator += acc.sl_numerator
        summary_acc.sl_denominator += acc.sl_denominator
        summary_acc.talk_ms += acc.talk_ms
        summary_acc.talk_count += acc.talk_count
        summary_acc.hold_ms += acc.hold_ms
        summary_acc.hold_count += acc.hold_count
        summary_acc.acw_ms += acc.acw_ms
        summary_acc.acw_count += acc.acw_count
        summary_acc.handle_ms += acc.handle_ms
        summary_acc.handle_count += acc.handle_count
        summary_acc.asa_ms += acc.asa_ms
        summary_acc.asa_count += acc.asa_count

    summary_metrics = summary_acc.to_metrics()
    summary_row = enrich_queue_row(
        {"id": "", "name": "All filtered queues", "division_name": ""},
        summary_metrics,
        filters,
    )

    trend = build_daily_trend(start_date, end_date, per_day)
    chart_start, chart_end = chart_trend_window(end_date)
    trend_90d = fetch_daily_trend(
        client,
        start_date=chart_start,
        end_date=chart_end,
        scoped_ids=scoped_ids,
    )

    return {
        "filters": filters,
        "interval": interval,
        "divisions": divisions,
        "summary": summary_row,
        "queues": queue_rows,
        "trend": trend,
        "trend_90d": trend_90d,
        "trend_chart_days": TREND_CHART_DAYS,
        "metric_thresholds": METRIC_THRESHOLDS,
        "queue_count": len(queue_rows),
        "generated_at": date.today().isoformat(),
    }
