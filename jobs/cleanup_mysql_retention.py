#!/usr/bin/env python3
"""
Cleanup MySQL data by table-specific retention windows.

Defaults:
    queue_daily_metrics: 1095 days / 3 years
    gc_conversations:    180 days

Dry run:
    python tools\cleanup_mysql_retention.py

Apply:
    python tools\cleanup_mysql_retention.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

try:
    import pymysql
except ImportError:
    print("Missing dependency: pymysql. Install with: pip install pymysql")
    sys.exit(1)


def load_client_env() -> None:
    for env_path in [Path.cwd() / "client.env", Path(__file__).resolve().parent.parent / "client.env"]:
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return


def env_first(*names: str, default: Optional[str] = None) -> Optional[str]:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return default


def get_conn():
    load_client_env()
    return pymysql.connect(
        host=env_first("MYSQL_HOST", "DB_HOST", default="127.0.0.1"),
        port=int(env_first("MYSQL_PORT", "DB_PORT", default="3306") or 3306),
        user=env_first("MYSQL_USER", "DB_USER", default="root"),
        password=env_first("MYSQL_PASSWORD", "DB_PASSWORD", default="password"),
        database=env_first("MYSQL_DATABASE", "DB_NAME", default="genesys"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) AS n
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_name = %s
        """, (table_name,))
        row = cur.fetchone()
        return bool(row and row["n"])


def columns(conn, table_name: str) -> set[str]:
    if not table_exists(conn, table_name):
        return set()
    with conn.cursor() as cur:
        cur.execute(f"SHOW COLUMNS FROM `{table_name}`")
        return {r["Field"] for r in cur.fetchall()}


def pick_column(existing: set[str], candidates: list[str]) -> Optional[str]:
    lower = {c.lower(): c for c in existing}
    for candidate in candidates:
        if candidate.lower() in lower:
            return lower[candidate.lower()]
    return None


def count_old_rows(conn, table: str, date_col: str, days: int) -> int:
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT COUNT(*) AS n
            FROM `{table}`
            WHERE `{date_col}` < DATE_SUB(CURDATE(), INTERVAL %s DAY)
        """, (days,))
        row = cur.fetchone()
        return int(row["n"] or 0)


def delete_old_rows(conn, table: str, date_col: str, days: int) -> int:
    with conn.cursor() as cur:
        cur.execute(f"""
            DELETE FROM `{table}`
            WHERE `{date_col}` < DATE_SUB(CURDATE(), INTERVAL %s DAY)
        """, (days,))
        return cur.rowcount


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--raw-days", type=int, default=180)
    parser.add_argument("--rollup-days", type=int, default=1095)
    args = parser.parse_args()

    targets = {
        "queue_daily_metrics": {
            "days": args.rollup_days,
            "date_columns": ["metric_date", "calculated_at", "updated_at"],
        },
        "gc_conversations": {
            "days": args.raw_days,
            "date_columns": ["conversation_start", "conversationStart", "start_time", "startTime", "created_at"],
        },
    }

    conn = get_conn()
    try:
        print("MySQL retention cleanup")
        print(f"  Mode: {'APPLY' if args.apply else 'DRY RUN'}")
        print(f"  queue_daily_metrics retention: {args.rollup_days} days")
        print(f"  gc_conversations retention: {args.raw_days} days")
        print("")

        total_to_delete = 0
        total_deleted = 0

        for table, cfg in targets.items():
            if not table_exists(conn, table):
                print(f"Skipping {table}: table not found.")
                continue

            existing_cols = columns(conn, table)
            date_col = pick_column(existing_cols, cfg["date_columns"])

            if not date_col:
                print(f"Skipping {table}: no recognized date column.")
                continue

            count = count_old_rows(conn, table, date_col, cfg["days"])
            total_to_delete += count

            print(f"{table}")
            print(f"  Date column: {date_col}")
            print(f"  Retention days: {cfg['days']}")
            print(f"  Rows older than retention: {count}")

            if args.apply and count:
                deleted = delete_old_rows(conn, table, date_col, cfg["days"])
                total_deleted += deleted
                print(f"  Deleted: {deleted}")
            print("")

        if args.apply:
            conn.commit()
            print(f"Applied successfully. Total rows deleted: {total_deleted}")
        else:
            conn.rollback()
            print(f"Dry run only. Total rows that would be deleted: {total_to_delete}")

        return 0

    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
