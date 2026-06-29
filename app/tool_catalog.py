from __future__ import annotations

import os

from app.tool_specs import TOOL_SPECS, get_tool as _get_tool, tools_by_category as _tools_by_category
from app.tool_help import enrich_tool
from core.demo_mode import DEMO_EXCLUDED_TOOL_IDS, is_demo_mode


def demo_tools_only() -> bool:
    return is_demo_mode()


def get_tool(tool_id: str):
    tool = _get_tool(tool_id)
    if not tool:
        return None
    if demo_tools_only() and tool["id"] in DEMO_EXCLUDED_TOOL_IDS:
        return None
    return enrich_tool(tool)


def tools_by_category() -> dict:
    grouped: dict = {}
    for tool in TOOL_SPECS:
        if demo_tools_only() and tool["id"] in DEMO_EXCLUDED_TOOL_IDS:
            continue
        enriched = enrich_tool(tool)
        grouped.setdefault(enriched["category"], []).append(enriched)
    for items in grouped.values():
        items.sort(key=lambda t: t["name"].lower())
    return dict(sorted(grouped.items(), key=lambda kv: kv[0]))
