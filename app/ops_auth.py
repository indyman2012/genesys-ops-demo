"""Genesys OAuth and role gates for the launchpad and protected ops apps."""
from __future__ import annotations

import re
from typing import Any

from core.config import env_first
from core.genesys_client import safe_str

# Built-in Genesys role names (normalized for matching).
SUPERVISION_ROLE_NAMES = frozenset({"supervisor"})
ADMIN_ROLE_NAMES = frozenset({"admin", "master admin", "super admin"})

# Legacy combined set (queue-performance env override OPS_QUEUE_PERFORMANCE_ROLES).
DEFAULT_ALLOWED_ROLES = frozenset({"supervisor", "admin", "master admin", "super admin"})

SESSION_ACCESS_KEY = "queue_performance_allowed"
SESSION_ROLES_KEY = "genesys_role_names"
SESSION_HUB_SUPERVISION = "hub_supervision_allowed"
SESSION_HUB_ADMIN = "hub_admin_allowed"


def normalize_role_name(name: Any) -> str:
    """Case/whitespace/separator-insensitive role key ("Super_Admin", " super-admin " -> "super admin")."""
    return " ".join(re.sub(r"[_\-]+", " ", str(name or "")).split()).lower()


def normalize_role_names(names: Any) -> frozenset[str]:
    return frozenset(n for n in (normalize_role_name(x) for x in (names or [])) if n)


def hub_auth_enabled() -> bool:
    """When false (OPS_QUEUE_PERFORMANCE_PUBLIC=1), launchpad shows all sections without sign-in."""
    value = (env_first("OPS_QUEUE_PERFORMANCE_PUBLIC") or "0").strip().lower()
    return value not in {"1", "true", "yes", "y", "on"}


def queue_performance_public() -> bool:
    return not hub_auth_enabled()


def queue_performance_auth_enabled() -> bool:
    return hub_auth_enabled()


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


def _supervision_roles() -> frozenset[str]:
    custom = (env_first("OPS_SUPERVISION_ROLES") or "").strip()
    if custom:
        return normalize_role_names(custom.split(","))
    return SUPERVISION_ROLE_NAMES


def _admin_roles() -> frozenset[str]:
    custom = (env_first("OPS_ADMIN_ROLES") or "").strip()
    if custom:
        return normalize_role_names(custom.split(","))
    return ADMIN_ROLE_NAMES


def evaluate_hub_access(client, user_id: str) -> tuple[bool, bool, list[str]]:
    role_names = fetch_user_role_names(client, user_id)
    normalized = normalize_role_names(role_names)
    supervision = bool(normalized & _supervision_roles())
    admin = bool(normalized & _admin_roles())
    return supervision, admin, role_names


def sync_hub_access(session: dict[str, Any], client) -> tuple[bool, bool, list[str]]:
    user_id = safe_str(session.get("genesys_user_id"))
    if not user_id:
        session.pop(SESSION_HUB_SUPERVISION, None)
        session.pop(SESSION_HUB_ADMIN, None)
        session.pop(SESSION_ACCESS_KEY, None)
        session.pop(SESSION_ROLES_KEY, None)
        return False, False, []

    supervision, admin, role_names = evaluate_hub_access(client, user_id)
    session[SESSION_HUB_SUPERVISION] = supervision
    session[SESSION_HUB_ADMIN] = admin
    session[SESSION_ROLES_KEY] = role_names
    session[SESSION_ACCESS_KEY] = supervision or admin
    return supervision, admin, role_names


def sync_queue_performance_access(session: dict[str, Any], client) -> tuple[bool, list[str], list[str]]:
    """Legacy API: True if user has supervision or admin tier (for older callers)."""
    supervision, admin, role_names = sync_hub_access(session, client)
    allowed = supervision or admin
    normalized = normalize_role_names(role_names)
    matched = sorted(normalized & (allowed_role_names()))
    return allowed, role_names, matched


