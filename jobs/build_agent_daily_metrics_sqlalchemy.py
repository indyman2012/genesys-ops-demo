#!/usr/bin/env python3
"""
build_agent_daily_metrics_sqlalchemy.py

Builds agent_daily_metrics from gc_conversations.

Improvements:
- Resolves agent display names from local gc_users cache when available.
- Resolves queue names/divisions from local gc_queues cache when available.
- Avoids repeated Genesys API lookups during dashboard use.

Run:
    python tools\build_agent_daily_metrics_sqlalchemy.py --days-back 7
    python tools\build_agent_daily_metrics_sqlalchemy.py --days-back 7 --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from pydantic import ValidationError

from db import get_engine
from core.genesys_pydantic_models import Conversation, validate_conversation_payload
from core.pydantic_validation_helpers import ValidationLogger


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
    s = parse_dt(start)
    e = parse_dt(end)
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


def rows(conn, sql, params=None):
    return conn.execute(text(sql), params or {}).mappings().all()


def table_exists(conn, table_name):
    try:
        row = conn.execute(text("""
            SELECT COUNT(*) AS n
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
        """), {"table_name": table_name}).mappings().first()
        return bool(row and row["n"])
    except Exception:
        return False


def table_columns(conn, table_name: str) -> set[str]:
    try:
        return {r["Field"] for r in rows(conn, f"SHOW COLUMNS FROM `{table_name}`")}
    except Exception:
        return set()


def ensure_table(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS agent_daily_metrics (
            metric_date DATE NOT NULL,
            queue_id VARCHAR(128) NOT NULL,
            queue_name VARCHAR(255) NULL,
            division_name VARCHAR(255) NULL,
            agent_id VARCHAR(128) NOT NULL,
            agent_name VARCHAR(255) NULL,
            answered_calls INT NOT NULL DEFAULT 0,
            outbound_calls INT NOT NULL DEFAULT 0,
            handled_calls INT NOT NULL DEFAULT 0,
            avg_asa_sec DECIMAL(12,3) NULL,
            avg_talk_sec DECIMAL(12,3) NULL,
            avg_hold_sec DECIMAL(12,3) NULL,
            avg_acw_sec DECIMAL(12,3) NULL,
            raw_conversations INT NOT NULL DEFAULT 0,
            calculated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (metric_date, queue_id, agent_id),
            INDEX idx_adm_queue_name (queue_name),
            INDEX idx_adm_division_name (division_name),
            INDEX idx_adm_agent_name (agent_name),
            INDEX idx_adm_metric_date (metric_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


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

    return rows(conn, f"SELECT {', '.join(select_cols)} FROM gc_conversations {where}", params)


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


def extra(model, key):
    return (getattr(model, "model_extra", None) or {}).get(key)


def extract_queue_name(p, s):
    candidates = [
        getattr(p, "queueName", None),
        getattr(s, "queueName", None),
        extra(p, "queueName"),
        extra(s, "queueName"),
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return None


def extract_queue_id(p, s):
    candidates = [
        getattr(p, "queueId", None),
        getattr(s, "queueId", None),
        extra(p, "queueId"),
        extra(s, "queueId"),
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


def conv_start(conv: Conversation, fallback):
    dt = parse_dt(conv.conversationStart)
    if dt:
        return dt
    return parse_dt(fallback)


def participant_user_id(p):
    candidates = [
        extra(p, "userId"),
        extra(p, "user",),
        getattr(p, "participantId", None),
        extra(p, "id"),
    ]

    for c in candidates:
        if isinstance(c, dict) and c.get("id"):
            return str(c["id"])
        if isinstance(c, str) and c.strip():
            return c.strip()
    return None


def agent_identity(p, user_name_by_id):
    user_id = participant_user_id(p)
    participant_id = getattr(p, "participantId", None) or extra(p, "id")
    agent_id = user_id or participant_id

    agent_name = None

    if user_id and user_id in user_name_by_id:
        agent_name = user_name_by_id[user_id]

    if not agent_name:
        candidate = getattr(p, "participantName", None) or extra(p, "name") or extra(p, "participantName")
        if candidate and not str(candidate).startswith("userId:"):
            agent_name = str(candidate)

    if not agent_id and agent_name:
        agent_id = f"name:{agent_name}"

    if not agent_name:
        agent_name = str(agent_id or "Unknown Agent")

    return str(agent_id or agent_name), str(agent_name)


def agent_voice_metrics(conv: Conversation, user_name_by_id):
    agents = []

    for p in conv.participants:
        purpose = (p.purpose or "").lower()
        if purpose != "agent":
            continue

        agent_id, agent_name = agent_identity(p, user_name_by_id)
        first_interact = None
        talk = []
        hold = []
        acw = []

        for s in p.sessions:
            media = (s.mediaType or "").lower()
            if media != "voice":
                continue

            for seg in s.segments:
                seg_type = (seg.segmentType or "").lower()
                start = parse_dt(seg.segmentStart)

                if seg_type == "interact" and start:
                    if first_interact is None or start < first_interact:
                        first_interact = start

                dur = seconds_between(seg.segmentStart, seg.segmentEnd)
                if dur is None:
                    continue

                if seg_type == "interact":
                    talk.append(dur)
                elif seg_type == "hold":
                    hold.append(dur)
                elif seg_type in ("wrapup", "wrapupcode"):
                    acw.append(dur)

        if first_interact or talk or hold or acw:
            agents.append({
                "agent_id": agent_id,
                "agent_name": agent_name,
                "first_interact": first_interact,
                "talk": talk,
                "hold": hold,
                "acw": acw,
            })

    return agents


def lookup_users(conn):
    by_id = {}
    if not table_exists(conn, "gc_users"):
        return by_id

    try:
        for r in rows(conn, "SELECT user_id, name FROM gc_users WHERE user_id IS NOT NULL AND name IS NOT NULL"):
            by_id[str(r["user_id"])] = r["name"]
    except Exception:
        pass
    return by_id


def lookup_queues(conn):
    by_id = {}
    by_name = {}
    if not table_exists(conn, "gc_queues"):
        return by_id, by_name

    try:
        for r in rows(conn, """
            SELECT queue_id, queue_name, division_name
            FROM gc_queues
            WHERE queue_id IS NOT NULL OR queue_name IS NOT NULL
        """):
            payload = {
                "queue_name": r.get("queue_name"),
                "division_name": r.get("division_name"),
            }
            if r.get("queue_id"):
                by_id[str(r["queue_id"])] = payload
            if r.get("queue_name"):
                by_name[str(r["queue_name"])] = payload
    except Exception:
        pass

    return by_id, by_name


@dataclass
class Acc:
    answered_calls: int = 0
    outbound_calls: int = 0
    handled_calls: int = 0
    raw_conversations: int = 0
    asa: list[float] = field(default_factory=list)
    talk: list[float] = field(default_factory=list)
    hold: list[float] = field(default_factory=list)
    acw: list[float] = field(default_factory=list)

    def avg(self, values):
        return sum(values) / len(values) if values else None


def build_rollups(conn, source_rows, validate: bool = True):
    user_name_by_id = lookup_users(conn)
    queue_by_id, queue_by_name = lookup_queues(conn)

    rollups = defaultdict(Acc)
    logger = ValidationLogger("logs")
    stats = {
        "input_rows": len(source_rows),
        "validation_failures": 0,
        "agent_metric_rows": 0,
        "asa_values": 0,
        "asa_excluded_over_cap": 0,
        "skipped": 0,
        "user_names_cached": len(user_name_by_id),
        "queues_cached": len(queue_by_id) or len(queue_by_name),
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
        if not voice:
            stats["skipped"] += 1
            continue

        q_enter, qid, qname = first_queue_enter(conv)

        if not qid:
            qid = f"name:{qname}" if qname else "unknown_queue"
        if not qname:
            qname = qid
        if qid == "unknown_queue":
            stats["skipped"] += 1
            continue

        q_cache = queue_by_id.get(str(qid)) or queue_by_name.get(str(qname)) or {}
        final_qname = q_cache.get("queue_name") or qname
        division_name = q_cache.get("division_name")

        agents = agent_voice_metrics(conv, user_name_by_id)

        if not agents:
            continue

        for agent in agents:
            key = (
                start.date().isoformat(),
                str(qid),
                str(final_qname),
                division_name or "",
                str(agent["agent_id"]),
                str(agent["agent_name"]),
            )

            acc = rollups[key]
            acc.raw_conversations += 1

            has_interact = agent["first_interact"] is not None

            if direction == "inbound" and has_interact:
                acc.answered_calls += 1
                acc.handled_calls += 1

                if q_enter:
                    asa = (agent["first_interact"] - q_enter).total_seconds()
                    if 0 <= asa <= ASA_MAX_SECONDS:
                        acc.asa.append(asa)
                        stats["asa_values"] += 1
                    elif asa > ASA_MAX_SECONDS:
                        stats["asa_excluded_over_cap"] += 1

            elif direction == "outbound" and has_interact:
                acc.outbound_calls += 1
                acc.handled_calls += 1

            acc.talk.extend(agent["talk"])
            acc.hold.extend(agent["hold"])
            acc.acw.extend(agent["acw"])

            stats["agent_metric_rows"] += 1

            if len(debug) < 10:
                debug.append((final_qname, agent["agent_name"], direction, bool(has_interact)))

    return rollups, stats, debug


def save_rollups(conn, rollups, days_back: int, no_delete: bool = False):
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days_back)

    if not no_delete:
        conn.execute(
            text("DELETE FROM agent_daily_metrics WHERE metric_date >= :start_date AND metric_date <= :today"),
            {"start_date": start_date, "today": today},
        )

    insert_sql = text("""
        INSERT INTO agent_daily_metrics (
            metric_date, queue_id, queue_name, division_name,
            agent_id, agent_name,
            answered_calls, outbound_calls, handled_calls,
            avg_asa_sec, avg_talk_sec, avg_hold_sec, avg_acw_sec,
            raw_conversations
        )
        VALUES (
            :metric_date, :queue_id, :queue_name, :division_name,
            :agent_id, :agent_name,
            :answered_calls, :outbound_calls, :handled_calls,
            :avg_asa_sec, :avg_talk_sec, :avg_hold_sec, :avg_acw_sec,
            :raw_conversations
        )
        ON DUPLICATE KEY UPDATE
            queue_name=VALUES(queue_name),
            division_name=VALUES(division_name),
            agent_name=VALUES(agent_name),
            answered_calls=VALUES(answered_calls),
            outbound_calls=VALUES(outbound_calls),
            handled_calls=VALUES(handled_calls),
            avg_asa_sec=VALUES(avg_asa_sec),
            avg_talk_sec=VALUES(avg_talk_sec),
            avg_hold_sec=VALUES(avg_hold_sec),
            avg_acw_sec=VALUES(avg_acw_sec),
            raw_conversations=VALUES(raw_conversations),
            updated_at=CURRENT_TIMESTAMP
    """)

    payload = []

    for (metric_date, qid, qname, div, aid, aname), acc in rollups.items():
        payload.append({
            "metric_date": metric_date,
            "queue_id": qid,
            "queue_name": qname,
            "division_name": div or None,
            "agent_id": aid,
            "agent_name": aname,
            "answered_calls": acc.answered_calls,
            "outbound_calls": acc.outbound_calls,
            "handled_calls": acc.handled_calls,
            "avg_asa_sec": acc.avg(acc.asa),
            "avg_talk_sec": acc.avg(acc.talk),
            "avg_hold_sec": acc.avg(acc.hold),
            "avg_acw_sec": acc.avg(acc.acw),
            "raw_conversations": acc.raw_conversations,
        })

    if payload:
        conn.execute(insert_sql, payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-back", type=int, default=7)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-delete", action="store_true")
    parser.add_argument("--no-validate", action="store_true")
    args = parser.parse_args()

    engine = get_engine()

    try:
        with engine.begin() as conn:
            ensure_table(conn)
            source_rows = fetch_conversation_rows(conn, args.days_back)
            rollups, stats, debug = build_rollups(conn, source_rows, validate=not args.no_validate)

            print("Agent daily metrics build - SQLAlchemy/Pydantic")
            print(f"  Days back: {args.days_back}")
            print(f"  ASA cap: {ASA_MAX_SECONDS} seconds")
            print(f"  Source rows: {stats['input_rows']}")
            print(f"  Rollup rows: {len(rollups)}")
            print(f"  Agent metric rows seen: {stats['agent_metric_rows']}")
            print(f"  ASA values found: {stats['asa_values']}")
            print(f"  ASA values excluded over cap: {stats['asa_excluded_over_cap']}")
            print(f"  Validation failures: {stats['validation_failures']}")
            print(f"  Skipped: {stats['skipped']}")
            print(f"  Cached users available: {stats['user_names_cached']}")
            print(f"  Cached queues available: {stats['queues_cached']}")

            if debug:
                print("\nSample agent attribution:")
                for qname, agent_name, direction, has_interact in debug:
                    print(f"  Queue={qname} | Agent={agent_name} | Direction={direction} | Interact={has_interact}")

            if args.apply:
                save_rollups(conn, rollups, args.days_back, args.no_delete)
                print("\nApplied successfully.")
            else:
                print("\nDry run only. Re-run with --apply.")

        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
