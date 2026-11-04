from __future__ import annotations

from typing import Any

from core.genesys_client import GenesysClient, GenesysError, find_by_name, safe_str


def exact_email_matches(users: list[dict[str, Any]], email: str) -> list[dict[str, Any]]:
    wanted = email.strip().lower()
    matches: list[dict[str, Any]] = []
    for user in users:
        if safe_str(user.get("email")).lower() == wanted:
            matches.append(user)
            continue
        if safe_str(user.get("username")).lower() == wanted:
            matches.append(user)
            continue
        for addr in user.get("addresses") or []:
            if isinstance(addr, dict) and safe_str(addr.get("address")).lower() == wanted:
                matches.append(user)
                break
    return matches


def find_users_by_email_search(gc: GenesysClient, email: str) -> list[dict[str, Any]]:
    """Resolve users by exact email/username search only (no full-org scan)."""
    email = email.strip()
    if not email:
        return []

    candidates: dict[str, dict[str, Any]] = {}
    for body in (
        {"query": [{"type": "EXACT", "fields": ["email"], "value": email}], "pageSize": 25, "pageNumber": 1},
        {"query": [{"type": "EXACT", "fields": ["username"], "value": email}], "pageSize": 25, "pageNumber": 1},
    ):
        try:
            payload = gc.request("POST", "/api/v2/users/search", json_body=body)
            for user in (payload or {}).get("results") or []:
                if user.get("id"):
                    candidates[user["id"]] = user
        except GenesysError:
            pass

    return exact_email_matches(list(candidates.values()), email)


def find_user_by_email(gc: GenesysClient, email: str) -> dict[str, Any]:
    """Resolve a user by exact email using search first, then paginated fallback."""
    email = email.strip()
    if not email:
        raise GenesysError("Email is required")

    matches = find_users_by_email_search(gc, email)
    if not matches:
        candidates: dict[str, dict[str, Any]] = {}
        for user in gc.paged_get("/api/v2/users", params={"state": "any"}):
            if user.get("id"):
                candidates[user["id"]] = user
        matches = exact_email_matches(list(candidates.values()), email)
    if not matches:
        raise GenesysError(f"User not found: {email}")

    active = [u for u in matches if safe_str(u.get("state")).upper() == "ACTIVE"]
    if len(active) == 1:
        return active[0]
    if len(matches) == 1:
        return matches[0]
    raise GenesysError(f"Multiple users matched email {email}: {[u.get('name') for u in matches]}")


def get_all_queues(gc: GenesysClient) -> list[dict[str, Any]]:
    return gc.paged_get("/api/v2/routing/queues")


def get_queues_by_prefix(gc: GenesysClient, prefix: str) -> list[dict[str, Any]]:
    prefix_lower = prefix.strip().lower()
    return [q for q in get_all_queues(gc) if safe_str(q.get("name")).lower().startswith(prefix_lower)]


def find_queue_by_name(gc: GenesysClient, name: str) -> dict[str, Any]:
    queue = find_by_name(get_all_queues(gc), name)
    if not queue:
        raise GenesysError(f"Queue not found: {name}")
    return queue


def get_queue_members(gc: GenesysClient, queue_id: str) -> list[dict[str, Any]]:
    return gc.paged_get(f"/api/v2/routing/queues/{queue_id}/members")


def user_is_queue_member(gc: GenesysClient, queue_id: str, user_id: str) -> dict[str, Any] | None:
    payload = gc.request(
        "GET",
        f"/api/v2/routing/queues/{queue_id}/members",
        params={"pageSize": 25, "pageNumber": 1, "userId": user_id},
    )
    for member in (payload or {}).get("entities") or []:
        user = member.get("user") or {}
        member_id = user.get("id") or member.get("userId") or member.get("id")
        if member_id == user_id:
            return member
    return None


def find_user_queue_memberships(gc: GenesysClient, user_id: str) -> list[dict[str, str]]:
    memberships: list[dict[str, str]] = []
    for queue in get_all_queues(gc):
        queue_id = queue.get("id")
        if not queue_id:
            continue
        if user_is_queue_member(gc, queue_id, user_id):
            memberships.append({
                "queue_id": queue_id,
                "queue_name": safe_str(queue.get("name")) or queue_id,
            })
    return memberships
