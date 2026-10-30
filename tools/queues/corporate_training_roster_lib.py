"""Corporate training roster workflows (Genesys queue membership)."""
from __future__ import annotations

import re
from io import BytesIO
from typing import Any, BinaryIO

from openpyxl import load_workbook

from core.genesys_client import GenesysClient, GenesysError, safe_str
from core.lookups import find_user_by_email, get_all_queues, user_is_queue_member

TRAINING_NAME_FRAGMENT = "corporate training"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

RESULT_FIELDS = [
    "input_email",
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


def normalize_roster_email(value: str) -> str:
    return safe_str(value).strip().lower()


def _split_email_cell(raw: str) -> list[str]:
    parts = re.split(r"[,;\s]+", raw)
    return [normalize_roster_email(p) for p in parts if normalize_roster_email(p)]


def parse_emails_from_workbook(source: BinaryIO | bytes) -> list[str]:
    """Read work emails from the first sheet (column headed Email or first column)."""
    data = source.read() if hasattr(source, "read") else source
    wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []

    header = [safe_str(cell).strip().lower() for cell in rows[0]]
    email_col = 0
    for idx, label in enumerate(header):
        if label in {"email", "e-mail", "work email", "email address", "mail"} or "email" in label:
            email_col = idx
            break

    seen: set[str] = set()
    emails: list[str] = []
    start = 1 if any(header) else 0
    for row in rows[start:]:
        if not row or email_col >= len(row):
            continue
        raw = safe_str(row[email_col]).strip()
        if not raw or raw.lower() in {"email", "e-mail", "work email", "email address"}:
            continue
        for addr in _split_email_cell(raw):
            if addr not in seen:
                seen.add(addr)
                emails.append(addr)
    return emails


def parse_names_from_workbook(source: BinaryIO | bytes) -> list[str]:
    """Backward-compatible alias — roster workbooks are email-based."""
    return parse_emails_from_workbook(source)


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


def _resolve_input_email(client: GenesysClient, input_email: str) -> tuple[dict[str, Any] | None, str]:
    normalized = normalize_roster_email(input_email)
    if not normalized:
        return None, "empty email"
    if not EMAIL_PATTERN.match(normalized):
        return None, f"invalid email address: {input_email}"
    try:
        return find_user_by_email(client, normalized), ""
    except GenesysError as exc:
        return None, str(exc)


def run_start_training(
    client: GenesysClient,
    emails: list[str],
    *,
    apply: bool,
    training_queues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify training queue membership; set joined=false on production queues."""
    training_queues = training_queues if training_queues is not None else list_corporate_training_queues(client)
    training_ids = _training_queue_ids(training_queues)

    rows: list[dict[str, Any]] = []
    stats = {"ok": 0, "error": 0, "production_joined_cleared": 0, "would_clear": 0}

    for input_email in emails:
        user, resolve_detail = _resolve_input_email(client, input_email)
        if not user:
            rows.append(
                {
                    "input_email": input_email,
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
                    "input_email": input_email,
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
                "input_email": input_email,
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
        "input_count": len(emails),
        "stats": stats,
        "rows": rows,
    }


def run_return_from_training(
    client: GenesysClient,
    emails: list[str],
    *,
    apply: bool,
    training_queues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Remove from corporate training queues; set joined=true on production queues."""
    training_queues = training_queues if training_queues is not None else list_corporate_training_queues(client)
    training_ids = _training_queue_ids(training_queues)

    rows: list[dict[str, Any]] = []
    stats = {
        "ok": 0,
        "error": 0,
        "training_removed": 0,
        "production_joined_set": 0,
        "would_remove": 0,
        "would_join": 0,
    }

    for input_email in emails:
        user, resolve_detail = _resolve_input_email(client, input_email)
        if not user:
            rows.append(
                {
                    "input_email": input_email,
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
                "input_email": input_email,
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
        "input_count": len(emails),
        "stats": stats,
        "rows": rows,
    }


def resolve_training_queue(
    client: GenesysClient,
    queue_id: str,
    training_queues: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    training_queues = training_queues if training_queues is not None else list_corporate_training_queues(client)
    wanted = safe_str(queue_id)
    for queue in training_queues:
        if safe_str(queue.get("id")) == wanted:
            return queue
    return None


def _ensure_joined_training_member(
    client: GenesysClient,
    *,
    queue: dict[str, Any],
    user: dict[str, Any],
    apply: bool,
) -> tuple[str, str]:
    queue_id = safe_str(queue.get("id"))
    queue_name = safe_str(queue.get("name"))
    uid = safe_str(user.get("id"))
    member = user_is_queue_member(client, queue_id, uid)
    if member:
        if member.get("joined"):
            return "skipped_already_joined", f"already joined on {queue_name}"
        if not apply:
            return "would_set_joined", f"would set joined=true on {queue_name}"
        client.request(
            "PATCH",
            f"/api/v2/users/{uid}/queues/{queue_id}",
            json_body={"joined": True},
        )
        return "updated_joined", f"joined=true on {queue_name}"

    if not apply:
        return "would_add_member", f"would add to {queue_name} with joined=true"

    client.request(
        "POST",
        f"/api/v2/routing/queues/{queue_id}/members",
        json_body=[{"id": uid}],
        expected_statuses=[200, 201, 202],
    )
    client.request(
        "PATCH",
        f"/api/v2/users/{uid}/queues/{queue_id}",
        json_body={"joined": True},
    )
    return "added_member", f"added to {queue_name} with joined=true"


def run_assign_to_training_queue(
    client: GenesysClient,
    emails: list[str],
    *,
    queue_id: str,
    apply: bool,
    training_queues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Add each agent to the selected corporate training queue with joined=true."""
    training_queues = training_queues if training_queues is not None else list_corporate_training_queues(client)
    target = resolve_training_queue(client, queue_id, training_queues)
    if not target:
        raise GenesysError(f"Not a corporate training queue: {queue_id}")

    target_name = safe_str(target.get("name"))
    target_id = safe_str(target.get("id"))

    rows: list[dict[str, Any]] = []
    stats = {
        "ok": 0,
        "error": 0,
        "added": 0,
        "joined_updated": 0,
        "already_joined": 0,
        "would_add": 0,
        "would_join": 0,
    }

    for input_email in emails:
        user, resolve_detail = _resolve_input_email(client, input_email)
        if not user:
            rows.append(
                {
                    "input_email": input_email,
                    "user_id": "",
                    "user_name": "",
                    "user_email": "",
                    "status": "error",
                    "training_queues": target_name,
                    "production_queues": "",
                    "actions": "",
                    "detail": resolve_detail,
                }
            )
            stats["error"] += 1
            continue

        try:
            action, action_detail = _ensure_joined_training_member(
                client,
                queue=target,
                user=user,
                apply=apply,
            )
        except GenesysError as exc:
            rows.append(
                {
                    "input_email": input_email,
                    "user_id": safe_str(user.get("id")),
                    "user_name": safe_str(user.get("name")),
                    "user_email": safe_str(user.get("email")),
                    "status": "error",
                    "training_queues": target_name,
                    "production_queues": "",
                    "actions": "",
                    "detail": str(exc),
                }
            )
            stats["error"] += 1
            continue

        if action == "skipped_already_joined":
            stats["already_joined"] += 1
        elif action == "added_member":
            stats["added"] += 1
        elif action == "updated_joined":
            stats["joined_updated"] += 1
        elif action == "would_add_member":
            stats["would_add"] += 1
        elif action == "would_set_joined":
            stats["would_join"] += 1

        rows.append(
            {
                "input_email": input_email,
                "user_id": safe_str(user.get("id")),
                "user_name": safe_str(user.get("name")),
                "user_email": safe_str(user.get("email")),
                "status": "ok",
                "training_queues": target_name,
                "production_queues": "",
                "actions": action_detail,
                "detail": resolve_detail,
            }
        )
        stats["ok"] += 1

    return {
        "mode": "assign_training_queue",
        "apply": apply,
        "target_queue_id": target_id,
        "target_queue_name": target_name,
        "training_queue_count": len(training_queues),
        "input_count": len(emails),
        "stats": stats,
        "rows": rows,
    }
