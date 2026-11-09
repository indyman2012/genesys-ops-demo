"""Registry of web UIs served by GenesysToolkit deploys (hub launcher)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from flask import Flask, url_for

from app.ops_auth import (
    admin_access_granted,
    hub_auth_enabled,
    is_genesys_authenticated,
    supervision_access_granted,
)

AccessTier = Literal["frontline", "supervision", "admin"]


@dataclass(frozen=True)
class WebAppSpec:
    id: str
    title: str
    description: str
    category: str
    access: AccessTier
    endpoint: str | None = None
    path: str | None = None
    badge: str | None = None
    sort: int = 100


WEB_APP_SPECS: tuple[WebAppSpec, ...] = (
    WebAppSpec(
        id="agent",
        title="Agent Workspace",
        description="Queue login, presence, outbound, and transfers — Associate-style pilot for the desktop.",
        category="Frontline",
        access="frontline",
        endpoint="agent.index",
        badge="Pilot",
        sort=10,
    ),
    WebAppSpec(
        id="site-picker",
        title="Site Picker",
        description="Bridge for agents or workflows that need to pick an ArchWell site or market context.",
        category="Frontline",
        access="frontline",
        endpoint="site_picker.index",
        sort=20,
    ),
    WebAppSpec(
        id="queue-performance",
        title="Queue Performance",
        description="Live queue metrics, filters, drill-down, and PDF export for supervisors.",
        category="Supervision",
        access="supervision",
        endpoint="ops.queue_performance_page",
        sort=10,
    ),
    WebAppSpec(
        id="not-responding",
        title="Not Responding",
        description="Agents in routed states who are not responding — quick operational snapshot.",
        category="Supervision",
        access="supervision",
        endpoint="ops.not_responding_page",
        sort=20,
    ),
    WebAppSpec(
        id="corporate-training",
        title="Corporate Training Roster",
        description="Move users into training queues and restore their normal queue setup when training ends.",
        category="Supervision",
        access="supervision",
        endpoint="ops.corporate_training_page",
        sort=30,
    ),
    WebAppSpec(
        id="call-manager-sync",
        title="Care Manager Sync",
        description="Upload NCM care-manager rosters and sync queue membership, phones, and lookup rows from a controlled UI.",
        category="Supervision",
        access="supervision",
        endpoint="ops.call_manager_sync_page",
        sort=40,
    ),
    WebAppSpec(
        id="ops-console",
        title="Ops Console",
        description="Browse toolkit scripts, dry-run safely, and execute approved changes after review.",
        category="Admin",
        access="admin",
        endpoint="ops.ops_console",
        sort=10,
    ),
    WebAppSpec(
        id="ask-genesys",
        title="Ask Genesys",
        description="Natural-language helper for reports and toolkit actions (LLM-assisted, preview in demo).",
        category="Admin",
        access="admin",
        endpoint="ask.ask_page",
        sort=20,
    ),
)

_CATEGORY_ORDER = ("Frontline", "Supervision", "Admin")


def _tier_visible(session: dict[str, Any], tier: AccessTier) -> bool:
    if not hub_auth_enabled():
        return True
    if tier == "frontline":
        return True
    if not is_genesys_authenticated(session):
        return False
    if tier == "supervision":
        return supervision_access_granted(session)
    if tier == "admin":
        return admin_access_granted(session)
    return False


def _resolve_href(spec: WebAppSpec) -> str | None:
    if spec.endpoint:
        try:
            return url_for(spec.endpoint)
        except Exception:
            return None
    if spec.path:
        return spec.path
    return None


def hub_sections_for_app(app: Flask, session: dict[str, Any]) -> list[dict]:
    """Build hub sections: frontline for everyone; other tiers after hub sign-in + role."""
    with app.app_context():
        entries: list[dict] = []
        for spec in WEB_APP_SPECS:
            if not _tier_visible(session, spec.access):
                continue
            href = _resolve_href(spec)
            if not href:
                continue
            entries.append(
                {
                    "id": spec.id,
                    "title": spec.title,
                    "description": spec.description,
                    "category": spec.category,
                    "access": spec.access,
                    "href": href,
                    "badge": spec.badge,
                    "sort": spec.sort,
                }
            )

    by_cat: dict[str, list[dict]] = {c: [] for c in _CATEGORY_ORDER}
    for entry in entries:
        by_cat.setdefault(entry["category"], []).append(entry)

    sections: list[dict] = []
    for cat in _CATEGORY_ORDER:
        apps = sorted(by_cat.get(cat, []), key=lambda a: (a["sort"], a["title"].lower()))
        if apps:
            sections.append({"category": cat, "apps": apps})
    return sections


def hub_locked_categories(session: dict[str, Any]) -> list[dict[str, str]]:
    """Sections hidden because the user is not signed in or lacks the role."""
    if not hub_auth_enabled():
        return []
    locked: list[dict[str, str]] = []
    if not is_genesys_authenticated(session):
        locked.append({
            "category": "Supervision",
            "reason": "Sign in with Genesys to see supervisor tools.",
        })
        locked.append({
            "category": "Admin",
            "reason": "Sign in with Genesys to see admin tools.",
        })
        return locked
    if not supervision_access_granted(session):
        locked.append({
            "category": "Supervision",
            "reason": "Requires the Genesys Supervisor role.",
        })
    if not admin_access_granted(session):
        locked.append({
            "category": "Admin",
            "reason": "Requires a Genesys Admin role.",
        })
    return locked
