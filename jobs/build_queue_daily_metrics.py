#!/usr/bin/env python3
"""
build_queue_daily_metrics_asa_timestamp.py

Builds/refreshes queue_daily_metrics from gc_conversations, with ASA calculated as:

    ASA = first agent interact start time - first ACD/queue enter time

Filters for ASA:
    - inbound only
    - voice only
    - answered only
    - queue_enter and agent_connect must both exist
    - negative ASA values are ignored

Usage:
    python tools\build_queue_daily_metrics_asa_timestamp.py --days-back 7 --apply

DB env vars are read from a colocated client.env first, then from process env.

Supported DB env names:
    MYSQL_HOST / DB_HOST          default: 127.0.0.1
    MYSQL_PORT / DB_PORT          default: 3306
    MYSQL_DATABASE / DB_NAME      default: genesys
    MYSQL_USER / DB_USER          default: root
    MYSQL_PASSWORD / DB_PASSWORD  default: password
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple


try:
    import pymysql
except ImportError:
    print("Missing dependency: pymysql")
    print("Install with: pip install pymysql")
    sys.exit(1)


# -----------------------------
# Environment / DB helpers
# -----------------------------

def load_client_env() -> None:
    """
    Load simple KEY=VALUE lines from client.env.
    Search order:
      1) current working directory
      2) script directory
      3) parent of script directory
    """
    candidates = [
        Path.cwd() / "client.env",
        Path(__file__).resolve().parent / "client.env",
        Path(__file__).resolve().parent.parent / "client.env",
    ]

    for env_path in candidates:
        if not env_path.exists():
            continue

        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            # Do not overwrite real environment variables.
            os.environ.setdefault(key, value)

        return


def env_first(*names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return default


def get_db_connection():
    load_client_env()

    host = env_first("MYSQL_HOST", "DB_HOST", default="127.0.0.1")
    port = int(env_first("MYSQL_PORT", "DB_PORT", default="3306") or "3306")
    database = env_first("MYSQL_DATABASE", "DB_NAME", default="genesys")
    user = env_first("MYSQL_USER", "DB_USER", default="root")
    password = env_first("MYSQL_PASSWORD", "DB_PASSWORD", default="password")

    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


# -----------------------------
# Date / JSON helpers
# -----------------------------

def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    try:
        # Genesys generally uses ISO timestamps with Z.
        cleaned = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def min_dt(current: Optional[datetime], candidate: Optional[datetime]) -> Optional[datetime]:
    if candidate is None:
        return current
    if current is None or candidate < current:
        return candidate
    return current


def seconds_between(start: Optional[str], end: Optional[str]) -> Optional[float]:
    start_dt = parse_dt(start)
    end_dt = parse_dt(end)
    if not start_dt or not end_dt:
        return None
    seconds = (end_dt - start_dt).total_seconds()
    if seconds < 0:
        return None
    return seconds


def safe_json_loads(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def find_first_present(row: Dict[str, Any], candidates: Iterable[str]) -> Optional[Any]:
    lowered = {k.lower(): k for k in row.keys()}
    for candidate in candidates:
        key = lowered.get(candidate.lower())
        if key is not None:
            return row[key]
    return None


# -----------------------------
# Conversation metric extraction
# -----------------------------

@dataclass
class QueueMetricAccumulator:
    offered_calls: int = 0
    outbound_calls: int = 0
    answered_calls: int = 0
    abandoned_calls: int = 0
    raw_conversations: int = 0

    asa_values: list[float] = field(default_factory=list)
    talk_values: list[float] = field(default_factory=list)
    hold_values: list[float] = field(default_factory=list)
    acw_values: list[float] = field(default_factory=list)

    def avg(self, values: list[float]) -> Optional[float]:
        if not values:
            return None
        return sum(values) / len(values)

    def as_db_row(self) -> Dict[str, Any]:
        abandon_pct = None
        if self.offered_calls:
            abandon_pct = (self.abandoned_calls / self.offered_calls) * 100.0

        return {
            "offered_calls": self.offered_calls,
            "outbound_calls": self.outbound_calls,
            "answered_calls": self.answered_calls,
            "abandoned_calls": self.abandoned_calls,
            "abandon_pct": abandon_pct,
            "avg_asa_sec": self.avg(self.asa_values),
            "avg_talk_sec": self.avg(self.talk_values),
            "avg_hold_sec": self.avg(self.hold_values),
            "avg_acw_sec": self.avg(self.acw_values),
            "raw_conversations": self.raw_conversations,
        }


def get_conversation_start(conv: Dict[str, Any], fallback: Optional[Any] = None) -> Optional[datetime]:
    for key in ("conversationStart", "startTime", "start_time"):
        dt = parse_dt(conv.get(key))
        if dt:
            return dt
    if isinstance(fallback, datetime):
        if fallback.tzinfo is None:
            return fallback.replace(tzinfo=timezone.utc)
        return fallback.astimezone(timezone.utc)
    if isinstance(fallback, str):
        return parse_dt(fallback)
    return None


def get_direction(conv: Dict[str, Any]) -> Optional[str]:
    value = conv.get("originatingDirection") or conv.get("direction")
    if isinstance(value, str):
        return value.lower()
    return None


def iter_sessions(conv: Dict[str, Any]):
    for participant in conv.get("participants", []) or []:
        purpose = (participant.get("purpose") or "").lower()
        participant_name = participant.get("participantName") or participant.get("name")
        for session in participant.get("sessions", []) or []:
            media_type = (session.get("mediaType") or "").lower()
            yield participant, purpose, participant_name, session, media_type


def iter_segments(conv: Dict[str, Any]):
    for participant, purpose, participant_name, session, media_type in iter_sessions(conv):
        for segment in session.get("segments", []) or []:
            segment_type = (segment.get("segmentType") or "").lower()
            yield participant, purpose, participant_name, session, media_type, segment, segment_type


def conversation_has_voice(conv: Dict[str, Any]) -> bool:
    return any(media_type == "voice" for *_prefix, media_type in iter_sessions(conv))


def extract_queue_name_from_acd(participant: Dict[str, Any], session: Dict[str, Any]) -> Optional[str]:
    """
    Tries several common Genesys locations for queue name.
    """
    candidates = [
        participant.get("queueName"),
        participant.get("participantName"),
        participant.get("name"),
        session.get("queueName"),
        session.get("dnisName"),
    ]

    # Some analytics payloads store queue under nested queue fields.
    queue_obj = participant.get("queue") or session.get("queue")
    if isinstance(queue_obj, dict):
        candidates.extend([queue_obj.get("name"), queue_obj.get("queueName")])

    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def extract_queue_id_from_acd(participant: Dict[str, Any], session: Dict[str, Any]) -> Optional[str]:
    candidates = [
        participant.get("queueId"),
        session.get("queueId"),
    ]

    queue_obj = participant.get("queue") or session.get("queue")
    if isinstance(queue_obj, dict):
        candidates.extend([queue_obj.get("id"), queue_obj.get("queueId")])

    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def first_acd_queue_entry(conv: Dict[str, Any]) -> Tuple[Optional[datetime], Optional[str], Optional[str]]:
    """
    Returns earliest queue-enter timestamp from an ACD participant/session.

    This intentionally uses segmentStart from the ACD participant as the queue-enter source
    instead of relying on segmentType == delay.
    """
    queue_enter: Optional[datetime] = None
    queue_name: Optional[str] = None
    queue_id: Optional[str] = None

    for participant, purpose, _pname, session, media_type, segment, _segment_type in iter_segments(conv):
        if purpose != "acd" or media_type != "voice":
            continue

        start_dt = parse_dt(segment.get("segmentStart"))
        if not start_dt:
            continue

        if queue_enter is None or start_dt < queue_enter:
            queue_enter = start_dt
            queue_name = extract_queue_name_from_acd(participant, session)
            queue_id = extract_queue_id_from_acd(participant, session)

    return queue_enter, queue_id, queue_name


def first_agent_connect(conv: Dict[str, Any]) -> Optional[datetime]:
    """
    Returns earliest voice agent interact segment start.
    This is treated as the answer/connect timestamp.
    """
    connected: Optional[datetime] = None

    for _participant, purpose, _pname, _session, media_type, segment, segment_type in iter_segments(conv):
        if purpose != "agent" or media_type != "voice":
            continue
        if segment_type != "interact":
            continue

        start_dt = parse_dt(segment.get("segmentStart"))
        connected = min_dt(connected, start_dt)

    return connected


def is_answered_voice(conv: Dict[str, Any]) -> bool:
    return first_agent_connect(conv) is not None


def extract_asa_seconds(conv: Dict[str, Any]) -> Optional[float]:
    """
    ASA = first agent interact start - first ACD queue enter.

    This matches the operational definition:
      "time it connected to an agent minus the time it entered the queue"
    """
    if get_direction(conv) != "inbound":
        return None
    if not conversation_has_voice(conv):
        return None

    queue_enter, _qid, _qname = first_acd_queue_entry(conv)
    agent_connect = first_agent_connect(conv)

    if not queue_enter or not agent_connect:
        return None

    asa = (agent_connect - queue_enter).total_seconds()
    if asa < 0:
        return None

    return asa


def extract_talk_hold_acw(conv: Dict[str, Any]) -> Tuple[list[float], list[float], list[float]]:
    talk: list[float] = []
    hold: list[float] = []
    acw: list[float] = []

    for _participant, purpose, _pname, _session, media_type, segment, segment_type in iter_segments(conv):
        if media_type != "voice":
            continue

        duration = seconds_between(segment.get("segmentStart"), segment.get("segmentEnd"))
        if duration is None:
            continue

        # Keep the prior metric approach: agent interact = talk, hold = hold, wrapup = ACW.
        if purpose == "agent" and segment_type == "interact":
            talk.append(duration)
        elif segment_type == "hold":
            hold.append(duration)
        elif segment_type in ("wrapup", "wrapupcode"):
            acw.append(duration)

    return talk, hold, acw


def classify_conversation(conv: Dict[str, Any]) -> Dict[str, Any]:
    direction = get_direction(conv)
    has_voice = conversation_has_voice(conv)
    answered = is_answered_voice(conv) if has_voice else False
    queue_enter, queue_id, queue_name = first_acd_queue_entry(conv)

    asa = extract_asa_seconds(conv)
    talk, hold, acw = extract_talk_hold_acw(conv)

    return {
        "direction": direction,
        "has_voice": has_voice,
        "answered": answered,
        "queue_enter": queue_enter,
        "queue_id": queue_id,
        "queue_name": queue_name,
        "asa": asa,
        "talk": talk,
        "hold": hold,
        "acw": acw,
    }


# -----------------------------
# DB reads/writes
# -----------------------------

def ensure_rollup_table(conn) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS queue_daily_metrics (
        metric_date DATE NOT NULL,
        queue_id VARCHAR(128) NOT NULL,
        queue_name VARCHAR(255) NULL,

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

        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

        PRIMARY KEY (metric_date, queue_id),
        INDEX idx_queue_daily_metrics_queue_name (queue_name),
        INDEX idx_queue_daily_metrics_metric_date (metric_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    with conn.cursor() as cur:
        cur.execute(sql)


def detect_gc_conversation_columns(conn) -> Dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SHOW COLUMNS FROM gc_conversations")
        columns = [row["Field"] for row in cur.fetchall()]

    lower = {c.lower(): c for c in columns}

    json_col = None
    for candidate in ("raw_json", "conversation_json", "json", "payload", "raw_conversation", "conversation"):
        if candidate in lower:
            json_col = lower[candidate]
            break

    if not json_col:
        # Fall back to first JSON-ish/text-ish column name.
        for c in columns:
            lc = c.lower()
            if "json" in lc or "payload" in lc or "conversation" in lc:
                json_col = c
                break

    if not json_col:
        raise RuntimeError(
            "Could not detect JSON column in gc_conversations. "
            "Expected one of raw_json, conversation_json, json, payload, raw_conversation, conversation."
        )

    start_col = None
    for candidate in ("conversation_start", "conversationStart", "start_time", "startTime", "created_at"):
        if candidate.lower() in lower:
            start_col = lower[candidate.lower()]
            break

    id_col = None
    for candidate in ("conversation_id", "conversationId", "id"):
        if candidate.lower() in lower:
            id_col = lower[candidate.lower()]
            break

    return {"json_col": json_col, "start_col": start_col or "", "id_col": id_col or ""}


def fetch_conversations(conn, days_back: int) -> list[Dict[str, Any]]:
    cols = detect_gc_conversation_columns(conn)
    json_col = cols["json_col"]
    start_col = cols["start_col"]
    id_col = cols["id_col"]

    select_cols = [f"`{json_col}` AS raw_payload"]
    if start_col:
        select_cols.append(f"`{start_col}` AS fallback_start")
    if id_col:
        select_cols.append(f"`{id_col}` AS conversation_id")

    where = ""
    params = []

    if start_col:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        where = f"WHERE `{start_col}` >= %s"
        params.append(cutoff.replace(tzinfo=None))

    sql = f"""
        SELECT {", ".join(select_cols)}
        FROM gc_conversations
        {where}
    """

    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def delete_existing_rollups(conn, start_date: datetime.date, end_date: datetime.date) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM queue_daily_metrics
            WHERE metric_date >= %s AND metric_date <= %s
            """,
            (start_date, end_date),
        )


