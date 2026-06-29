#!/usr/bin/env python3
"""
backfill_queue_daily_metrics_divisions.py

Backfills queue_daily_metrics.division_name from queue metadata.

Primary purpose:
    Restore/support the Division dropdown in the Queue Perspective dashboard.

Default behavior:
    - Uses shared db.py SQLAlchemy engine
    - Adds queue_daily_metrics.division_name if missing
    - Detects gc_queues column names where possible
    - Updates queue_daily_metrics.division_name by queue_id first, then queue_name
    - Safe to run repeatedly

Run:
    python tools\backfill_queue_daily_metrics_divisions.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db import get_engine  # noqa: E402


def fetch_columns(conn, table_name: str) -> set[str]:
    rows = conn.execute(text(f"SHOW COLUMNS FROM `{table_name}`")).mappings().all()
    return {row["Field"] for row in rows}


def table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        text(
            """
            SELECT COUNT(*) AS n
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
            """
        ),
        {"table_name": table_name},
    ).mappings().first()

    return bool(row and row["n"])


def pick_column(columns: set[str], candidates: list[str]) -> str | None:
    lowered = {col.lower(): col for col in columns}

    for candidate in candidates:
        found = lowered.get(candidate.lower())
        if found:
            return found

    return None


def discover_queue_source(conn) -> dict[str, str] | None:
    """
    Finds a queue metadata table that has division data.

    Expected/common tables:
        gc_queues
        queues
        genesys_queues
        gc_queue
    """
    possible_tables = [
        "gc_queues",
        "queues",
        "genesys_queues",
        "gc_queue",
    ]

    division_candidates = [
        "division_name",
        "divisionName",
        "division",
        "division_display_name",
        "divisionDisplayName",
    ]

    queue_id_candidates = [
        "queue_id",
        "queueId",
        "id",
        "genesys_queue_id",
    ]

    queue_name_candidates = [
        "queue_name",
        "queueName",
        "name",
    ]

    for table_name in possible_tables:
        if not table_exists(conn, table_name):
            continue

        cols = fetch_columns(conn, table_name)

        division_col = pick_column(cols, division_candidates)
        queue_id_col = pick_column(cols, queue_id_candidates)
        queue_name_col = pick_column(cols, queue_name_candidates)

        if division_col and (queue_id_col or queue_name_col):
            return {
                "table": table_name,
                "division_col": division_col,
                "queue_id_col": queue_id_col or "",
                "queue_name_col": queue_name_col or "",
            }

    return None


def ensure_division_column(conn) -> None:
    if not table_exists(conn, "queue_daily_metrics"):
        raise RuntimeError("queue_daily_metrics table not found.")

    cols = fetch_columns(conn, "queue_daily_metrics")

    if "division_name" not in cols:
        print("Adding queue_daily_metrics.division_name...")
        conn.execute(
            text(
                """
                ALTER TABLE queue_daily_metrics
                ADD COLUMN division_name VARCHAR(255) NULL AFTER queue_name
                """
            )
        )
    else:
        print("queue_daily_metrics.division_name already exists.")


def update_by_queue_id(conn, source: dict[str, str]) -> int:
    table = source["table"]
    division_col = source["division_col"]
    src_queue_id_col = source["queue_id_col"]

    if not src_queue_id_col:
        return 0

    qdm_cols = fetch_columns(conn, "queue_daily_metrics")
    qdm_queue_id_col = pick_column(qdm_cols, ["queue_id", "queueId"])

    if not qdm_queue_id_col:
        return 0

    result = conn.execute(
        text(
            f"""
            UPDATE queue_daily_metrics qdm
            JOIN `{table}` src
              ON src.`{src_queue_id_col}` = qdm.`{qdm_queue_id_col}`
            SET qdm.division_name = src.`{division_col}`
            WHERE src.`{division_col}` IS NOT NULL
              AND src.`{division_col}` <> ''
              AND (qdm.division_name IS NULL OR qdm.division_name = '')
            """
        )
    )

    return int(result.rowcount or 0)


def update_by_queue_name(conn, source: dict[str, str]) -> int:
    table = source["table"]
    division_col = source["division_col"]
    src_queue_name_col = source["queue_name_col"]

    if not src_queue_name_col:
        return 0

    qdm_cols = fetch_columns(conn, "queue_daily_metrics")
    qdm_queue_name_col = pick_column(qdm_cols, ["queue_name", "queueName", "name"])

    if not qdm_queue_name_col:
        return 0

    result = conn.execute(
        text(
            f"""
            UPDATE queue_daily_metrics qdm
            JOIN `{table}` src
              ON src.`{src_queue_name_col}` = qdm.`{qdm_queue_name_col}`
            SET qdm.division_name = src.`{division_col}`
            WHERE src.`{division_col}` IS NOT NULL
              AND src.`{division_col}` <> ''
              AND (qdm.division_name IS NULL OR qdm.division_name = '')
            """
        )
    )

    return int(result.rowcount or 0)


def print_division_summary(conn) -> None:
    row = conn.execute(
        text(
            """
            SELECT COUNT(DISTINCT division_name) AS division_count
            FROM queue_daily_metrics
            WHERE division_name IS NOT NULL
              AND division_name <> ''
            """
        )
    ).mappings().first()

    division_count = row["division_count"] if row else 0
    print(f"Distinct divisions in queue_daily_metrics: {division_count}")

    rows = conn.execute(
        text(
            """
            SELECT DISTINCT division_name
            FROM queue_daily_metrics
            WHERE division_name IS NOT NULL
              AND division_name <> ''
            ORDER BY division_name
            LIMIT 25
            """
        )
    ).mappings().all()

    if rows:
        print("Sample divisions:")
        for item in rows:
            print(f"  - {item['division_name']}")


def main() -> int:
    engine = get_engine()

    with engine.begin() as conn:
        print("Backfilling division_name in queue_daily_metrics...")

        ensure_division_column(conn)

        source = discover_queue_source(conn)

        if not source:
            print("")
            print("ERROR: Could not find a queue metadata table with division data.")
            print("")
            print("Expected one of these to exist with division information:")
            print("  - gc_queues.division_name")
            print("  - gc_queues.divisionName")
            print("  - queues.division_name")
            print("")
            print("Run these in MySQL if we need to inspect the schema:")
            print("  SHOW TABLES;")
            print("  DESCRIBE gc_queues;")
            print("  DESCRIBE queue_daily_metrics;")
            return 2

        print("Detected queue metadata source:")
        print(f"  Table:        {source['table']}")
        print(f"  Division col: {source['division_col']}")
        print(f"  Queue ID col: {source['queue_id_col'] or 'N/A'}")
        print(f"  Queue name:   {source['queue_name_col'] or 'N/A'}")

        updated_by_id = update_by_queue_id(conn, source)
        updated_by_name = update_by_queue_name(conn, source)

        print("")
        print(f"Rows updated by queue_id:   {updated_by_id}")
        print(f"Rows updated by queue_name: {updated_by_name}")
        print(f"Total rows updated:         {updated_by_id + updated_by_name}")
        print("")

        print_division_summary(conn)

    print("")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
