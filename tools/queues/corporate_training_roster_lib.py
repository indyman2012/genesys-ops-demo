"""Corporate training roster workflows (Genesys queue membership)."""
from __future__ import annotations

import re
from io import BytesIO
from typing import Any, BinaryIO

from openpyxl import load_workbook

from core.genesys_client import GenesysClient, safe_str
from core.lookups import get_all_queues
from tools.queues.user_name_resolve import (
    build_user_indexes,
    normalize_person_name,
    resolve_user,
)

TRAINING_NAME_FRAGMENT = "corporate training"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

RESULT_FIELDS = [
    "input_name",
    "user_id",
    "user_name",
    "user_email",
    "status",
    "training_queues",
    "production_queues",
    "actions",
    "detail",
]


def is_corporate_training_queue_name(name: str) -> bool:
    return TRAINING_NAME_FRAGMENT in safe_str(name).lower()


def list_corporate_training_queues(client: GenesysClient) -> list[dict[str, Any]]:
    queues = [
        q
        for q in get_all_queues(client)
        if is_corporate_training_queue_name(safe_str(q.get("name")))
    ]
    queues.sort(key=lambda row: safe_str(row.get("name")).lower())
    return queues


def _training_queue_ids(queues: list[dict[str, Any]]) -> set[str]:
    return {safe_str(q["id"]) for q in queues if q.get("id")}


def parse_names_from_workbook(source: BinaryIO | bytes) -> list[str]:
    data = source.read() if hasattr(source, "read") else source
    wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []

    header = [safe_str(cell).strip().lower() for cell in rows[0]]
    name_col = 0
    for idx, label in enumerate(header):
        if label in {"name", "agent", "agents", "employee", "email"} or "name" in label:
            name_col = idx
            break

    seen: set[str] = set()
    names: list[str] = []
    start = 1 if any(header) else 0
    for row in rows[start:]:
        if not row or name_col >= len(row):
            continue
        raw = safe_str(row[name_col]).strip()
        if not raw or raw.lower() in {"name", "agent", "email"}:
            continue
        for piece in re.split(r"\s+and\s+", raw, flags=re.IGNORECASE):
            normalized = normalize_person_name(piece)
            if not normalized:
                continue
            key = normalized.lower()
            if key not in seen:
                seen.add(key)
                names.append(normalized)
    return names


def _user_queue_rows(client: GenesysClient, user_id: str) -> list[dict[str, Any]]:
    return client.paged_get(f"/api/v2/users/{user_id}/queues")


