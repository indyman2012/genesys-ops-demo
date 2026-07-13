from __future__ import annotations

import os
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify, redirect, render_template, request, session, url_for

from app.agent_workspace.demo_reports import aht_trend_report_html
from app.agent_workspace.demo_state import demo_state
from app.agent_workspace.live_queues import (
    LiveQueueError,
    apply_on_queue,
    apply_queue_selection,
    build_live_snapshot,
)
from app.agent_workspace.oauth import (
    OAuthConfigError,
    OAuthExchangeError,
    agent_oauth_settings,
    authorize_url,
    clear_session,
    exchange_code_for_tokens,
    fetch_current_user,
    generate_pkce,
    generate_state,
    persist_session,
    use_pkce,
)
from core.config import env_first, genesys_analytics_dashboards_url, get_login_host
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


def _is_live_pilot() -> bool:
    from flask import current_app

    return not current_app.config.get("DEMO_MODE") and _is_authenticated()


def _handle_live_error(exc: LiveQueueError):
    if "expired" in str(exc).lower():
        clear_session(session)
        return jsonify(_unauthenticated_snapshot()), 401
    return jsonify({"error": str(exc)}), 502


def _live_snapshot_or_fallback() -> dict:
    try:
        return build_live_snapshot(session)
    except LiveQueueError as exc:
        if "expired" in str(exc).lower():
            clear_session(session)
            return _unauthenticated_snapshot()
        snap = _unauthenticated_snapshot()
        snap["authenticated"] = True
        snap["auth_required"] = False
        snap["agent_name"] = session.get("genesys_user_name") or "Agent"
        snap["queues_error"] = str(exc)
        return snap


def _pilot_telephony_blocked():
    if _is_live_pilot():
        return jsonify({"error": "Telephony not available until WebRTC phase"}), 501
    return None


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
    return _live_snapshot_or_fallback()


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
        "authenticated": _is_authenticated(),
        "service": "agent-workspace",
    })


@agent_bp.route("/oauth/login")
def oauth_login():
    from flask import current_app

    if current_app.config.get("DEMO_MODE"):
        return render_template(
            "agent_workspace/oauth_error.html",
            demo_mode=True,
            message="Demo mode does not require sign-in.",
            popup=False,
        )

    try:
        verifier: str | None = None
        challenge: str | None = None
        if use_pkce():
            verifier, challenge = generate_pkce()
            session["oauth_pkce_verifier"] = verifier
        else:
            session.pop("oauth_pkce_verifier", None)
        oauth_state = generate_state()
        session["oauth_state"] = oauth_state
        return redirect(authorize_url(state=oauth_state, code_challenge=challenge))
    except OAuthConfigError as exc:
        return (
            render_template(
                "agent_workspace/oauth_error.html",
                message=str(exc),
                popup=bool(request.args.get("popup")),
            ),
            500,
        )


@agent_bp.route("/oauth/callback")
def oauth_callback():
    from flask import current_app

    if current_app.config.get("DEMO_MODE"):
        return redirect(url_for("agent.index"))

    oauth_error = request.args.get("error")
    if oauth_error:
        description = request.args.get("error_description") or oauth_error
        return (
            render_template(
                "agent_workspace/oauth_error.html",
                message=description,
                popup=True,
            ),
            400,
        )

    code = request.args.get("code")
    returned_state = request.args.get("state")
    expected_state = session.get("oauth_state")
    if not code:
        return (
            render_template(
                "agent_workspace/oauth_error.html",
                message="Missing authorization code from Genesys.",
                popup=True,
            ),
            400,
        )
    if not expected_state or returned_state != expected_state:
        return (
            render_template(
                "agent_workspace/oauth_error.html",
                message="OAuth state mismatch. Please try signing in again.",
                popup=True,
            ),
            400,
        )

    verifier = session.get("oauth_pkce_verifier")
    if use_pkce() and not verifier:
        return (
            render_template(
                "agent_workspace/oauth_error.html",
                message="OAuth session expired. Please try signing in again.",
                popup=True,
            ),
            400,
        )

    try:
        tokens = exchange_code_for_tokens(
            code=code,
            code_verifier=verifier or "",
        )
        user = fetch_current_user(tokens.access_token)
        persist_session(session, tokens=tokens, user=user)
        return render_template(
            "agent_workspace/oauth_callback.html",
            user_name=user.display_name,
        )
    except OAuthExchangeError as exc:
        return (
            render_template(
                "agent_workspace/oauth_error.html",
                message=str(exc),
                popup=True,
            ),
            500,
        )


