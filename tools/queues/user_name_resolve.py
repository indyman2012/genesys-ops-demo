"""Resolve Genesys users from roster names (shared by queue provisioning tools)."""
from __future__ import annotations

import difflib
import re
from collections import defaultdict
from typing import Any

from core.genesys_client import GenesysError, safe_str
from core.lookups import find_user_by_email

PAREN_NOTE = re.compile(r"\s*\([^)]*\)\s*")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FUZZY_NAME_THRESHOLD = 0.88


def normalize_person_name(value: str) -> str:
    cleaned = PAREN_NOTE.sub(" ", safe_str(value))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def build_user_indexes(client) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    users = list(client.paged_get("/api/v2/users", params={"state": "any"}))
    by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for user in users:
        name_key = normalize_person_name(safe_str(user.get("name"))).lower()
        if name_key:
            by_name[name_key].append(user)
    return users, by_name


def pick_best_duplicate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    active = [u for u in candidates if safe_str(u.get("state")).upper() == "ACTIVE"]
    pool = active or candidates
    if not pool:
        return None
    if len(pool) == 1:
        return pool[0]

    def score(user: dict[str, Any]) -> tuple[int, int, str]:
        email = safe_str(user.get("email")).lower()
        name = normalize_person_name(safe_str(user.get("name"))).lower()
        email_tokens = re.split(r"[.@]+", email)
        name_tokens = name.split()
        email_match = int(all(token in email_tokens for token in name_tokens if token))
        typo_penalty = int("longswoth" in email or "gmail.com" in email)
        return (email_match, -typo_penalty, email)

    return sorted(pool, key=score, reverse=True)[0]


def fuzzy_name_matches(normalized: str, users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target = normalized.lower()
    matches: list[tuple[float, dict[str, Any]]] = []
    for user in users:
        if safe_str(user.get("state")).upper() != "ACTIVE":
            continue
        candidate = normalize_person_name(safe_str(user.get("name"))).lower()
        if not candidate:
            continue
        ratio = difflib.SequenceMatcher(None, target, candidate).ratio()
        if ratio >= FUZZY_NAME_THRESHOLD:
            matches.append((ratio, user))
    matches.sort(key=lambda item: item[0], reverse=True)
    return [user for _ratio, user in matches]


def resolve_user(
    agent_name: str,
    *,
    client,
    users: list[dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, str, str]:
    normalized = normalize_person_name(agent_name)
    if not normalized:
        return None, "", "empty agent name"

    if EMAIL_PATTERN.match(normalized.lower()):
        try:
            user = find_user_by_email(client, normalized)
            return user, "email", ""
        except GenesysError as exc:
            return None, "email", str(exc)

    exact = by_name.get(normalized.lower(), [])
    picked = pick_best_duplicate(exact)
    if picked:
        if len(exact) > 1:
            return picked, "name_exact_duplicate", f"selected from {len(exact)} same-name accounts"
        return picked, "name_exact", ""

    tokens = normalized.split()
    if len(tokens) >= 2:
        last = tokens[-1].lower()
        first = tokens[0].lower()
        candidates = [
            u
            for u in users
            if safe_str(u.get("state")).upper() == "ACTIVE"
            and last in normalize_person_name(safe_str(u.get("name"))).lower()
            and first in normalize_person_name(safe_str(u.get("name"))).lower()
        ]
        if len(candidates) == 1:
            return candidates[0], "name_tokens", ""
        if len(candidates) > 1:
            names = [safe_str(u.get("name")) for u in candidates]
            return None, "name_tokens", f"ambiguous name match: {names}"

    try:
        body = {
            "query": [{"type": "CONTAINS", "fields": ["name"], "value": normalized}],
            "pageSize": 25,
            "pageNumber": 1,
        }
        payload = client.request("POST", "/api/v2/users/search", json_body=body)
        results = (payload or {}).get("results") or []
        active = [u for u in results if safe_str(u.get("state")).upper() == "ACTIVE"]
        if len(active) == 1:
            return active[0], "search_contains", ""
        if len(active) > 1:
            names = [safe_str(u.get("name")) for u in active]
            return None, "search_contains", f"ambiguous search match: {names}"
    except GenesysError as exc:
        return None, "search_contains", str(exc)

    fuzzy = fuzzy_name_matches(normalized, users)
    if len(fuzzy) == 1:
        matched_name = safe_str(fuzzy[0].get("name"))
        return fuzzy[0], "name_fuzzy", f"matched '{matched_name}'"
    if len(fuzzy) > 1:
        names = [safe_str(u.get("name")) for u in fuzzy[:5]]
        return None, "name_fuzzy", f"ambiguous fuzzy match: {names}"

    return None, "unmatched", "no Genesys user found"