def _split_memberships(
    memberships: list[dict[str, Any]],
    training_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    training: list[dict[str, Any]] = []
    production: list[dict[str, Any]] = []
    for row in memberships:
        qid = safe_str(row.get("id"))
        if not qid:
            continue
        if qid in training_ids:
            training.append(row)
        else:
            production.append(row)
    return training, production


def _queue_label(row: dict[str, Any]) -> str:
    name = safe_str(row.get("name"))
    joined = bool(row.get("joined"))
    suffix = " (joined)" if joined else ""
    return f"{name}{suffix}"


def _resolve_input_name(
    client: GenesysClient,
    input_name: str,
    *,
    users: list[dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, str]:
    user, _method, detail = resolve_user(
        input_name,
        client=client,
        users=users,
        by_name=by_name,
    )
    if user:
        return user, detail
    return None, detail or "user not found"


def run_start_training(
    client: GenesysClient,
    names: list[str],
    *,
    apply: bool,
    training_queues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify training queue membership; set joined=false on production queues."""
    training_queues = training_queues if training_queues is not None else list_corporate_training_queues(client)
    training_ids = _training_queue_ids(training_queues)
    users, by_name = build_user_indexes(client)

    rows: list[dict[str, Any]] = []
    stats = {"ok": 0, "error": 0, "production_joined_cleared": 0, "would_clear": 0}

    for input_name in names:
        user, resolve_detail = _resolve_input_name(
            client, input_name, users=users, by_name=by_name
        )
        if not user:
            rows.append(
                {
                    "input_name": input_name,
                    "user_id": "",
                    "user_name": "",
                    "user_email": "",
                    "status": "error",
                    "training_queues": "",
                    "production_queues": "",
                    "actions": "",
                    "detail": resolve_detail,
                }
            )
            stats["error"] += 1
            continue

        uid = safe_str(user.get("id"))
        memberships = _user_queue_rows(client, uid)
        training, production = _split_memberships(memberships, training_ids)

        if not training:
            rows.append(
                {
                    "input_name": input_name,
                    "user_id": uid,
                    "user_name": safe_str(user.get("name")),
                    "user_email": safe_str(user.get("email")),
                    "status": "error",
                    "training_queues": "",
                    "production_queues": "; ".join(_queue_label(q) for q in production),
                    "actions": "",
                    "detail": "Not a member of any corporate training queue",
                }
            )
            stats["error"] += 1
            continue

        actions: list[str] = []
        for prod in production:
            if not prod.get("joined"):
                continue
            qid = safe_str(prod.get("id"))
            qname = safe_str(prod.get("name"))
            if apply:
                client.request(
                    "PATCH",
                    f"/api/v2/users/{uid}/queues/{qid}",
                    json_body={"joined": False},
                )
                actions.append(f"joined=false on {qname}")
                stats["production_joined_cleared"] += 1
            else:
                actions.append(f"would set joined=false on {qname}")
                stats["would_clear"] += 1

        rows.append(
            {
                "input_name": input_name,
                "user_id": uid,
                "user_name": safe_str(user.get("name")),
                "user_email": safe_str(user.get("email")),
                "status": "ok",
                "training_queues": "; ".join(_queue_label(q) for q in training),
                "production_queues": "; ".join(_queue_label(q) for q in production),
                "actions": "; ".join(actions) if actions else "(no joined production queues)",
                "detail": resolve_detail,
            }
        )
        stats["ok"] += 1

    return {
        "mode": "start_training",
        "apply": apply,
        "training_queue_count": len(training_queues),
        "training_queues": [safe_str(q.get("name")) for q in training_queues],
        "input_count": len(names),
        "stats": stats,
        "rows": rows,
    }


def run_return_from_training(
    client: GenesysClient,
    names: list[str],
    *,
    apply: bool,
    training_queues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Remove from corporate training queues; set joined=true on production queues."""
    training_queues = training_queues if training_queues is not None else list_corporate_training_queues(client)
    training_ids = _training_queue_ids(training_queues)
    users, by_name = build_user_indexes(client)

    rows: list[dict[str, Any]] = []
    stats = {
        "ok": 0,
        "error": 0,
        "training_removed": 0,
        "production_joined_set": 0,
        "would_remove": 0,
        "would_join": 0,
    }

    for input_name in names:
        user, resolve_detail = _resolve_input_name(
            client, input_name, users=users, by_name=by_name
        )
        if not user:
            rows.append(
                {
                    "input_name": input_name,
                    "user_id": "",
                    "user_name": "",
                    "user_email": "",
                    "status": "error",
                    "training_queues": "",
                    "production_queues": "",
                    "actions": "",
                    "detail": resolve_detail,
                }
            )
            stats["error"] += 1
            continue

        uid = safe_str(user.get("id"))
        memberships = _user_queue_rows(client, uid)
        training, production = _split_memberships(memberships, training_ids)

        actions: list[str] = []
        for train in training:
            qid = safe_str(train.get("id"))
            qname = safe_str(train.get("name"))
            if apply:
                client.request("DELETE", f"/api/v2/routing/queues/{qid}/members/{uid}")
                actions.append(f"removed from {qname}")
                stats["training_removed"] += 1
            else:
                actions.append(f"would remove from {qname}")
                stats["would_remove"] += 1

        for prod in production:
            qid = safe_str(prod.get("id"))
            qname = safe_str(prod.get("name"))
            if prod.get("joined"):
                continue
            if apply:
                client.request(
                    "PATCH",
                    f"/api/v2/users/{uid}/queues/{qid}",
                    json_body={"joined": True},
                )
                actions.append(f"joined=true on {qname}")
                stats["production_joined_set"] += 1
            else:
                actions.append(f"would set joined=true on {qname}")
                stats["would_join"] += 1

        status = "ok" if training or production else "warn"
        if not training and not production:
            detail = "No training or production queue memberships found"
        else:
            detail = resolve_detail

        rows.append(
            {
                "input_name": input_name,
                "user_id": uid,
                "user_name": safe_str(user.get("name")),
                "user_email": safe_str(user.get("email")),
                "status": status,
                "training_queues": "; ".join(_queue_label(q) for q in training),
                "production_queues": "; ".join(_queue_label(q) for q in production),
                "actions": "; ".join(actions) if actions else "(nothing to change)",
                "detail": detail,
            }
        )
        if status == "error":
            stats["error"] += 1
        else:
            stats["ok"] += 1

    return {
        "mode": "return_from_training",
        "apply": apply,
        "training_queue_count": len(training_queues),
        "training_queues": [safe_str(q.get("name")) for q in training_queues],
        "input_count": len(names),
        "stats": stats,
        "rows": rows,
    }