@agent_bp.route("/oauth/config")
def oauth_config():
    """Read-only OAuth wiring check (no secrets) for pilot troubleshooting."""
    from flask import current_app

    if current_app.config.get("DEMO_MODE"):
        return jsonify({"demo_mode": True, "message": "Demo mode — OAuth not used."})
    try:
        settings = agent_oauth_settings()
        return jsonify({
            "login_host": get_login_host(),
            "redirect_uri": settings.redirect_uri,
            "client_id": settings.client_id,
            "pkce": use_pkce(),
            "checklist": [
                "Genesys Admin → Integrations → OAuth → open this client_id",
                "Grant type: Authorization Code (CODE)",
                "Registered redirect URI must match redirect_uri exactly (https, path, no typos)",
                "Client state: Active",
                "Org region: usw2 (login.usw2.pure.cloud)",
            ],
        })
    except OAuthConfigError as exc:
        return jsonify({"error": str(exc)}), 500


@agent_bp.route("/oauth/logout", methods=["GET", "POST"])
def oauth_logout():
    clear_session(session)
    return redirect(url_for("agent.index"))


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
    if _is_live_pilot():
        try:
            return jsonify(apply_queue_selection(session, queue_id=queue_id))
        except LiveQueueError as exc:
            return _handle_live_error(exc)
    demo_state.set_queue(queue_id)
    return jsonify(_public_snapshot(jitter=False))


@agent_bp.route("/api/on-queue", methods=["POST"])
def api_on_queue():
    blocked = _require_auth()
    if blocked:
        return blocked
    payload = request.get_json(silent=True) or {}
    on_queue = bool(payload.get("on_queue"))
    if _is_live_pilot():
        try:
            return jsonify(apply_on_queue(session, joined=on_queue))
        except LiveQueueError as exc:
            return _handle_live_error(exc)
    demo_state.toggle_queue(on_queue)
    return jsonify(_public_snapshot(jitter=False))


@agent_bp.route("/api/chat", methods=["POST"])
def api_chat():
    blocked = _require_auth()
    if blocked:
        return blocked
    telephony = _pilot_telephony_blocked()
    if telephony:
        return telephony
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
    telephony = _pilot_telephony_blocked()
    if telephony:
        return telephony
    call = demo_state.start_inbound_call()
    if not call:
        return jsonify({"error": "Must be on queue with no active call"}), 400
    return jsonify(_public_snapshot(jitter=False))


@agent_bp.route("/api/call/answer", methods=["POST"])
def api_answer():
    blocked = _require_auth()
    if blocked:
        return blocked
    telephony = _pilot_telephony_blocked()
    if telephony:
        return telephony
    call = demo_state.answer_call()
    if not call:
        return jsonify({"error": "No ringing call"}), 400
    return jsonify(_public_snapshot(jitter=False))


@agent_bp.route("/api/call/dial", methods=["POST"])
def api_dial():
    blocked = _require_auth()
    if blocked:
        return blocked
    telephony = _pilot_telephony_blocked()
    if telephony:
        return telephony
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
    telephony = _pilot_telephony_blocked()
    if telephony:
        return telephony
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
    if _is_live_pilot():
        if payload.get("mic_id") is not None:
            session["agent_media_mic_id"] = payload.get("mic_id") or "default"
        if payload.get("mic_label") is not None:
            session["agent_media_mic"] = payload.get("mic_label") or "System default audio"
        if payload.get("speaker_id") is not None:
            session["agent_media_speaker_id"] = payload.get("speaker_id") or "default"
        if payload.get("speaker_label") is not None:
            session["agent_media_speaker"] = payload.get("speaker_label") or "System default audio"
        return jsonify(_public_snapshot(jitter=False))
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
    if _is_live_pilot():
        return jsonify(_public_snapshot(jitter=False))
    demo_state.refresh_stats()
    return jsonify(_public_snapshot(jitter=False))


def create_app(*, demo_mode: bool = False):
    from datetime import timedelta

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
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = not app.config["DEMO_MODE"]
    app.register_blueprint(agent_bp)
    return app
