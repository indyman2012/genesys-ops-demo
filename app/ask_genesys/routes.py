"""Ask Genesys — read-only natural-language query page (regex router, no LLM).

Access uses the same Genesys OAuth + role gate as Queue Performance
(supervisor / admin / super admin by default; override with OPS_QUEUE_PERFORMANCE_ROLES,
or OPS_QUEUE_PERFORMANCE_PUBLIC=1 for local dev). Production should keep the gate on.
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
    from tools.reporting.ask_genesys_router import HELP_EXAMPLES

    return render_template(
        "ops/ask_genesys.html",
        demo_mode=_demo_mode(),
        auth_enabled=queue_performance_auth_enabled(),
        auth_user_name=session.get("genesys_user_name") or "",
        examples=HELP_EXAMPLES,
    )


@ask_bp.route("/api/ask-genesys", methods=["POST"])
def ask_api():
    blocked = require_queue_performance_access()
    if blocked:
        return blocked

    payload = request.get_json(silent=True) or {}
    prompt = str(payload.get("prompt") or request.form.get("prompt") or "").strip()
    if len(prompt) > MAX_PROMPT_CHARS:
        return jsonify({"error": f"Prompt too long (max {MAX_PROMPT_CHARS} characters)."}), 400

    # Lightweight audit trail (stdout). v2: persist to a durable audit log.
    tool_log("info", f"ask-genesys user={session.get('genesys_user_name') or 'anonymous'!r} prompt={prompt!r}")

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


@ask_bp.route("/api/ask-genesys/csv/<filename>")
def ask_csv(filename: str):
    blocked = require_queue_performance_access()
    if blocked:
        return blocked
    if not _CSV_NAME_RE.match(filename):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(OUTPUTS_DIR / CSV_SUBDIR, filename, as_attachment=True)
