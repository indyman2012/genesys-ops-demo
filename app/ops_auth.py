"""Genesys OAuth gate for Queue Performance (supervisor/admin roles only)."""
from __future__ import annotations

import re
from typing import Any

from core.config import env_first
from core.genesys_client import safe_str

# Genesys built-in admin roles are "Admin" and "Master Admin"; "Super Admin" kept for custom orgs.
DEFAULT_ALLOWED_ROLES = frozenset({"supervisor", "admin", "master admin", "super admin"})
SESSION_ACCESS_KEY = "queue_performance_allowed"
SESSION_ROLES_KEY = "genesys_role_names"


def normalize_role_name(name: Any) -> str:
    """Case/whitespace/separator-insensitive role key ("Super_Admin", " super-admin " -> "super admin")."""
    return " ".join(re.sub(r"[_\-]+", " ", str(name or "")).split()).lower()


def normalize_role_names(names: Any) -> frozenset[str]:
    return frozenset(n for n in (normalize_role_name(x) for x in (names or [])) if n)


def queue_performance_public() -> bool:
    value = (env_first("OPS_QUEUE_PERFORMANCE_PUBLIC") or "0").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def queue_performance_auth_enabled() -> bool:
    return not queue_performance_public()


def allowed_role_names() -> frozenset[str]:
    custom = (env_first("OPS_QUEUE_PERFORMANCE_ROLES") or "").strip()
    if not custom:
        return DEFAULT_ALLOWED_ROLES
    return normalize_role_names(custom.split(","))


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
    normalized = normalize_role_names(role_names)
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


def _wants_json_api_response() -> bool:
    """True for XHR/fetch endpoints (including /corporate-training/api/*, not only /api/*)."""
    from flask import request

    path = request.path or ""
    if path.startswith("/api/") or "/api/" in path:
        return True
    accept = (request.headers.get("Accept") or "").lower()
    if "application/json" in accept:
        return True
    if (request.headers.get("X-Requested-With") or "").lower() == "xmlhttprequest":
        return True
    return False


def _oauth_return_path() -> str:
    from flask import request

    path = request.path or ""
    if path.startswith("/corporate-training") or "/corporate-training/" in path:
        return "/corporate-training/"
    if path.startswith("/call-manager-sync") or "/call-manager-sync/" in path:
        return "/call-manager-sync/"
    next_url = request.full_path if request.query_string else path
    if next_url.endswith("?"):
        next_url = next_url[:-1]
    return next_url or "/queue-performance"


def _queue_performance_login_url(*, return_to: str | None = None) -> str:
    from flask import url_for

    target = (return_to or _oauth_return_path()).strip() or "/queue-performance"
    if target.startswith("/corporate-training"):
        return url_for(
            "agent.oauth_login",
            **{"for": "queue-performance", "next": "/corporate-training/"},
        )
    if target.startswith("/call-manager-sync"):
        return url_for(
            "agent.oauth_login",
            **{"for": "queue-performance", "next": "/call-manager-sync/"},
        )
    if target != "/queue-performance":
        return url_for(
            "agent.oauth_login",
            **{"for": "queue-performance", "next": target},
        )
    return url_for("agent.oauth_login", **{"for": "queue-performance"})


def require_queue_performance_access():
    from flask import jsonify, redirect, render_template, request, session, url_for

    from core.cli import create_client, tool_log

    if not queue_performance_auth_enabled():
        return None

    if not is_genesys_authenticated(session):
        return_to = _oauth_return_path()
        session["oauth_for"] = "queue-performance"
        session["oauth_next"] = return_to
        login_url = _queue_performance_login_url(return_to=return_to)
        if _wants_json_api_response():
            return jsonify({"error": "Sign in required", "login_url": login_url}), 401
        return redirect(login_url)

    if session.get(SESSION_ACCESS_KEY) is True:
        return None

    try:
        client = create_client(log=tool_log)
        allowed, role_names, matched = sync_queue_performance_access(session, client)
    except Exception as exc:
        if _wants_json_api_response():
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
    if _wants_json_api_response():
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
