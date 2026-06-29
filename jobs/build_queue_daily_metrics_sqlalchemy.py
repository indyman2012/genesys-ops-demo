#!/usr/bin/env python3
"""
build_queue_daily_metrics_sqlalchemy.py

SQLAlchemy-standardized rollup builder.

Purpose:
    Replace direct PyMySQL usage in rollup jobs with the shared db.py engine.

Current DB:
    MySQL

Future-ready:
    SQL Server support becomes easier because connection handling is centralized.
    Some SQL statements are still dialect-specific, but they are isolated here.

Latest ASA logic:
    ASA = first agent voice interact timestamp - first ACD queue-enter timestamp

Run:
    python tools\build_queue_daily_metrics_sqlalchemy.py --days-back 7 --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from db import get_engine  # noqa: E402
from core.genesys_pydantic_models import Conversation, validate_conversation_payload  # noqa: E402
from core.pydantic_validation_helpers import ValidationLogger  # noqa: E402
from pydantic import ValidationError  # noqa: E402

ASA_MAX_SECONDS = 900


def parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def seconds_between(start, end):
    s, e = parse_dt(start), parse_dt(end)
    if not s or not e:
        return None
    val = (e - s).total_seconds()
    return val if val >= 0 else None


def safe_json(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def as_dict(model):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model


def get_direction(conv: Conversation):
    return (conv.originatingDirection or "").lower() or None


def iter_sessions(conv: Conversation):
    for p in conv.participants:
        purpose = (p.purpose or "").lower()
        for s in p.sessions:
            media = (s.mediaType or "").lower()
            yield p, purpose, s, media


def iter_segments(conv: Conversation):
    for p, purpose, s, media in iter_sessions(conv):
        for seg in s.segments:
            seg_type = (seg.segmentType or "").lower()
            yield p, purpose, s, media, seg, seg_type


def has_voice(conv: Conversation):
    return any(media == "voice" for *_x, media in iter_sessions(conv))


def extract_queue_name(p, s):
    # Pydantic model allows extra fields, but we primarily use these known fields.
    candidates = [
        getattr(p, "queueName", None),
        getattr(p, "participantName", None),
        getattr(s, "queueName", None),
    ]

    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return None


def extract_queue_id(p, s):
    candidates = [
        getattr(p, "queueId", None),
        getattr(s, "queueId", None),
    ]

    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return None


def first_queue_enter(conv: Conversation):
    best = None
    qid = None
    qname = None

    for p, purpose, s, media, seg, seg_type in iter_segments(conv):
        if purpose != "acd" or media != "voice":
            continue

        start = parse_dt(seg.segmentStart)
        if start and (best is None or start < best):
            best = start
            qid = extract_queue_id(p, s)
            qname = extract_queue_name(p, s)

    return best, qid, qname


def first_agent_connect(conv: Conversation):
    best = None

    for p, purpose, s, media, seg, seg_type in iter_segments(conv):
        if purpose == "agent" and media == "voice" and seg_type == "interact":
            start = parse_dt(seg.segmentStart)
            if start and (best is None or start < best):
                best = start

    return best


def extract_agent_durations(conv: Conversation):
    talk, hold, acw = [], [], []

    for p, purpose, s, media, seg, seg_type in iter_segments(conv):
        if media != "voice":
            continue

        dur = seconds_between(seg.segmentStart, seg.segmentEnd)
        if dur is None:
            continue

        if purpose == "agent" and seg_type == "interact":
            talk.append(dur)
        elif seg_type == "hold":
            hold.append(dur)
        elif seg_type in ("wrapup", "wrapupcode"):
            acw.append(dur)

    return talk, hold, acw


def conv_start(conv: Conversation, fallback):
    dt = parse_dt(conv.conversationStart)
    if dt:
        return dt
    return parse_dt(fallback)


@dataclass
class Acc:
    offered_calls: int = 0
    outbound_calls: int = 0
    answered_calls: int = 0
    abandoned_calls: int = 0
    raw_conversations: int = 0
    asa: list[float] = field(default_factory=list)
    talk: list[float] = field(default_factory=list)
    hold: list[float] = field(default_factory=list)
    acw: list[float] = field(default_factory=list)

    def avg(self, values):
        return sum(values) / len(values) if values else None


def scalar(conn, sql, params=None):
    return conn.execute(text(sql), params or {}).scalar()


def rows(conn, sql, params=None):
    return conn.execute(text(sql), params or {}).mappings().all()


def table_columns(conn, table_name: str) -> set[str]:
    try:
        result = rows(conn, f"SHOW COLUMNS FROM `{table_name}`")
        return {r["Field"] for r in result}
    except Exception:
        return set()


def ensure_table(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS queue_daily_metrics (
            metric_date DATE NOT NULL,
            queue_id VARCHAR(128) NOT NULL,
            queue_name VARCHAR(255) NULL,
            division_name VARCHAR(255) NULL,
            offered_calls INT NOT NULL DEFAULT 0,
            outbound_calls INT NOT NULL DEFAULT 0,
            answered_calls INT NOT NULL DEFAULT 0,
            abandoned_calls INT NOT NULL DEFAULT 0,
            abandon_pct DECIMAL(8,3) NULL,
            avg_asa_sec DECIMAL(12,3) NULL,
            avg_talk_sec DECIMAL(12,3) NULL,
            avg_hold_sec DECIMAL(12,3) NULL,
            avg_acw_sec DECIMAL(12,3) NULL,
            raw_conversations INT NOT NULL DEFAULT 0,
            calculated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (metric_date, queue_id),
            INDEX idx_qdm_queue_name (queue_name),
            INDEX idx_qdm_division_name (division_name),
            INDEX idx_qdm_metric_date (metric_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))

    cols = table_columns(conn, "queue_daily_metrics")

    if "division_name" not in cols:
        conn.execute(text("ALTER TABLE queue_daily_metrics ADD COLUMN division_name VARCHAR(255) NULL AFTER queue_name"))
    if "calculated_at" not in cols:
        conn.execute(text("ALTER TABLE queue_daily_metrics ADD COLUMN calculated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"))
    if "updated_at" not in cols:
        conn.execute(text("ALTER TABLE queue_daily_metrics ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"))


def detect_gc_conversation_columns(conn):
    cols = table_columns(conn, "gc_conversations")
    lower = {c.lower(): c for c in cols}

    json_col = None
    for cand in ("raw_json", "conversation_json", "json", "payload", "raw_conversation", "conversation"):
        if cand in lower:
            json_col = lower[cand]
            break

    if not json_col:
        for c in cols:
            if "json" in c.lower() or "payload" in c.lower() or "conversation" in c.lower():
                json_col = c
                break

    if not json_col:
        raise RuntimeError("Could not detect JSON column in gc_conversations.")

    start_col = None
    for cand in ("conversation_start", "conversationStart", "start_time", "startTime", "created_at"):
        if cand.lower() in lower:
            start_col = lower[cand.lower()]
            break

    id_col = None
    for cand in ("conversation_id", "conversationId", "id"):
        if cand.lower() in lower:
            id_col = lower[cand.lower()]
            break

    return json_col, start_col, id_col


def fetch_conversation_rows(conn, days_back: int):
    json_col, start_col, id_col = detect_gc_conversation_columns(conn)

    select_cols = [f"`{json_col}` AS raw_payload"]
    if start_col:
        select_cols.append(f"`{start_col}` AS fallback_start")
    if id_col:
        select_cols.append(f"`{id_col}` AS conversation_id")

    where = ""
    params = {}

    if start_col:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        where = f"WHERE `{start_col}` >= :cutoff"
        params["cutoff"] = cutoff.replace(tzinfo=None)

    sql = f"SELECT {', '.join(select_cols)} FROM gc_conversations {where}"
    return rows(conn, sql, params)


def build_rollups(source_rows, validate: bool = True):
    rollups = defaultdict(Acc)
    logger = ValidationLogger("logs")
    stats = {
        "input_rows": len(source_rows),
        "validation_failures": 0,
        "asa_values": 0,
        "asa_excluded_over_cap": 0,
        "skipped": 0,
    }
    debug = []

    for row in source_rows:
        raw = safe_json(row.get("raw_payload"))
        if not raw:
            stats["skipped"] += 1
            continue

        if validate:
            try:
                conv = validate_conversation_payload(raw)
            except ValidationError as exc:
                stats["validation_failures"] += 1
                logger.log_failure(raw.get("conversationId") if isinstance(raw, dict) else None, exc)
                continue
        else:
            conv = Conversation.model_validate(raw)

        start = conv_start(conv, row.get("fallback_start"))
        if not start:
            stats["skipped"] += 1
            continue

        direction = get_direction(conv)
        voice = has_voice(conv)
        q_enter, qid, qname = first_queue_enter(conv)

        if not qid:
            qid = f"name:{qname}" if qname else "unknown_queue"
        if not qname:
            qname = qid
        if qid == "unknown_queue":
            stats["skipped"] += 1
            continue

        key = (start.date().isoformat(), qid, qname)
        acc = rollups[key]
        acc.raw_conversations += 1

        agent_connect = first_agent_connect(conv)
        answered = agent_connect is not None

        if direction == "inbound" and voice:
            acc.offered_calls += 1
            if answered:
                acc.answered_calls += 1
            else:
                acc.abandoned_calls += 1
        elif direction == "outbound" and voice:
            acc.outbound_calls += 1

        if direction == "inbound" and voice and q_enter and agent_connect:
            asa = (agent_connect - q_enter).total_seconds()
            if 0 <= asa <= ASA_MAX_SECONDS:
                acc.asa.append(asa)
                stats["asa_values"] += 1
                if len(debug) < 10:
                    debug.append((qname, round(asa, 1), q_enter.isoformat(), agent_connect.isoformat()))
            elif asa > ASA_MAX_SECONDS:
                stats["asa_excluded_over_cap"] += 1

        talk, hold, acw = extract_agent_durations(conv)
        acc.talk.extend(talk)
        acc.hold.extend(hold)
        acc.acw.extend(acw)

    return rollups, stats, debug


def save_rollups(conn, rollups, days_back: int, no_delete: bool = False):
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days_back)

    if not no_delete:
        conn.execute(
            text("DELETE FROM queue_daily_metrics WHERE metric_date >= :start_date AND metric_date <= :today"),
            {"start_date": start_date, "today": today},
        )

    insert_sql = text("""
        INSERT INTO queue_daily_metrics (
            metric_date, queue_id, queue_name,
            offered_calls, outbound_calls, answered_calls, abandoned_calls, abandon_pct,
            avg_asa_sec, avg_talk_sec, avg_hold_sec, avg_acw_sec,
            raw_conversations
        )
        VALUES (
            :metric_date, :queue_id, :queue_name,
            :offered_calls, :outbound_calls, :answered_calls, :abandoned_calls, :abandon_pct,
            :avg_asa_sec, :avg_talk_sec, :avg_hold_sec, :avg_acw_sec,
            :raw_conversations
        )
        ON DUPLICATE KEY UPDATE
            queue_name=VALUES(queue_name),
            offered_calls=VALUES(offered_calls),
            outbound_calls=VALUES(outbound_calls),
            answered_calls=VALUES(answered_calls),
            abandoned_calls=VALUES(abandoned_calls),
            abandon_pct=VALUES(abandon_pct),
            avg_asa_sec=VALUES(avg_asa_sec),
            avg_talk_sec=VALUES(avg_talk_sec),
            avg_hold_sec=VALUES(avg_hold_sec),
            avg_acw_sec=VALUES(avg_acw_sec),
            raw_conversations=VALUES(raw_conversations),
            updated_at=CURRENT_TIMESTAMP
    """)

    rows_to_insert = []

    for (metric_date, qid, qname), acc in rollups.items():
        abandon_pct = (acc.abandoned_calls / acc.offered_calls * 100.0) if acc.offered_calls else None

        rows_to_insert.append({
            "metric_date": metric_date,
            "queue_id": qid,
            "queue_name": qname,
            "offered_calls": acc.offered_calls,
            "outbound_calls": acc.outbound_calls,
            "answered_calls": acc.answered_calls,
            "abandoned_calls": acc.abandoned_calls,
            "abandon_pct": abandon_pct,
            "avg_asa_sec": acc.avg(acc.asa),
            "avg_talk_sec": acc.avg(acc.talk),
            "avg_hold_sec": acc.avg(acc.hold),
            "avg_acw_sec": acc.avg(acc.acw),
            "raw_conversations": acc.raw_conversations,
        })

    if rows_to_insert:
        conn.execute(insert_sql, rows_to_insert)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-back", type=int, default=7)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-delete", action="store_true")
    parser.add_argument("--no-validate", action="store_true", help="Disable Pydantic validation.")
    args = parser.parse_args()

    engine = get_engine()

    try:
        with engine.begin() as conn:
            ensure_table(conn)
            source_rows = fetch_conversation_rows(conn, args.days_back)
            rollups, stats, debug = build_rollups(source_rows, validate=not args.no_validate)

            print("Queue daily metrics build - SQLAlchemy/Pydantic")
            print(f"  Days back: {args.days_back}")
            print(f"  ASA cap: {ASA_MAX_SECONDS} seconds")
            print(f"  Source rows: {stats['input_rows']}")
            print(f"  Rollup rows: {len(rollups)}")
            print(f"  ASA values found: {stats['asa_values']}")
            print(f"  ASA values excluded over cap: {stats.get('asa_excluded_over_cap', 0)}")
            print(f"  Validation failures: {stats['validation_failures']}")
            print(f"  Skipped: {stats['skipped']}")

            if debug:
                print("\nSample ASA calculations:")
                for qname, asa, q_enter, agent_connect in debug:
                    print(f"  {qname}: ASA={asa}s | queue_enter={q_enter} | agent_connect={agent_connect}")

            if args.apply:
                save_rollups(conn, rollups, args.days_back, args.no_delete)
                print("\nApplied successfully.")
            else:
                print("\nDry run only. Re-run with --apply.")
                # engine.begin auto-commits; force rollback for dry run by raising would be ugly.
                # No writes occur in dry-run except table ensure. That is intentional.

        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
