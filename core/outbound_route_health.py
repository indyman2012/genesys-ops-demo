"""Shared Default Outbound Route health checks (CLEMKT lesson)."""
from __future__ import annotations

from typing import Any

DEFAULT_ROUTE_NAME = "Default Outbound Route"
DEFAULT_TRUNK_NAME = "Pure-IP"

STATUS_OK = "OK"
STATUS_MISSING = "MISSING"
STATUS_DISABLED = "DISABLED"
STATUS_NO_TRUNK = "NO_TRUNK"
STATUS_ERROR = "ERROR"


def build_default_route_payload(*, trunk_name: str = DEFAULT_TRUNK_NAME, route_name: str = DEFAULT_ROUTE_NAME) -> dict[str, Any]:
    return {
        "name": route_name,
        "description": "Auto Added",
        "classificationTypes": ["National", "Network"],
        "enabled": True,
        "distribution": "SEQUENTIAL",
        "externalTrunkBases": [{"name": trunk_name}],
    }


def trunk_names(route: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for trunk in route.get("externalTrunkBases") or []:
        if isinstance(trunk, dict) and trunk.get("name"):
            names.append(str(trunk["name"]))
    if not names:
        for trunk in route.get("trunks") or []:
            if isinstance(trunk, dict) and trunk.get("name"):
                names.append(str(trunk["name"]))
    return names


def route_enabled(route: dict[str, Any]) -> bool:
    if route.get("enabled") is True:
        return True
    return (route.get("state") or "").upper() == "ACTIVE"


def has_trunk_assignment(route: dict[str, Any]) -> bool:
    if trunk_names(route):
        return True
    trunk_ids = route.get("trunkBaseIds") or route.get("externalTrunkBaseIds") or []
    return bool(trunk_ids)


def evaluate_route(route: dict[str, Any] | None, *, route_name: str = DEFAULT_ROUTE_NAME) -> dict[str, Any]:
    if not route:
        return {
            "status": STATUS_MISSING,
            "route_name": route_name,
            "route_id": "",
            "enabled": False,
            "trunk_names": "",
            "issues": f"Missing {route_name}",
        }

    enabled = route_enabled(route)
    trunks = trunk_names(route)
    has_trunk = has_trunk_assignment(route)
    issues: list[str] = []
    status = STATUS_OK

    if not enabled:
        status = STATUS_DISABLED
        issues.append("Route not enabled")
    if not has_trunk:
        status = STATUS_NO_TRUNK if status == STATUS_OK else status
        issues.append("No external trunk assigned")

    return {
        "status": status,
        "route_name": route.get("name") or route_name,
        "route_id": route.get("id") or "",
        "enabled": enabled,
        "trunk_names": ";".join(trunks),
        "issues": "; ".join(issues),
    }


def find_default_route(client, site_id: str, *, route_name: str = DEFAULT_ROUTE_NAME) -> dict[str, Any] | None:
    routes = client.paged_get(f"/api/v2/telephony/providers/edges/sites/{site_id}/outboundroutes")
    summary = next((r for r in routes if (r.get("name") or "") == route_name), None)
    if not summary:
        return None
    route_id = summary.get("id")
    if not route_id:
        return summary
    try:
        return client.request(
            "GET",
            f"/api/v2/telephony/providers/edges/sites/{site_id}/outboundroutes/{route_id}",
        )
    except Exception as exc:
        return {**summary, "_detail_error": str(exc)}


def verify_default_route(client, site_id: str, *, route_name: str = DEFAULT_ROUTE_NAME) -> tuple[bool, str, dict[str, Any]]:
    try:
        route = find_default_route(client, site_id, route_name=route_name)
    except Exception as exc:
        result = {
            "status": STATUS_ERROR,
            "route_name": route_name,
            "route_id": "",
            "enabled": False,
            "trunk_names": "",
            "issues": str(exc),
        }
        return False, str(exc), result

    result = evaluate_route(route, route_name=route_name)
    ok = result["status"] == STATUS_OK
    note = "ok" if ok else (result["issues"] or result["status"])
    return ok, note, result
