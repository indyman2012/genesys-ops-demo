from __future__ import annotations

import os


def is_demo_mode() -> bool:
    return os.environ.get("OPS_DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")


# Tools excluded from demo — no reliable dry-run or inherently destructive.
DEMO_EXCLUDED_TOOL_IDS = frozenset({
    "delete_sites_locations",
})
