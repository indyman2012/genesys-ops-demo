METRIC_THRESHOLDS = {
    "asa": {"label": "ASA", "baseline": 30, "good_max": 30, "warn_max": 60, "unit": "sec", "description": "Average Speed to Answer"},
    "aht": {"label": "AHT", "baseline": 360, "good_max": 360, "warn_max": 480, "unit": "sec", "description": "Average Handle / Talk Time"},
    "acw": {"label": "ACW", "baseline": 45, "good_max": 45, "warn_max": 90, "unit": "sec", "description": "After Call Work"},
    "abandon_pct": {"label": "Abandon %", "baseline": 5, "good_max": 5, "warn_max": 10, "unit": "%", "description": "Percent of offered calls not answered"},
    "service_level_pct": {
        "label": "Service Level",
        "baseline": 80,
        "good_min": 80,
        "warn_min": 70,
        "unit": "%",
        "description": "Percent of interactions answered within the queue service target",
        "higher_is_better": True,
    },
}


def metric_status_higher_better(metric_key, value):
    if value is None:
        return "metric-na"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "metric-na"
    cfg = METRIC_THRESHOLDS.get(metric_key)
    if not cfg:
        return "metric-na"
    good_min = cfg.get("good_min", cfg.get("baseline", 0))
    warn_min = cfg.get("warn_min", good_min)
    if numeric >= good_min:
        return "metric-good"
    if numeric >= warn_min:
        return "metric-warning"
    return "metric-bad"


def metric_status(metric_key, value):
    if value is None:
        return "metric-na"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "metric-na"
    cfg = METRIC_THRESHOLDS.get(metric_key)
    if not cfg:
        return "metric-na"
    if cfg.get("higher_is_better"):
        return metric_status_higher_better(metric_key, numeric)
    if numeric <= cfg["good_max"]:
        return "metric-good"
    if numeric <= cfg["warn_max"]:
        return "metric-warning"
    return "metric-bad"