def upsert_rollup_rows(conn, rollups: Dict[Tuple[str, str, str], QueueMetricAccumulator]) -> None:
    sql = """
    INSERT INTO queue_daily_metrics (
        metric_date, queue_id, queue_name,
        offered_calls, outbound_calls, answered_calls, abandoned_calls, abandon_pct,
        avg_asa_sec, avg_talk_sec, avg_hold_sec, avg_acw_sec,
        raw_conversations
    )
    VALUES (
        %(metric_date)s, %(queue_id)s, %(queue_name)s,
        %(offered_calls)s, %(outbound_calls)s, %(answered_calls)s, %(abandoned_calls)s, %(abandon_pct)s,
        %(avg_asa_sec)s, %(avg_talk_sec)s, %(avg_hold_sec)s, %(avg_acw_sec)s,
        %(raw_conversations)s
    )
    ON DUPLICATE KEY UPDATE
        queue_name = VALUES(queue_name),
        offered_calls = VALUES(offered_calls),
        outbound_calls = VALUES(outbound_calls),
        answered_calls = VALUES(answered_calls),
        abandoned_calls = VALUES(abandoned_calls),
        abandon_pct = VALUES(abandon_pct),
        avg_asa_sec = VALUES(avg_asa_sec),
        avg_talk_sec = VALUES(avg_talk_sec),
        avg_hold_sec = VALUES(avg_hold_sec),
        avg_acw_sec = VALUES(avg_acw_sec),
        raw_conversations = VALUES(raw_conversations),
        updated_at = CURRENT_TIMESTAMP
    """

    rows = []
    for (metric_date, queue_id, queue_name), acc in sorted(rollups.items()):
        db_row = acc.as_db_row()
        db_row.update({
            "metric_date": metric_date,
            "queue_id": queue_id,
            "queue_name": queue_name,
        })
        rows.append(db_row)

    if not rows:
        return

    with conn.cursor() as cur:
        cur.executemany(sql, rows)


