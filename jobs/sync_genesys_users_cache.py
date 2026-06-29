#!/usr/bin/env python3
"""
sync_genesys_users_cache.py

Pulls Genesys users into a local dimension/cache table so dashboards do not
need to resolve names through repeated API calls.

Creates/updates:
    gc_users

Run:
    python tools\sync_genesys_users_cache.py --apply
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
        CREATE TABLE IF NOT EXISTS gc_users (
            user_id VARCHAR(128) NOT NULL PRIMARY KEY,
            name VARCHAR(255) NULL,
            email VARCHAR(255) NULL,
            state VARCHAR(64) NULL,
            title VARCHAR(255) NULL,
            department VARCHAR(255) NULL,
            raw_json JSON NULL,
            synced_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_gc_users_name (name),
            INDEX idx_gc_users_email (email)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """))


def fetch_users():
    token = get_access_token()
    page = 1
    page_size = 100
    users = []

    while True:
        data = genesys_get(
            "/api/v2/users",
            token,
            params={
                "pageSize": page_size,
                "pageNumber": page,
                "state": "any",
                "expand": "authorization,profileSkills,locations",
            },
        )

        entities = data.get("entities") or []
        users.extend(entities)

        page_count = data.get("pageCount")
        if page_count is not None:
            if page >= int(page_count):
                break
        elif not entities or len(entities) < page_size:
            break

        page += 1

    return users


def user_title(user):
    # Genesys profile shape can vary. Keep this deliberately forgiving.
    title = user.get("title")
    if title:
        return title

    profile = user.get("profile") or {}
    if isinstance(profile, dict):
        for key in ("title", "jobTitle"):
            if profile.get(key):
                return profile.get(key)

    return None


def user_department(user):
    profile = user.get("profile") or {}
    if isinstance(profile, dict):
        for key in ("department", "division"):
            if profile.get(key):
                return profile.get(key)
    return None


def upsert_users(conn, users):
    import json

    sql = text("""
        INSERT INTO gc_users (
            user_id, name, email, state, title, department, raw_json
        )
        VALUES (
            :user_id, :name, :email, :state, :title, :department, :raw_json
        )
        ON DUPLICATE KEY UPDATE
            name=VALUES(name),
            email=VALUES(email),
            state=VALUES(state),
            title=VALUES(title),
            department=VALUES(department),
            raw_json=VALUES(raw_json),
            synced_at=CURRENT_TIMESTAMP
    """)

    payload = []
    for u in users:
        user_id = u.get("id")
        if not user_id:
            continue

        payload.append({
            "user_id": user_id,
            "name": u.get("name"),
            "email": u.get("email"),
            "state": u.get("state"),
            "title": user_title(u),
            "department": user_department(u),
            "raw_json": json.dumps(u, default=str),
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

        print("Fetching Genesys users...")
        users = fetch_users()
        print(f"Users fetched: {len(users)}")

        if args.apply:
            written = upsert_users(conn, users)
            print(f"Users written to gc_users: {written}")
            print("Applied successfully.")
        else:
            print("Dry run only. Re-run with --apply.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
