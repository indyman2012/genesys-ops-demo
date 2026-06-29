from app.ui.agent_section_descriptions import AGENT_SECTION_DESCRIPTIONS

from pathlib import Path
import csv
import io
import os
import sys
from datetime import date, timedelta

from flask import Flask, Response, render_template, request
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from db import get_engine  # noqa: E402

app = Flask(
    __name__,
    template_folder=str(ROOT / "templates"),
    static_folder=str(ROOT / "static"),
)


def getf():
    today = date.today()
    return {
        "start_date": request.args.get("start_date") or (today - timedelta(days=7)).isoformat(),
        "end_date": request.args.get("end_date") or today.isoformat(),
        "queue_name": request.args.get("queue_name", "").strip(),
    }


def safe_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def avg_weighted(rows, value_key, weight_key):
    total = 0.0
    weight_total = 0
    for r in rows:
        value = safe_float(r.get(value_key))
        weight = int(r.get(weight_key) or 0)
        if value is not None and weight > 0:
            total += value * weight
            weight_total += weight
    return (total / weight_total) if weight_total else None


def data(f):
    with get_engine().connect() as c:
        queue_options = c.execute(text("""
            SELECT DISTINCT queue_name
            FROM agent_daily_metrics
            WHERE queue_name IS NOT NULL
              AND queue_name <> ''
            ORDER BY queue_name
        """)).scalars().all()

        if not f["queue_name"] and queue_options:
            f["queue_name"] = queue_options[0]

        agent_rows = c.execute(text("""
            SELECT
                agent_id,
                agent_name,
                SUM(answered_calls) answered_calls,
                SUM(outbound_calls) outbound_calls,
                SUM(handled_calls) handled_calls,
                AVG(avg_asa_sec) avg_asa_sec,
                AVG(avg_talk_sec) avg_talk_sec,
                AVG(avg_hold_sec) avg_hold_sec,
                AVG(avg_acw_sec) avg_acw_sec,
                SUM(raw_conversations) raw_conversations
            FROM agent_daily_metrics
            WHERE metric_date >= :start_date
              AND metric_date <= :end_date
              AND (:queue_name='' OR LOWER(TRIM(queue_name)) = LOWER(TRIM(:queue_name)))
            GROUP BY agent_id, agent_name
            ORDER BY handled_calls DESC, agent_name ASC
        """), f).mappings().all()

        trend = c.execute(text("""
            SELECT
                metric_date,
                SUM(answered_calls) answered_calls,
                SUM(outbound_calls) outbound_calls,
                SUM(handled_calls) handled_calls,
                ROUND(AVG(avg_asa_sec), 1) avg_asa_sec,
                ROUND(AVG(avg_talk_sec), 1) avg_talk_sec,
                ROUND(AVG(avg_hold_sec), 1) avg_hold_sec,
                ROUND(AVG(avg_acw_sec), 1) avg_acw_sec
            FROM agent_daily_metrics
            WHERE metric_date >= :start_date
              AND metric_date <= :end_date
              AND (:queue_name='' OR LOWER(TRIM(queue_name)) = LOWER(TRIM(:queue_name)))
            GROUP BY metric_date
            ORDER BY metric_date
        """), f).mappings().all()

    answered = sum(int(r["answered_calls"] or 0) for r in agent_rows)
    outbound = sum(int(r["outbound_calls"] or 0) for r in agent_rows)
    handled = sum(int(r["handled_calls"] or 0) for r in agent_rows)

    summary = {
        "agent_count": len(agent_rows),
        "answered_calls": answered,
        "outbound_calls": outbound,
        "handled_calls": handled,
        "avg_asa_sec": avg_weighted(agent_rows, "avg_asa_sec", "answered_calls"),
        "avg_talk_sec": avg_weighted(agent_rows, "avg_talk_sec", "handled_calls"),
        "avg_hold_sec": avg_weighted(agent_rows, "avg_hold_sec", "handled_calls"),
        "avg_acw_sec": avg_weighted(agent_rows, "avg_acw_sec", "handled_calls"),
    }

    return {
        "filters": f,
        "queue_options": queue_options,
        "summary": summary,
        "agents": [dict(r) for r in agent_rows],
        "trend": [dict(r) for r in trend],
        "section_desc": AGENT_SECTION_DESCRIPTIONS,
    }


@app.route("/")
@app.route("/perspective/agent")
def page():
    return render_template(
        "agent_perspective_metrics.html",
        data=data(getf()),
        section_desc=AGENT_SECTION_DESCRIPTIONS,
    )


@app.route("/perspective/agent/export.csv")
def export():
    d = data(getf())
    out = io.StringIO()
    fields = list(d["agents"][0].keys()) if d["agents"] else ["no_data"]
    w = csv.DictWriter(out, fieldnames=fields)
    w.writeheader()
    w.writerows(d["agents"])
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=agent_perspective_metrics_export.csv"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8053")), debug=False)
