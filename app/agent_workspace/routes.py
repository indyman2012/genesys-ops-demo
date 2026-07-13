from __future__ import annotations

import os
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify, render_template, request, session

from app.agent_workspace.demo_reports import aht_trend_report_html
from app.agent_workspace.demo_state import demo_state
from core.config import genesys_analytics_dashboards_url
from core.paths import PROJECT_ROOT

agent_bp = Blueprint("agent", __name__, url_prefix="/agent")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_authenticated() -> bool:
    return bool(session.get("genesys_user_id"))


def _unauthenticated_snapshot() -> dict:
    return {
        "demo_mode": False,
        "authenticated": False,
        "auth_required": True,
        "agent_name": "Sign in required",
        "on_queue": False,
        "selected_queue_id": "",
        "selected_queue_name": "",
        "queues": [],
        "media": {
            "mic": "",
            "mic_id": "",
            "speaker": "",
            "speaker_id": "",
            "webrtc_ready": False,
        },
        "current_call": None,
        "calls_waiting": [],
        "wrapup_codes": [],
        "membership": [],
        "activity": {
            "calls_in_progress": 0,
            "service_level_pct": None,
            "asa_sec": None,
            "service_level_target_sec": None,
        },
        "chat": [],
        "agent_stats": {
            "handled_today": 0,
            "avg_handle": "—",
            "avg_talk": "—",
            "avg_acw": "—",
            "callbacks_due": 0,
        },
        "stats_updated_at": None,
        "dial_requires_queue": True,
        "dial_requires_on_queue": False,
        "nav": {"supervisor": False, "admin": False},
        "updated_at": _utc_now(),
    }


def _public_snapshot(*, jitter: bool = True) -> dict:
    """Return API state; pilot mode returns empty shell until OAuth session exists."""
    from flask import current_app

    if current_app.config.get("DEMO_MODE"):
        snap = demo_state.snapshot(jitter=jitter)
        snap["authenticated"] = False
        snap["auth_required"] = False
        return snap
    if not _is_authenticated():
        return _unauthenticated_snapshot()
    snap = demo_state.snapshot(jitter=jitter)
    snap["demo_mode"] = False
    snap["authenticated"] = True
    snap["auth_required"] = False
    return snap


def _require_auth():
    from flask import current_app

    if current_app.config.get("DEMO_MODE") or _is_authenticated():
        return None
    return jsonify({"error": "Sign in required"}), 401


@agent_bp.route("/")
def index():
    from flask import current_app

    return render_template(
        "agent_workspace/index.html",
        demo_mode=bool(current_app.config.get("DEMO_MODE")),
    )


@agent_bp.route("/reports")
def reports():
    from flask import current_app

    return render_template(
        "agent_workspace/reports.html",
        demo_mode=bool(current_app.config.get("DEMO_MODE")),
        genesys_analytics_url=genesys_analytics_dashboards_url(),
    )


@agent_bp.route("/reports/aht-trend")
def reports_aht_trend():
    from flask import current_app

    prefer_live = not bool(current_app.config.get("DEMO_MODE"))
    html = aht_trend_report_html(prefer_live=prefer_live)
    return Response(html, mimetype="text/html; charset=utf-8")


@agent_bp.route("/health")
def health():
    from flask import current_app

    return jsonify({
        "ok": True,
        "demo_mode": bool(current_app.config.get("DEMO_MODE")),
        "service": "agent-workspace",
    })


@agent_bp.route("/oauth/login")
def oauth_login():
    """OAuth sign-in entry — wired in Phase 1 pilot build."""
    from flask import current_app

    if current_app.config.get("DEMO_MODE"):
        return render_template(
            "agent_workspace/oauth_placeholder.html",
            demo_mode=True,
            message="Demo mode does not require sign-in.",
        )
    return render_template(
        "agent_workspace/oauth_placeholder.html",
        demo_mode=False,
        message="Genesys sign-in is being connected. Check back after the OAuth build lands.",
    )


@agent_bp.route("/api/state")
def api_state():
    return jsonify(_public_snapshot())