def resolve_queue_identity(metric: Dict[str, Any]) -> Tuple[str, str]:
    queue_id = metric.get("queue_id")
    queue_name = metric.get("queue_name")

    if not queue_id and queue_name:
        queue_id = f"name:{queue_name}"

    if not queue_name and queue_id:
        queue_name = queue_id

    if not queue_id:
        queue_id = "unknown_queue"
    if not queue_name:
        queue_name = "Unknown Queue"

    return str(queue_id), str(queue_name)


def build_rollups(rows: list[Dict[str, Any]], debug_sample: int = 0):
    rollups: Dict[Tuple[str, str, str], QueueMetricAccumulator] = defaultdict(QueueMetricAccumulator)
    asa_debug = []
    skipped_json = 0
    skipped_no_date = 0
    skipped_no_queue = 0

    for row in rows:
        conv = safe_json_loads(row.get("raw_payload"))
        if not conv:
            skipped_json += 1
            continue

        conv_start = get_conversation_start(conv, row.get("fallback_start"))
        if not conv_start:
            skipped_no_date += 1
            continue

        metric_date = conv_start.date().isoformat()
        metric = classify_conversation(conv)

        # This rollup is queue perspective, so queue identity comes from ACD.
        queue_id, queue_name = resolve_queue_identity(metric)

        if queue_id == "unknown_queue":
            skipped_no_queue += 1
            continue

        key = (metric_date, queue_id, queue_name)
        acc = rollups[key]
        acc.raw_conversations += 1

        direction = metric["direction"]

        if direction == "inbound" and metric["has_voice"]:
            acc.offered_calls += 1
            if metric["answered"]:
                acc.answered_calls += 1
            else:
                acc.abandoned_calls += 1

        elif direction == "outbound" and metric["has_voice"]:
            acc.outbound_calls += 1

        if metric["asa"] is not None:
            acc.asa_values.append(metric["asa"])

            if debug_sample and len(asa_debug) < debug_sample:
                asa_debug.append({
                    "conversation_id": row.get("conversation_id"),
                    "metric_date": metric_date,
                    "queue_name": queue_name,
                    "asa_sec": metric["asa"],
                    "queue_enter": metric["queue_enter"].isoformat() if metric["queue_enter"] else None,
                    "agent_connect": first_agent_connect(conv).isoformat() if first_agent_connect(conv) else None,
                })

        acc.talk_values.extend(metric["talk"])
        acc.hold_values.extend(metric["hold"])
        acc.acw_values.extend(metric["acw"])

    stats = {
        "input_rows": len(rows),
        "rollup_rows": len(rollups),
        "skipped_json": skipped_json,
        "skipped_no_date": skipped_no_date,
        "skipped_no_queue": skipped_no_queue,
        "asa_debug": asa_debug,
        "asa_value_count": sum(len(acc.asa_values) for acc in rollups.values()),
    }

    return rollups, stats


