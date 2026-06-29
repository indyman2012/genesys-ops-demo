from app.ui.section_descriptions import SECTION_DESCRIPTIONS
from app.ui.metric_thresholds import METRIC_THRESHOLDS, metric_status

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


def band(v):
    if v is None:
        return "yellow"
    v = abs(v)
    return "green" if v < 10 else ("yellow" if v < 20 else "red")


def getf():
    today = date.today()
    return {
        "start_date": request.args.get("start_date") or (today - timedelta(days=7)).isoformat(),
        "end_date": request.args.get("end_date") or today.isoformat(),
        "division": request.args.get("division", "").strip(),
        "queue_prefix": request.args.get("queue_prefix", "").strip(),
        "baseline_inbound_pct": float(request.args.get("baseline_inbound_pct") or 82),
        "target_asa": float(request.args.get("target_asa") or 30),
        "target_aht": float(request.args.get("target_aht") or 360),
        "target_acw": float(request.args.get("target_acw") or 45),
        "target_abandon_pct": float(request.args.get("target_abandon_pct") or 5),
    }


def pct(actual, target):
    if actual is None or not target:
        return None
    return ((actual - target) / target) * 100


def table_exists(conn, table_name):
    try:
        row = conn.execute(
            text("""
                SELECT COUNT(*) AS n
                FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND table_name = :table_name
            """),
            {"table_name": table_name},
        ).mappings().first()
        return bool(row and row["n"])
    except Exception:
        return False


def table_columns(conn, table_name):
    try:
        rows = conn.execute(text(f"SHOW COLUMNS FROM `{table_name}`")).mappings().all()
        return {r["Field"] for r in rows}
    except Exception:
        return set()


def qdm_has_real_divisions(conn):
    try:
        row = conn.execute(
            text("""
                SELECT COUNT(DISTINCT division_name) AS n
                FROM queue_daily_metrics
                WHERE division_name IS NOT NULL
                  AND division_name <> ''
            """)
        ).mappings().first()
        return bool(row and int(row["n"] or 0) > 0)
    except Exception:
        return False


def division_sql_bits(conn):
    """
    Robust division handling.

    The previous fix populated the dropdown from gc_queues, but filtering could
    return zero rows if queue_daily_metrics.queue_id did not match gc_queues.id
    exactly. This version builds a broader join to gc_queues using every matching
    key available:
        qdm.queue_id   -> gc_queues.queue_id / queueId / id
        qdm.queue_name -> gc_queues.queue_name / queueName / name

    It also uses an effective division expression:
        COALESCE(qdm.division_name, gc_queues.division_name)

    So the filter and display agree.
    """
    qdm_cols = table_columns(conn, "queue_daily_metrics")
    has_qdm_div = "division_name" in qdm_cols and qdm_has_real_divisions(conn)

    gcq_available = table_exists(conn, "gc_queues")
    gcq_cols = table_columns(conn, "gc_queues") if gcq_available else set()

    gcq_div_col = None
    for candidate in ["division_name", "divisionName", "division", "division_display_name", "divisionDisplayName"]:
        if candidate in gcq_cols:
            gcq_div_col = candidate
            break

    join_conditions = []

    if gcq_div_col:
        if "queue_id" in qdm_cols:
            for candidate in ["queue_id", "queueId", "id"]:
                if candidate in gcq_cols:
                    join_conditions.append(f"gq.`{candidate}` = qdm.`queue_id`")

        if "queue_name" in qdm_cols:
            for candidate in ["queue_name", "queueName", "name"]:
                if candidate in gcq_cols:
                    join_conditions.append(f"gq.`{candidate}` = qdm.`queue_name`")

    if gcq_div_col and join_conditions:
        join = "LEFT JOIN gc_queues gq ON (" + " OR ".join(join_conditions) + ")"

        if "division_name" in qdm_cols:
            division_expr = f"COALESCE(NULLIF(qdm.division_name, ''), NULLIF(gq.`{gcq_div_col}`, ''), '')"
        else:
            division_expr = f"COALESCE(NULLIF(gq.`{gcq_div_col}`, ''), '')"

        # Build dropdown from BOTH places if possible. This avoids the dropdown
        # depending on one source while the filter depends on another.
        qdm_div_sql = ""
        if "division_name" in qdm_cols:
            qdm_div_sql = """
                SELECT DISTINCT division_name
                FROM queue_daily_metrics
                WHERE division_name IS NOT NULL
                  AND division_name <> ''
                UNION
            """

        divs_sql = f"""
            {qdm_div_sql}
            SELECT DISTINCT `{gcq_div_col}` AS division_name
            FROM gc_queues
            WHERE `{gcq_div_col}` IS NOT NULL
              AND `{gcq_div_col}` <> ''
            ORDER BY division_name
        """

        return {
            "source": "effective: qdm division fallback to gc_queues",
            "join": join,
            "select": f"{division_expr} AS division_name",
            "where": f"(:division='' OR {division_expr}=:division)",
            "group": division_expr,
            "divs_sql": divs_sql,
        }

    if has_qdm_div:
        return {
            "source": "queue_daily_metrics.division_name",
            "join": "",
            "select": "qdm.division_name",
            "where": "(:division='' OR qdm.division_name=:division)",
            "group": "qdm.division_name",
            "divs_sql": """
                SELECT DISTINCT division_name
                FROM queue_daily_metrics
                WHERE division_name IS NOT NULL
                  AND division_name <> ''
                ORDER BY division_name
            """,
        }

    return {
        "source": "none",
        "join": "",
        "select": "'' AS division_name",
        "where": "(:division='')",
        "group": "''",
        "divs_sql": "SELECT '' AS division_name WHERE 1=0",
    }


