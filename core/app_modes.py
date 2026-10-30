"""Split ops-console demo (dry-run tools) from agent workspace demo (simulation)."""
from __future__ import annotations

import os

from flask import Flask

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


def coerce_bool(value: object, *, default: bool = False) -> bool:
    """Parse bool from config/env values (avoids bool(\"0\") == True)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    return default


def env_bool(name: str, default: bool | None = None) -> bool | None:
    raw = os.environ.get(name, "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    return default


def agent_demo_from_env() -> bool | None:
    """Read agent demo flag from env (canonical AGENT_DEMO_MODE only)."""
    return env_bool("AGENT_DEMO_MODE")


def apply_split_demo_config(app: Flask, *, ops_demo: bool) -> None:
    """OPS_DEMO_MODE = ops console dry-run; AGENT_DEMO_MODE = simulated /agent UI."""
    app.config["OPS_DEMO_MODE"] = ops_demo
    agent_demo = agent_demo_from_env()
    if agent_demo is None:
        # Independent of ops demo: unset env => live /agent (combined deploy default).
        agent_demo = False
    app.config["AGENT_DEMO_MODE"] = agent_demo
    app.config["DEMO_MODE"] = ops_demo


def apply_combined_deploy_modes(app: Flask) -> None:
    """genesys_toolkit_app: ops dry-run + live /agent.

    Stale Render env (e.g. AGENT_DEMO_MODE=1) must not re-enable simulation on this entry.
    Simulated agent UI remains on agent_app_demo.py.
    """
    app.config["GENESYS_COMBINED_DEPLOY"] = True
    app.config["OPS_DEMO_MODE"] = True
    app.config["DEMO_MODE"] = True
    if env_bool("GENESYS_TOOLKIT_FORCE_AGENT_DEMO") is True:
        app.config["AGENT_DEMO_MODE"] = True
    else:
        app.config["AGENT_DEMO_MODE"] = False


def demo_mode_health_payload(app: Flask) -> dict[str, object]:
    agent_env = (os.environ.get("AGENT_DEMO_MODE") or "").strip()
    ops_env = (os.environ.get("OPS_DEMO_MODE") or "").strip()
    agent_cfg = app.config.get("AGENT_DEMO_MODE")
    ops_cfg = app.config.get("OPS_DEMO_MODE")
    combined = bool(app.config.get("GENESYS_COMBINED_DEPLOY"))
    return {
        "demo_mode": coerce_bool(ops_cfg, default=coerce_bool(app.config.get("DEMO_MODE"))),
        "ops_demo_mode": coerce_bool(ops_cfg, default=coerce_bool(app.config.get("DEMO_MODE"))),
        "agent_demo_mode": coerce_bool(agent_cfg),
        "ops_demo_mode_env": ops_env or None,
        "agent_demo_mode_env": agent_env or None,
        "agent_demo_mode_env_parsed": agent_demo_from_env(),
        "genesys_combined_deploy": combined,
        "agent_demo_mode_env_applied": not combined,
    }


def agent_workspace_health_payload(app: Flask) -> dict[str, object]:
    base = demo_mode_health_payload(app)
    agent_demo = coerce_bool(app.config.get("AGENT_DEMO_MODE"))
    base["demo_mode"] = agent_demo
    return base