# -----------------------------
# CLI
# -----------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build queue_daily_metrics rollups with timestamp-based ASA calculation."
    )
    parser.add_argument("--days-back", type=int, default=7, help="Number of days back to rebuild.")
    parser.add_argument("--apply", action="store_true", help="Write changes to MySQL. Without this, dry-run only.")
    parser.add_argument("--debug-sample", type=int, default=10, help="Print sample ASA calculations.")
    parser.add_argument("--no-delete", action="store_true", help="Do not delete existing rollup rows before upsert.")
    args = parser.parse_args()

    conn = get_db_connection()

    try:
        ensure_rollup_table(conn)
        rows = fetch_conversations(conn, args.days_back)
        rollups, stats = build_rollups(rows, debug_sample=args.debug_sample)

        print("Queue daily metrics build")
        print(f"  Days back:        {args.days_back}")
        print(f"  Source rows:      {stats['input_rows']}")
        print(f"  Rollup rows:      {stats['rollup_rows']}")
        print(f"  ASA values found: {stats['asa_value_count']}")
        print(f"  Skipped JSON:     {stats['skipped_json']}")
        print(f"  Skipped no date:  {stats['skipped_no_date']}")
        print(f"  Skipped no queue: {stats['skipped_no_queue']}")

        if stats["asa_debug"]:
            print("\nSample ASA calculations:")
            for item in stats["asa_debug"]:
                print(
                    f"  {item['queue_name']} | ASA={item['asa_sec']:.1f}s | "
                    f"queue_enter={item['queue_enter']} | agent_connect={item['agent_connect']} | "
                    f"conversation={item['conversation_id']}"
                )

        if not args.apply:
            print("\nDRY RUN ONLY. Re-run with --apply to write queue_daily_metrics.")
            conn.rollback()
            return 0

        today = datetime.now(timezone.utc).date()
        start_date = today - timedelta(days=args.days_back)

        if not args.no_delete:
            delete_existing_rollups(conn, start_date, today)

        upsert_rollup_rows(conn, rollups)
        conn.commit()

        print("\nApplied successfully.")
        print("\nValidation SQL:")
        print(
            """
SELECT
    queue_name,
    ROUND(AVG(avg_asa_sec), 1) AS avg_asa_sec,
    SUM(offered_calls) AS offered_calls,
    SUM(answered_calls) AS answered_calls
FROM queue_daily_metrics
WHERE metric_date >= CURDATE() - INTERVAL 7 DAY
GROUP BY queue_name
ORDER BY avg_asa_sec DESC;
""".strip()
        )

        return 0

    except Exception as exc:
        conn.rollback()
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