def safe_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def data(f):
    with get_engine().connect() as c:
        divbits = division_sql_bits(c)

        trend = c.execute(
            text(f"""
                SELECT
                    qdm.metric_date,
                    SUM(qdm.offered_calls) inbound_calls,
                    SUM(qdm.outbound_calls) outbound_calls,
                    SUM(qdm.offered_calls + qdm.outbound_calls) total_calls,
                    ROUND(AVG(qdm.avg_asa_sec), 1) avg_asa_sec,
                    ROUND(AVG(qdm.avg_talk_sec), 1) avg_talk_sec,
                    ROUND(AVG(qdm.avg_acw_sec), 1) avg_acw_sec,
                    SUM(qdm.abandoned_calls) abandoned_calls,
                    CASE WHEN SUM(qdm.offered_calls)>0
                        THEN ROUND(SUM(qdm.abandoned_calls)/SUM(qdm.offered_calls)*100,2)
                        ELSE NULL
                    END abandon_pct
                FROM queue_daily_metrics qdm
                {divbits["join"]}
                WHERE qdm.metric_date >= :start_date
                  AND qdm.metric_date <= :end_date
                  AND {divbits["where"]}
                  AND (:queue_prefix='' OR qdm.queue_name LIKE CONCAT(:queue_prefix, '%'))
                GROUP BY qdm.metric_date
                ORDER BY qdm.metric_date
            """),
            f,
        ).mappings().all()

        queues = c.execute(
            text(f"""
                SELECT
                    qdm.queue_name,
                    {divbits["select"]},
                    SUM(qdm.offered_calls) inbound_calls,
                    SUM(qdm.outbound_calls) outbound_calls,
                    SUM(qdm.offered_calls + qdm.outbound_calls) total_calls,
                    SUM(qdm.answered_calls) answered_calls,
                    SUM(qdm.abandoned_calls) abandoned_calls,
                    CASE WHEN SUM(qdm.offered_calls)>0
                        THEN SUM(qdm.abandoned_calls)/SUM(qdm.offered_calls)*100
                        ELSE NULL
                    END abandon_pct,
                    AVG(qdm.avg_asa_sec) avg_asa_sec,
                    AVG(qdm.avg_talk_sec) avg_talk_sec,
                    AVG(qdm.avg_hold_sec) avg_hold_sec,
                    AVG(qdm.avg_acw_sec) avg_acw_sec
                FROM queue_daily_metrics qdm
                {divbits["join"]}
                WHERE qdm.metric_date >= :start_date
                  AND qdm.metric_date <= :end_date
                  AND {divbits["where"]}
                  AND (:queue_prefix='' OR qdm.queue_name LIKE CONCAT(:queue_prefix, '%'))
                GROUP BY qdm.queue_name, {divbits["group"]}
                ORDER BY total_calls DESC
                LIMIT 100
            """),
            f,
        ).mappings().all()

        try:
            divs = c.execute(text(divbits["divs_sql"])).scalars().all()
            divs = [d for d in divs if d]
        except Exception:
            divs = []

    inbound = sum(int(r["inbound_calls"] or 0) for r in trend)
    outbound = sum(int(r["outbound_calls"] or 0) for r in trend)
    total = inbound + outbound
    abandoned = sum(int(r["abandoned_calls"] or 0) for r in trend)
    inbound_pct = (inbound / total * 100) if total else 0

    vals = lambda col: [float(r[col]) for r in trend if r[col] is not None]
    avg = lambda xs: sum(xs) / len(xs) if xs else None

    qout = []
    for r in queues:
        asa = safe_float(r["avg_asa_sec"])
        aht = safe_float(r["avg_talk_sec"])
        acw = safe_float(r["avg_acw_sec"])
        ab = safe_float(r["abandon_pct"])

        av = pct(asa, f["target_asa"])
        tv = pct(aht, f["target_aht"])
        cv = pct(acw, f["target_acw"])
        bv = pct(ab, f["target_abandon_pct"])

        statuses = [band(x) for x in [av, tv, cv, bv]]
        overall = "red" if "red" in statuses else ("yellow" if "yellow" in statuses else "green")

        qout.append({
            "queue_name": r["queue_name"],
            "division_name": r["division_name"] or "",
            "total_calls": int(r["total_calls"] or 0),
            "inbound_calls": int(r["inbound_calls"] or 0),
            "outbound_calls": int(r["outbound_calls"] or 0),
            "answered_calls": int(r["answered_calls"] or 0),
            "abandoned_calls": int(r["abandoned_calls"] or 0),
            "abandon_pct": None if ab is None else round(ab, 2),
            "avg_asa_sec": None if asa is None else round(asa, 1),
            "avg_talk_sec": None if aht is None else round(aht, 1),
            "avg_hold_sec": None if r["avg_hold_sec"] is None else round(float(r["avg_hold_sec"]), 1),
            "avg_acw_sec": None if acw is None else round(acw, 1),
            "asa_var": None if av is None else round(av, 1),
            "aht_var": None if tv is None else round(tv, 1),
            "acw_var": None if cv is None else round(cv, 1),
            "abandon_var": None if bv is None else round(bv, 1),
            "overall_status": overall,
        })

    return {
        "filters": f,
        "cards": {
            "inbound": inbound,
            "outbound": outbound,
            "total": total,
            "abandoned": abandoned,
            "abandon_pct": (round(abandoned / inbound * 100, 2) if inbound else None),
            "inbound_pct": round(inbound_pct, 1),
            "mix_variance": round(inbound_pct - f["baseline_inbound_pct"], 1),
            "avg_asa": (round(avg(vals("avg_asa_sec")), 1) if vals("avg_asa_sec") else None),
            "avg_aht": (round(avg(vals("avg_talk_sec")), 1) if vals("avg_talk_sec") else None),
            "avg_acw": (round(avg(vals("avg_acw_sec")), 1) if vals("avg_acw_sec") else None),
        },
        "trend": [dict(r) for r in trend],
        "queues": qout,
        "divisions": divs,
        "division_source": divbits.get("source", "unknown"),
        "section_desc": SECTION_DESCRIPTIONS,
        "metric_thresholds": METRIC_THRESHOLDS,
    }


@app.route("/")
@app.route("/perspective/queue")
def page():
    return render_template(
        "queue_perspective_metrics.html",
        data=data(getf()),
        section_desc=SECTION_DESCRIPTIONS,
        metric_thresholds=METRIC_THRESHOLDS,
        metric_status=metric_status,
    )


@app.route("/perspective/queue/export.csv")
def export():
    d = data(getf())
    out = io.StringIO()
    fields = list(d["queues"][0].keys()) if d["queues"] else ["no_data"]
    w = csv.DictWriter(out, fieldnames=fields)
    w.writeheader()
    w.writerows(d["queues"])
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=queue_perspective_metrics_export.csv"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8052")), debug=False)
