"""Genesys OAuth gate for Queue Performance (supervisor/admin roles only)."""
from __future__ import annotations

from typing import Any

from core.config import env_first
from core.genesys_client import safe_str

DEFAULT_ALLOWED_ROLES = frozenset({"supervisor", "admin", "super admin"})
SESSION_ACCESS_KEY = "queue_performance_allowed"
SESSION_ROLES_KEY = "genesys_role_names"


def queue_performance_public() -> bool:
    value = (env_first("OPS_QUEUE_PERFORMANCE_PUBLIC") or "0").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def queue_performance_auth_enabled() -> bool:
    return not queue_performance_public()


def allowed_role_names() -> frozenset[str]:
    custom = (env_first("OPS_QUEUE_PERFORMANCE_ROLES") or "").strip()
    if not custom:
        return DEFAULT_ALLOWED_ROLES
    return frozenset(part.strip().lower() for part in custom.split(",") if part.strip())


def is_genesys_authenticated(session: dict[str, Any]) -> bool:
    return bool(session.get("genesys_user_id") and session.get("genesys_access_token"))


def fetch_user_role_names(client, user_id: str) -> list[str]:
    resp = client.request("GET", f"/api/v2/users/{user_id}/roles")
    roles = resp.get("roles") if isinstance(resp.get("roles"), list) else []
    names: list[str] = []
    for role in roles:
        if not isinstance(role, dict):
            continue
        name = safe_str(role.get("name")).strip()
        if name:
            names.append(name)
    return names


def evaluate_queue_performance_access(client, user_id: str) -> tuple[bool, list[str], list[str]]:
    role_names = fetch_user_role_names(client, user_id)
    normalized = {name.lower() for name in role_names}
    matched = sorted(normalized & allowed_role_names())
    return bool(matched), role_names, matched


def sync_queue_performance_access(session: dict[str, Any], client) -> tuple[bool, list[str], list[str]]:
    user_id = safe_str(session.get("genesys_user_id"))
    if not user_id:
        session.pop(SESSION_ACCESS_KEY, None)
        session.pop(SESSION_ROLES_KEY, None)
        return False, [], []

    allowed, role_names, matched = evaluate_queue_performance_access(client, user_id)
    session[SESSION_ACCESS_KEY] = allowed
    session[SESSION_ROLES_KEY] = role_names
    return allowed, role_names, matched


def queue_performance_access_granted(session: dict[str, Any]) -> bool:
    if not queue_performance_auth_enabled():
        return True
    if not is_genesys_authenticated(session):
        return False
    return bool(session.get(SESSION_ACCESS_KEY))


def require_queue_performance_access():
    from flask import jsonify, redirect, render_template, request, session, url_for

    from core.cli import create_client, tool_log

    if not queue_performance_auth_enabled():
        return None

    login_url = url_for("agent.oauth_login", **{"for": "queue-performance"})

    if not is_genesys_authenticated(session):
        next_url = request.full_path if request.query_string else request.path
        if next_url.endswith("?"):
            next_url = next_url[:-1]
        session["oauth_for"] = "queue-performance"
        session["oauth_next"] = next_url or "/queue-performance"
        if request.path.startswith("/api/"):
            return jsonify({"error": "Sign in required", "login_url": login_url}), 401
        return redirect(login_url)

    if session.get(SESSION_ACCESS_KEY) is True:
        return None

    try:
        client = create_client(log=tool_log)
        allowed, role_names, matched = sync_queue_performance_access(session, client)
    except Exception as exc:
        if request.path.startswith("/api/"):
            return jsonify({"error": f"Could not verify Genesys roles: {exc}"}), 502
        return (
            render_template(
                "ops/queue_performance_denied.html",
                demo_mode=False,
                user_name=session.get("genesys_user_name") or "User",
                user_roles=session.get(SESSION_ROLES_KEY) or [],
                required_roles=sorted(allowed_role_names()),
                matched_roles=[],
                error_message=str(exc),
            ),
            502,
        )

    if allowed:
        return None

    required = sorted(allowed_role_names())
    if request.path.startswith("/api/"):
        return jsonify({
            "error": "Access denied",
            "required_roles": required,
            "your_roles": role_names,
        }), 403

    return (
        render_template(
            "ops/queue_performance_denied.html",
            demo_mode=False,
            user_name=session.get("genesys_user_name") or "User",
            user_roles=role_names,
            required_roles=required,
            matched_roles=matched,
            error_message="",
        ),
        403,
    )