def evaluate_queue_performance_access(client, user_id: str) -> tuple[bool, list[str], list[str]]:
    supervision, admin, role_names = evaluate_hub_access(client, user_id)
    allowed = supervision or admin
    normalized = normalize_role_names(role_names)
    matched = sorted(normalized & allowed_role_names())
    return allowed, role_names, matched


def supervision_access_granted(session: dict[str, Any]) -> bool:
    if not hub_auth_enabled():
        return True
    if not is_genesys_authenticated(session):
        return False
    return bool(session.get(SESSION_HUB_SUPERVISION))


def admin_access_granted(session: dict[str, Any]) -> bool:
    if not hub_auth_enabled():
        return True
    if not is_genesys_authenticated(session):
        return False
    return bool(session.get(SESSION_HUB_ADMIN))


def queue_performance_access_granted(session: dict[str, Any]) -> bool:
    return supervision_access_granted(session)


def hub_login_url(*, next_path: str | None = None) -> str:
    from flask import url_for

    target = (next_path or "/").strip() or "/"
    return url_for("agent.oauth_login", **{"for": "hub", "next": target})


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
    return next_url or "/"


def _wants_json_api_response() -> bool:
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


def _access_denied_response(
    *,
    user_name: str,
    user_roles: list[str],
    required_label: str,
    required_roles: list[str],
    matched_roles: list[str],
    error_message: str,
    status: int,
):
    from flask import jsonify, render_template

    if _wants_json_api_response():
        return jsonify({
            "error": "Access denied",
            "required_tier": required_label,
            "required_roles": required_roles,
            "your_roles": user_roles,
        }), status

    return (
        render_template(
            "ops/hub_access_denied.html",
            user_name=user_name,
            user_roles=user_roles,
            required_label=required_label,
            required_roles=required_roles,
            matched_roles=matched_roles,
            error_message=error_message,
        ),
        status,
    )


def _require_tier_access(*, tier: str, session_key: str, required_label: str, role_names_display: list[str]):
    from flask import redirect, request, session, url_for

    from core.cli import create_client, tool_log

    if not hub_auth_enabled():
        return None

    if not is_genesys_authenticated(session):
        return_to = _oauth_return_path()
        login_url = hub_login_url(next_path=return_to)
        if _wants_json_api_response():
            from flask import jsonify

            return jsonify({
                "error": "Sign in required",
                "login_url": login_url,
                "hub_url": url_for("ops.index"),
            }), 401
        return redirect(url_for("ops.index", next=return_to, signin=1))

    if session.get(session_key) is True:
        return None

    try:
        client = create_client(log=tool_log)
        sync_hub_access(session, client)
    except Exception as exc:
        from flask import jsonify

        if _wants_json_api_response():
            return jsonify({"error": f"Could not verify Genesys roles: {exc}"}), 502
        return _access_denied_response(
            user_name=session.get("genesys_user_name") or "User",
            user_roles=session.get(SESSION_ROLES_KEY) or [],
            required_label=required_label,
            required_roles=role_names_display,
            matched_roles=[],
            error_message=str(exc),
            status=502,
        )

    if session.get(session_key):
        return None

    return _access_denied_response(
        user_name=session.get("genesys_user_name") or "User",
        user_roles=session.get(SESSION_ROLES_KEY) or [],
        required_label=required_label,
        required_roles=role_names_display,
        matched_roles=[],
        error_message="",
        status=403,
    )


def require_supervision_access():
    return _require_tier_access(
        tier="supervision",
        session_key=SESSION_HUB_SUPERVISION,
        required_label="Supervision (Supervisor role)",
        role_names_display=sorted(_supervision_roles()),
    )


def require_admin_access():
    return _require_tier_access(
        tier="admin",
        session_key=SESSION_HUB_ADMIN,
        required_label="Admin",
        role_names_display=sorted(_admin_roles()),
    )


def require_queue_performance_access():
    """Supervisor-tier tools (legacy import name)."""
    return require_supervision_access()
