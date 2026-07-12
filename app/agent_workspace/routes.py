from __future__ import annotations

import os

from flask import Blueprint, jsonify, render_template, request

from app.agent_workspace.demo_state import demo_state
from core.paths import PROJECT_ROOT

agent_bp = Blueprint("agent", __name__, url_prefix="/agent")


@agent_bp.route("/")
def index():
    from flask import current_app

    return render_template(
        "agent_workspace/index.html",
        demo_mode=bool(current_app.config.get("DEMO_MODE")),
    )


@agent_bp.route("/health")
def health():
    from flask import current_app

    return jsonify({
        "ok": True,
        "demo_mode": bool(current_app.config.get("DEMO_MODE")),
        "service": "agent-workspace",
    })


@agent_bp.route("/api/state")
def api_state():
    return jsonify(demo_state.snapshot())


@agent_bp.route("/api/queue", methods=["POST"])
def api_set_queue():
    payload = request.get_json(silent=True) or {}
    queue_id = payload.get("queue_id")
    if not queue_id:
        return jsonify({"error": "queue_id required"}), 400
    demo_state.set_queue(queue_id)
    return jsonify(demo_state.snapshot(jitter=False))


@agent_bp.route("/api/on-queue", methods=["POST"])
def api_on_queue():
    payload = request.get_json(silent=True) or {}
    on_queue = bool(payload.get("on_queue"))
    demo_state.toggle_queue(on_queue)
    return jsonify(demo_state.snapshot(jitter=False))


@agent_bp.route("/api/chat", methods=["POST"])
def api_chat():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    queue_id = payload.get("queue_id") or demo_state.selected_queue_id
    if not text:
        return jsonify({"error": "text required"}), 400
    message = demo_state.post_chat(queue_id, demo_state.agent_name, text)
    return jsonify({"message": message, "state": demo_state.snapshot(jitter=False)})


@agent_bp.route("/api/call/inbound", methods=["POST"])
def api_inbound():
    call = demo_state.start_inbound_call()
    if not call:
        return jsonify({"error": "Must be on queue with no active call"}), 400
    return jsonify(demo_state.snapshot(jitter=False))


@agent_bp.route("/api/call/answer", methods=["POST"])
def api_answer():
    call = demo_state.answer_call()
    if not call:
        return jsonify({"error": "No ringing call"}), 400
    return jsonify(demo_state.snapshot(jitter=False))


@agent_bp.route("/api/call/dial", methods=["POST"])
def api_dial():
    payload = request.get_json(silent=True) or {}
    phone = (payload.get("phone_number") or "").strip()
    if not phone:
        return jsonify({"error": "phone_number required"}), 400
    call = demo_state.dial_on_behalf(phone)
    if not call:
        if demo_state.current_call:
            return jsonify({"error": "End the current call before dialing"}), 400
        return jsonify({"error": "Select a queue first"}), 400
    return jsonify(demo_state.snapshot(jitter=False))


@agent_bp.route("/api/call/end", methods=["POST"])
def api_end_call():
    payload = request.get_json(silent=True) or {}
    wrapup_code_id = payload.get("wrapup_code_id") or "resolved"
    demo_state.end_call(wrapup_code_id)
    return jsonify(demo_state.snapshot(jitter=False))


@agent_bp.route("/api/stats/refresh", methods=["POST"])
def api_refresh_stats():
    demo_state.refresh_stats()
    return jsonify(demo_state.snapshot(jitter=False))


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
