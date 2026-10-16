"""Ask Genesys - read-only natural-language query page.

Standard mode: OpenAI tool-call router -> existing read-only handlers (regex fallback).
Advanced mode: LLM-generated read-only query script, AST-checked and run in a subprocess.

Access uses the same Genesys OAuth + role gate as Queue Performance
(supervisor / admin / super admin by default; override with OPS_QUEUE_PERFORMANCE_ROLES,
or OPS_QUEUE_PERFORMANCE_PUBLIC=1 for local dev). Production should keep the gate on.
Advanced mode has its own gate (ASK_GENESYS_ADVANCED_ROLES); see advanced.py.
"""
from __future__ import annotations

import re
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, send_from_directory, session

from app.ops_auth import queue_performance_auth_enabled, require_queue_performance_access
from core.cli import create_client, tool_log, write_csv
from core.demo_mode import is_demo_mode
from core.paths import OUTPUTS_DIR, ensure_runtime_dirs

ask_bp = Blueprint("ask", __name__)

CSV_SUBDIR = "ask_genesys"
MAX_PROMPT_CHARS = 500
_CSV_NAME_RE = re.compile(r"^ask_genesys_[a-z_]+_\d{8}_\d{6}\.csv$")


def _demo_mode() -> bool:
    from flask import current_app

    return bool(current_app.config.get("DEMO_MODE")) or is_demo_mode()


@ask_bp.route("/ask-genesys")
def ask_page():
    blocked = require_queue_performance_access()
    if blocked:
        return blocked
    from app.ask_genesys.advanced import advanced_access
    from app.ask_genesys.llm_parser import llm_enabled
    from tools.reporting.ask_genesys_router import HELP_EXAMPLES

    advanced_ok, advanced_reason = advanced_access(session)
    return render_template(
        "ops/ask_genesys.html",
        demo_mode=_demo_mode(),
        auth_enabled=queue_performance_auth_enabled(),
        auth_user_name=session.get("genesys_user_name") or "",
        examples=HELP_EXAMPLES,
        llm_on=llm_enabled(),
        advanced_allowed=advanced_ok,
        advanced_reason=advanced_reason,
    )


@ask_bp.route("/api/ask-genesys", methods=["POST"])
def ask_api():
    blocked = require_queue_performance_access()
    if blocked:
        return blocked

    payload = request.get_json(silent=True) or {}
    prompt = str(payload.get("prompt") or request.form.get("prompt") or "").strip()
    advanced = payload.get("advanced") in (True, 1, "1", "true", "True")
    user = session.get("genesys_user_name") or "anonymous"

    if advanced:
        return _advanced_response(prompt, user)

    if len(prompt) > MAX_PROMPT_CHARS:
        return jsonify({"error": f"Prompt too long (max {MAX_PROMPT_CHARS} characters)."}), 400

    # Lightweight audit trail (stdout). v2: persist to a durable audit log.
    tool_log("info", f"ask-genesys user={user!r} prompt={prompt!r}")

    try:
        from tools.reporting.ask_genesys_router import answer_prompt

        result = answer_prompt(lambda: create_client(log=tool_log), prompt)
        rows = result.get("rows") or []
        if len(rows) > 1 and result.get("csv_fields"):
            ensure_runtime_dirs()
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"ask_genesys_{result['intent']}_{stamp}.csv"
            write_csv(OUTPUTS_DIR / CSV_SUBDIR / name, rows, result["csv_fields"])
            result["csv_url"] = f"/api/ask-genesys/csv/{name}"
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _advanced_response(prompt: str, user: str):
    from app.ask_genesys.advanced import MAX_ADVANCED_PROMPT_CHARS, advanced_access, answer_advanced

    allowed, reason = advanced_access(session)
    if not allowed:
        tool_log("warn", f"ask-genesys ADVANCED denied user={user!r}: {reason}")
        return jsonify({"error": reason, "advanced": True, "ok": False}), 403
    if not prompt:
        return jsonify({"error": "Prompt is required."}), 400
    if len(prompt) > MAX_ADVANCED_PROMPT_CHARS:
        return jsonify({"error": f"Prompt too long (max {MAX_ADVANCED_PROMPT_CHARS} characters)."}), 400

    tool_log("info", f"ask-genesys ADVANCED user={user!r} prompt={prompt!r}")
    try:
        return jsonify(answer_advanced(prompt, user=user))
    except Exception as exc:
        return jsonify({"error": f"Advanced query failed: {exc}", "advanced": True, "ok": False}), 500


@ask_bp.route("/api/ask-genesys/csv/<filename>")
def ask_csv(filename: str):
    blocked = require_queue_performance_access()
    if blocked:
        return blocked
    if not _CSV_NAME_RE.match(filename):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(OUTPUTS_DIR / CSV_SUBDIR, filename, as_attachment=True)
