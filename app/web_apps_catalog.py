"""Registry of web UIs served by GenesysToolkit deploys (hub launcher)."""
from __future__ import annotations

from dataclasses import dataclass

from flask import Flask, url_for


@dataclass(frozen=True)
class WebAppSpec:
    id: str
    title: str
    description: str
    category: str
    endpoint: str | None = None
    path: str | None = None
    badge: str | None = None
    sort: int = 100


# Order within category is sort key, then title.
WEB_APP_SPECS: tuple[WebAppSpec, ...] = (
    WebAppSpec(
        id="agent",
        title="Agent Workspace",
        description="Queue login, presence, outbound, and transfers — Associate-style pilot for the desktop.",
        category="Frontline",
        endpoint="agent.index",
        badge="Pilot",
        sort=10,
    ),
    WebAppSpec(
        id="corporate-training",
        title="Corporate Training Roster",
        description="Move users into training queues and restore their normal queue setup when training ends.",
        category="Frontline",
        endpoint="ops.corporate_training_page",
        sort=20,
    ),
    WebAppSpec(
        id="site-picker",
        title="Site Picker",
        description="Bridge for agents or workflows that need to pick an ArchWell site or market context.",
        category="Frontline",
        endpoint="site_picker.index",
        sort=30,
    ),
    WebAppSpec(
        id="queue-performance",
        title="Queue Performance",
        description="Live queue metrics, filters, drill-down, and PDF export for supervisors.",
        category="Supervision",
        endpoint="ops.queue_performance_page",
        sort=10,
    ),
    WebAppSpec(
        id="not-responding",
        title="Not Responding",
        description="Agents in routed states who are not responding — quick operational snapshot.",
        category="Supervision",
        endpoint="ops.not_responding_page",
        sort=20,
    ),
    WebAppSpec(
        id="call-manager-sync",
        title="Call Manager Sync",
        description="Upload NCM roster spreadsheets and sync call-manager queue membership from a controlled UI.",
        category="Admin",
        endpoint="ops.call_manager_sync_page",
        sort=10,
    ),
    WebAppSpec(
        id="ops-console",
        title="Ops Console",
        description="Browse toolkit scripts, dry-run safely, and execute approved changes after review.",
        category="Admin",
        endpoint="ops.ops_console",
        sort=20,
    ),
    WebAppSpec(
        id="ask-genesys",
        title="Ask Genesys",
        description="Natural-language helper for reports and toolkit actions (LLM-assisted, preview in demo).",
        category="Admin",
        endpoint="ask.ask_page",
        sort=30,
    ),
)

_CATEGORY_ORDER = ("Frontline", "Supervision", "Admin")


def _resolve_href(spec: WebAppSpec) -> str | None:
    if spec.endpoint:
        try:
            return url_for(spec.endpoint)
        except Exception:
            return None
    if spec.path:
        return spec.path
    return None


def hub_sections_for_app(app: Flask) -> list[dict]:
    """Build hub sections with only apps whose routes are registered on this Flask app."""
    with app.app_context():
        entries: list[dict] = []
        for spec in WEB_APP_SPECS:
            href = _resolve_href(spec)
            if not href:
                continue
            entries.append(
                {
                    "id": spec.id,
                    "title": spec.title,
                    "description": spec.description,
                    "category": spec.category,
                    "href": href,
                    "badge": spec.badge,
                    "sort": spec.sort,
                }
            )

    by_cat: dict[str, list[dict]] = {c: [] for c in _CATEGORY_ORDER}
    for entry in entries:
        cat = entry["category"]
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(entry)

    sections: list[dict] = []
    for cat in _CATEGORY_ORDER:
        apps = sorted(by_cat.get(cat, []), key=lambda a: (a["sort"], a["title"].lower()))
        if apps:
            sections.append({"category": cat, "apps": apps})
    for cat, apps in by_cat.items():
        if cat in _CATEGORY_ORDER or not apps:
            continue
        sections.append(
            {
                "category": cat,
                "apps": sorted(apps, key=lambda a: (a["sort"], a["title"].lower())),
            }
        )
    return sections