@agent_bp.route("/api/queue", methods=["POST"])
def api_set_queue():
    blocked = _require_auth()
    if blocked:
        return blocked
    payload = request.get_json(silent=True) or {}
    queue_id = payload.get("queue_id")
    if not queue_id:
        return jsonify({"error": "queue_id required"}), 400
    demo_state.set_queue(queue_id)
    return jsonify(_public_snapshot(jitter=False))


@agent_bp.route("/api/on-queue", methods=["POST"])
def api_on_queue():
    blocked = _require_auth()
    if blocked:
        return blocked
    payload = request.get_json(silent=True) or {}
    on_queue = bool(payload.get("on_queue"))
    demo_state.toggle_queue(on_queue)
    return jsonify(_public_snapshot(jitter=False))


@agent_bp.route("/api/chat", methods=["POST"])
def api_chat():
    blocked = _require_auth()
    if blocked:
        return blocked
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    queue_id = payload.get("queue_id") or demo_state.selected_queue_id
    if not text:
        return jsonify({"error": "text required"}), 400
    message = demo_state.post_chat(queue_id, demo_state.agent_name, text)
    return jsonify({"message": message, "state": _public_snapshot(jitter=False)})


@agent_bp.route("/api/call/inbound", methods=["POST"])
def api_inbound():
    blocked = _require_auth()
    if blocked:
        return blocked
    call = demo_state.start_inbound_call()
    if not call:
        return jsonify({"error": "Must be on queue with no active call"}), 400
    return jsonify(_public_snapshot(jitter=False))


@agent_bp.route("/api/call/answer", methods=["POST"])
def api_answer():
    blocked = _require_auth()
    if blocked:
        return blocked
    call = demo_state.answer_call()
    if not call:
        return jsonify({"error": "No ringing call"}), 400
    return jsonify(_public_snapshot(jitter=False))


@agent_bp.route("/api/call/dial", methods=["POST"])
def api_dial():
    blocked = _require_auth()
    if blocked:
        return blocked
    payload = request.get_json(silent=True) or {}
    phone = (payload.get("phone_number") or "").strip()
    if not phone:
        return jsonify({"error": "phone_number required"}), 400
    call = demo_state.dial_on_behalf(phone)
    if not call:
        if demo_state.current_call:
            return jsonify({"error": "End the current call before dialing"}), 400
        return jsonify({"error": "Select a queue first"}), 400
    return jsonify(_public_snapshot(jitter=False))


@agent_bp.route("/api/call/end", methods=["POST"])
def api_end_call():
    blocked = _require_auth()
    if blocked:
        return blocked
    payload = request.get_json(silent=True) or {}
    wrapup_code_id = payload.get("wrapup_code_id") or "resolved"
    demo_state.end_call(wrapup_code_id)
    return jsonify(_public_snapshot(jitter=False))


@agent_bp.route("/api/media", methods=["POST"])
def api_set_media():
    blocked = _require_auth()
    if blocked:
        return blocked
    payload = request.get_json(silent=True) or {}
    demo_state.set_media(
        mic_id=payload.get("mic_id"),
        mic_label=payload.get("mic_label"),
        speaker_id=payload.get("speaker_id"),
        speaker_label=payload.get("speaker_label"),
    )
    return jsonify(_public_snapshot(jitter=False))


@agent_bp.route("/api/stats/refresh", methods=["POST"])
def api_refresh_stats():
    blocked = _require_auth()
    if blocked:
        return blocked
    demo_state.refresh_stats()
    return jsonify(_public_snapshot(jitter=False))


def create_app(*, demo_mode: bool = False):
    from flask import Flask

    if demo_mode:
        os.environ["AGENT_DEMO_MODE"] = "1"
    elif "AGENT_DEMO_MODE" not in os.environ:
        os.environ.pop("AGENT_DEMO_MODE", None)

    app = Flask(
        __name__,
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    app.config["DEMO_MODE"] = demo_mode or os.environ.get("AGENT_DEMO_MODE") == "1"
    if app.config["DEMO_MODE"]:
        app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.secret_key = os.environ.get(
        "FLASK_SECRET_KEY",
        "genesys-agent-demo" if demo_mode else "genesys-agent-local",
    )
    app.register_blueprint(agent_bp)
    return app
