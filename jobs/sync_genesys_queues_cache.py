#!/usr/bin/env python3
"""
sync_genesys_queues_cache.py

Pulls Genesys queues into local gc_queues cache/dimension table.

Creates/updates:
    gc_queues

Run:
    python tools\sync_genesys_queues_cache.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from db import get_engine  # noqa: E402
from core.genesys_api_cache_helpers import get_access_token, genesys_get  # noqa: E402


def ensure_table(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS gc_queues (
            queue_id VARCHAR(128) NOT NULL PRIMARY KEY,
            queue_name VARCHAR(255) NULL,
            division_id VARCHAR(128) NULL,
            division_name VARCHAR(255) NULL,
            member_count INT NULL,
            raw_json JSON NULL,
            synced_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_gc_queues_name (queue_name),
            INDEX idx_gc_queues_division_name (division_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def fetch_queues():
    token = get_access_token()
    page = 1
    page_size = 100
    queues = []

    while True:
        data = genesys_get(
            "/api/v2/routing/queues",
            token,
            params={
                "pageSize": page_size,
                "pageNumber": page,
                "sortBy": "name",
                "expand": "division",
            },
        )

        entities = data.get("entities") or []
        queues.extend(entities)

        page_count = data.get("pageCount")
        if page_count is not None:
            if page >= int(page_count):
                break
        elif not entities or len(entities) < page_size:
            break

        page += 1

    return queues


def upsert_queues(conn, queues):
    import json

    sql = text("""
        INSERT INTO gc_queues (
            queue_id, queue_name, division_id, division_name, member_count, raw_json
        )
        VALUES (
            :queue_id, :queue_name, :division_id, :division_name, :member_count, :raw_json
        )
        ON DUPLICATE KEY UPDATE
            queue_name=VALUES(queue_name),
            division_id=VALUES(division_id),
            division_name=VALUES(division_name),
            member_count=VALUES(member_count),
            raw_json=VALUES(raw_json),
            synced_at=CURRENT_TIMESTAMP
    """)

    payload = []
    for q in queues:
        queue_id = q.get("id")
        if not queue_id:
            continue

        division = q.get("division") or {}
        if not isinstance(division, dict):
            division = {}

        payload.append({
            "queue_id": queue_id,
            "queue_name": q.get("name"),
            "division_id": division.get("id"),
            "division_name": division.get("name"),
            "member_count": q.get("memberCount"),
            "raw_json": json.dumps(q, default=str),
        })

    if payload:
        conn.execute(sql, payload)

    return len(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    engine = get_engine()

    with engine.begin() as conn:
        ensure_table(conn)

        print("Fetching Genesys queues...")
        queues = fetch_queues()
        print(f"Queues fetched: {len(queues)}")

        if args.apply:
            written = upsert_queues(conn, queues)
            print(f"Queues written to gc_queues: {written}")
            print("Applied successfully.")
        else:
            print("Dry run only. Re-run with --apply.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
