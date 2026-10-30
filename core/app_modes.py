"""Split ops-console demo (dry-run tools) from agent workspace demo (simulation)."""
from __future__ import annotations

import os

from flask import Flask


def env_bool(name: str, default: bool | None = None) -> bool | None:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def apply_split_demo_config(app: Flask, *, ops_demo: bool) -> None:
    """OPS_DEMO_MODE = ops console dry-run; AGENT_DEMO_MODE = simulated /agent UI."""
    app.config["OPS_DEMO_MODE"] = ops_demo
    agent_demo = env_bool("AGENT_DEMO_MODE")
    if agent_demo is None:
        agent_demo = ops_demo
    app.config["AGENT_DEMO_MODE"] = agent_demo
    app.config["DEMO_MODE"] = ops_demo
