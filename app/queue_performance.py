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
MAX_DAYS = 60
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

    body = {
        "interval": interval,
        "granularity": "P1D",
        "groupBy": ["queueId"],
        "filter": {
            "type": "and",
            "predicates": [
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
            ],
        },
        "metrics": list(AGGREGATE_METRICS),
    }
    resp = client.request(
        "POST",
        "/api/v2/analytics/conversations/aggregates/query",
        json_body=body,
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

    trend: list[dict[str, Any]] = []
    cursor = start_date
    while cursor <= end_date:
        key = cursor.isoformat()
        acc = per_day.get(key, MetricAccumulator())
        day_metrics = acc.to_metrics()
        trend.append({"date": key, **day_metrics})
        cursor += timedelta(days=1)

    return {
        "filters": filters,
        "interval": interval,
        "divisions": divisions,
        "summary": summary_row,
        "queues": queue_rows,
        "trend": trend,
        "metric_thresholds": METRIC_THRESHOLDS,
        "queue_count": len(queue_rows),
        "generated_at": date.today().isoformat(),
    }
